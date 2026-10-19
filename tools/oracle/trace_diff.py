#!/usr/bin/env python3
"""Report the first firmware call where two traces disagree.

Traces are JSON lines in runtime/trace.c's format; capture_hle.py adds
`thread`. Records are paired in order: per thread when every record of both
traces has a `thread`, else as one stream each. Compared are `nid` and the
registers tools/oracle/hle_sigs.txt lists for that NID (with no line there:
every argument and `ret`, but not `ret_hi`); values that are null on either
side are skipped. `seq`, `ra` and `name` are only shown (--compare-ra compares
`ra`). Pairs are walked in A's order, and the walk stops at the first
difference.

--map-uids learns B-to-A value pairs from the returns of calls whose return
type is `u` in hle_sigs.txt (both non-zero, not error codes) and accepts a B
value that maps to A's value from then on. A return that contradicts a pair
already learned is a divergence. A call of type `d` that returns 0 on both
sides drops the pair holding its first argument (the UID or fd it freed).

-n N cuts both traces to their first N records. A stream that is shorter on
one side is a divergence only if that whole trace had fewer than N records
(it really ended); otherwise it is beyond the compared window.

Exit 0 if every compared record matches, 1 at a divergence, 2 on bad input.

Usage:
  tools/oracle/trace_diff.py A.jsonl B.jsonl [--map-uids] [-n N]
      [--ignore-arg NAME_OR_NID:INDEX|ret|ret_hi ...] [--compare-ra]
      [--sigs hle_sigs.txt]
"""
import argparse, collections, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from oracle import sigs as hle_sigs  # noqa: E402

CONTEXT = 3
ERROR = 0x80000000
HEX = re.compile(r"0x[0-9A-Fa-f]{8}")          # how runtime/trace.c and capture_hle write values

# Where the walk stopped: the stream key, the index in the stream, the A and B
# indexes into the traces (None for a missing record) and the field names.
Divergence = collections.namedtuple("Divergence", "stream index ia ib fields")


class BadInput(Exception):
    pass


def val(x):
    return None if x is None else int(x, 16)


def arg(r, i):
    return val(r["args"][i]) if i < len(r["args"]) else None


def check(r):
    """Raise ValueError unless `r` looks like a trace record."""
    if not isinstance(r, dict) or not isinstance(r.get("nid"), str) or not isinstance(r.get("args"), list):
        raise ValueError("not a trace record (needs a string nid and an args list)")
    if not isinstance(r.get("thread"), (str, type(None))):
        raise ValueError("thread is neither a string nor null")
    for v in (r["nid"], r.get("ret"), r.get("ret_hi"), r.get("ra"), *r["args"]):
        if v is not None and not (isinstance(v, str) and HEX.fullmatch(v)):
            raise ValueError(f"{v!r} is not null or a 0x-prefixed 8-digit hex string")


def load(path, n=None):
    """The first `n` records of the trace at `path` (all if `n` is None). A
    last line that has no newline and does not parse (a trace cut off while
    it was written) is dropped with a warning."""
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if n is not None and len(recs) >= n:
                    break
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    check(r)
                except ValueError as e:
                    if not line.endswith("\n"):
                        print(f"trace_diff: warning: {path}:{lineno}: dropped the unfinished last line ({e})",
                              file=sys.stderr)
                        break
                    raise BadInput(f"{path}:{lineno}: {e}") from None
                recs.append(r)
    except (OSError, UnicodeDecodeError) as e:
        raise BadInput(f"cannot read {path}: {e}") from None
    return recs


def streams(recs, per_thread):
    """{stream key: [index into recs]}; the key is the thread or None."""
    out = {}
    for i, r in enumerate(recs):
        out.setdefault(r["thread"] if per_thread else None, []).append(i)
    return out


class Differ:
    def __init__(self, sigs, ignore=(), map_uids=False, compare_ra=False):
        self.sigs, self.ignore, self.map_uids, self.compare_ra = sigs, set(ignore), map_uids, compare_ra
        self.uids, self.mapped_from = {}, {}       # B value -> A value, and back
        self.learned = 0
        self.note = None

    def same(self, x, y):
        return x == y or self.uids.get(y) == x

    def learn(self, x, y):
        """Add the pair B `y` -> A `x` from a `u` return; False (and a note)
        if it is no UID pair or contradicts a pair already held."""
        if not (x and y) or (x | y) & ERROR:
            self.note = f"uid map: A 0x{x:08X} / B 0x{y:08X} is no UID pair (zero or error code)"
        elif y in self.uids:
            self.note = f"uid map: B 0x{y:08X} already maps to A 0x{self.uids[y]:08X}"
        elif x in self.mapped_from:
            self.note = f"uid map: A 0x{x:08X} is already mapped from B 0x{self.mapped_from[x]:08X}"
        else:
            self.uids[y], self.mapped_from[x] = x, y
            self.learned += 1
            return True
        return False

    def forget(self, x, y):
        """Drop the pairs holding A value `x` or B value `y` (a freed UID)."""
        # Normally both are one pair. If they are two, `y` does not map to `x`,
        # so args[0] already differed and this record is the divergence:
        # dropping both cannot hide anything.
        if y in self.uids:
            del self.mapped_from[self.uids.pop(y)]
        if x in self.mapped_from:
            del self.uids[self.mapped_from.pop(x)]

    def compare(self, ra, rb):
        """The names of the fields where records `ra` (A) and `rb` (B) differ.
        Learns and forgets UID pairs as a side effect (with --map-uids)."""
        self.note = None
        nid = val(ra["nid"])
        if nid != val(rb["nid"]):
            return ["nid"]
        sig = self.sigs.get(nid)
        kind = sig.ret if sig else "i"
        fields = [f"args[{i}]" for i in (sig.args if sig else range(8))
                  if (nid, i) not in self.ignore and None not in (arg(ra, i), arg(rb, i))
                  and not self.same(arg(ra, i), arg(rb, i))]
        x, y = val(ra.get("ret")), val(rb.get("ret"))
        ret_differs = (kind != "v" and (nid, "ret") not in self.ignore and None not in (x, y)
                       and not self.same(x, y))
        if ret_differs and kind == "u" and self.map_uids:
            ret_differs = not self.learn(x, y)
        if ret_differs:
            fields.append("ret")
        if kind == "d" and self.map_uids and x == 0 and y == 0:
            self.forget(arg(ra, 0), arg(rb, 0))
        x, y = val(ra.get("ret_hi")), val(rb.get("ret_hi"))
        if kind == "l" and (nid, "ret_hi") not in self.ignore and None not in (x, y) and x != y:
            fields.append("ret_hi")
        x, y = val(ra.get("ra")), val(rb.get("ra"))
        if self.compare_ra and None not in (x, y) and x != y:
            fields.append("ra")
        return fields


def walk(a, b, n, differ, per_thread):
    """Compare A and B; returns (matched, compared, notes, Divergence or None)."""
    sa, sb = streams(a, per_thread), streams(b, per_thread)
    ended = {"A": n is None or len(a) < n, "B": n is None or len(b) < n}
    matched = compared = 0
    notes, done, pos = [], set(), {}
    for ia, r in enumerate(a):
        k = r["thread"] if per_thread else None
        j = pos[k] = pos.get(k, -1) + 1
        if k in done:
            continue
        lb = sb.get(k, [])
        if j >= len(lb):
            if ended["B"]:
                return matched, compared, notes, Divergence(k, j, ia, None, ["missing"])
            notes.append(stream_name(k, "B") + f" ends at record {j}, beyond the compared window")
            done.add(k)
            continue
        compared += 1
        fields = differ.compare(r, b[lb[j]])
        if fields:
            return matched, compared, notes, Divergence(k, j, ia, lb[j], fields)
        matched += 1
    extra = sorted((lb[len(sa.get(k, []))], k) for k, lb in sb.items() if len(lb) > len(sa.get(k, [])))
    for ib, k in extra:
        j = len(sa.get(k, []))
        if ended["A"]:
            return matched, compared, notes, Divergence(k, j, None, ib, ["missing"])
        notes.append(stream_name(k, "A") + f" ends at record {j}, beyond the compared window")
    return matched, compared, notes, None


def stream_name(k, side):
    return f"{side}'s trace" if k is None else f"{side}'s thread {k}"


def display_name(r, sigs):
    sig = sigs.get(val(r["nid"]))
    return r.get("name") or (sig.name if sig and sig.name else r["nid"])


def brief(r, sigs):
    """One line for a context record: the signature's arguments (every slot
    up to the last one it uses; `_` for a skipped one), or all of them."""
    sig = sigs.get(val(r["nid"]))
    if sig:
        slots = range(max(sig.args) + 1) if sig.args else range(0)
    else:
        slots = range(len(r["args"]))
    args = ["_" if sig and i not in sig.args else
            "null" if i >= len(r["args"]) or r["args"][i] is None else r["args"][i] for i in slots]
    ret = "" if sig and sig.ret == "v" else f" = {r.get('ret') or 'null'}"
    if sig and sig.ret == "l":
        ret += f":{r.get('ret_hi') or 'null'}"
    return f"seq {r.get('seq')}  {display_name(r, sigs)}({', '.join(args)}){ret}  ra {r.get('ra')}"


def report(paths, a, b, per_thread, d, differ):
    seq = lambda recs, i: "-" if i is None else recs[i].get("seq")  # noqa: E731
    where = "" if d.stream is None else f" of thread {d.stream}"
    print(f"first divergence at record {d.index}{where} (A seq {seq(a, d.ia)}, B seq {seq(b, d.ib)})")
    print("differing: " + ", ".join(d.fields))
    if differ.note:
        print(differ.note)
    if d.fields not in (["nid"], ["missing"]) and val(a[d.ia]["nid"]) not in differ.sigs:
        print(f"hint: no signature for {display_name(a[d.ia], differ.sigs)} in hle_sigs.txt")
    for side, path, recs, i in (("A", paths[0], a, d.ia), ("B", paths[1], b, d.ib)):
        print(f"{side}: {path}")
        stream = streams(recs, per_thread).get(d.stream, [])
        for c in stream[max(0, d.index - CONTEXT):d.index]:
            print("    " + brief(recs[c], differ.sigs))
        if i is None:
            print("  > (none: this trace ended)")
        else:
            print("  > " + json.dumps({**recs[i], "name": display_name(recs[i], differ.sigs)}))


def ignore_spec(text):
    key, _, field = text.rpartition(":")
    try:
        nid = hle_sigs.parse_nid(key) if key else None
        idx = field if field in ("ret", "ret_hi") else int(field)
    except ValueError:
        nid = None
    if nid is None or not (isinstance(idx, str) or 0 <= idx < 8):
        raise argparse.ArgumentTypeError(f"expected NAME_OR_NID:INDEX, :ret or :ret_hi, got {text!r}")
    return nid, idx


def positive(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("-n", type=positive, help="compare only the first N records of each trace")
    ap.add_argument("--map-uids", action="store_true")
    ap.add_argument("--ignore-arg", type=ignore_spec, action="append", default=[])
    ap.add_argument("--compare-ra", action="store_true")
    ap.add_argument("--sigs", default=hle_sigs.SIGS, help="signature table")
    o = ap.parse_args(argv)
    try:
        try:
            sigs = hle_sigs.load(o.sigs)
        except (OSError, ValueError) as e:
            raise BadInput(f"signature table: {e}") from None
        a, b = load(o.a, o.n), load(o.b, o.n)
        if not a and not b:
            raise BadInput("both traces are empty")
    except BadInput as e:
        print(f"trace_diff: {e}", file=sys.stderr)
        return 2
    seen = sigs.keys() | {val(r["nid"]) for r in a + b}
    for nid in sorted({nid for nid, _ in o.ignore_arg} - seen):
        print(f"trace_diff: warning: --ignore-arg NID 0x{nid:08X} is in neither hle_sigs.txt nor "
              "either trace", file=sys.stderr)
    per_thread = all(r.get("thread") is not None for r in a + b)
    differ = Differ(sigs, o.ignore_arg, o.map_uids, o.compare_ra)
    matched, compared, notes, divergence = walk(a, b, o.n, differ, per_thread)
    mapped = f"; {differ.learned} uid pairs learned" if o.map_uids else ""
    print(f"{matched} of {compared} records match"
          f" ({'per-thread streams' if per_thread else 'one stream each'}{mapped})")
    for note in notes:
        print(note)
    if divergence is None:
        return 0
    report((o.a, o.b), a, b, per_thread, divergence, differ)
    return 1


if __name__ == "__main__":
    sys.exit(main())
