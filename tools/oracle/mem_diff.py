#!/usr/bin/env python3
"""Compare two guest RAM snapshots and report where they differ.

A snapshot is a raw dump plus a sidecar DUMP.json ({"base", "size", "event",
"side", ...}), written by `khbbs --snapshot` or tools/oracle/snapshot.py. Both
sidecars must agree on base and size.

Differing bytes with at most GAP equal bytes between them form one range.
Ranges never cross a region boundary: an allocated section of --elf (.text,
.data, .bss, ...), "image (no section)" for the gaps between sections inside
the image (from the lowest section start to the highest section end), and
outside the image "kernel partition" (below 0x08800000) or "user partition".
Bytes inside an --ignore range [LO, HI) are not compared, and no range
bridges one.
At most --limit ranges are printed (0: all), then the differing bytes per
region.

Exit 0 if nothing differs outside --ignore, 1 if something does, 2 on bad
input.

Usage:
  tools/oracle/mem_diff.py A.bin B.bin [--elf game/EBOOT.dec.ELF]
      [--ignore 0x09F3D7D0-0x0A000000 ...] [--gap 16] [--limit 100]
"""
import argparse, json, pathlib, re, struct, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import import_report  # noqa: E402

USER = 0x08800000          # start of the user partition
BLOCK = 4096               # compared with == first; only differing blocks are scanned
SHF_ALLOC = 0x2
SHOW = 16                  # bytes of each side shown per range
NONZERO = re.compile(rb"[^\x00]+")


class BadInput(Exception):
    pass


def load(path):
    """(bytes, sidecar dict with int base and size)."""
    side = pathlib.Path(str(path) + ".json")
    try:
        data = pathlib.Path(path).read_bytes()
        meta = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadInput(f"{path}: {e}") from None
    try:
        base = meta["base"]
        if not isinstance(base, str) or not re.fullmatch(r"0x[0-9A-Fa-f]{1,8}", base):
            raise ValueError(f"bad base {base!r}")
        meta["base"] = int(base, 16)
        if not isinstance(meta["size"], int) or meta["size"] < 0:
            raise ValueError(f"bad size {meta['size']!r}")
    except (KeyError, TypeError, ValueError) as e:
        raise BadInput(f"{side}: {e}") from None
    if len(data) != meta["size"]:
        raise BadInput(f"{path}: {len(data)} bytes, its sidecar says {meta['size']}")
    return data, meta


def regions(base, size, elf=None):
    """[(lo, hi, label)] covering [base, base + size) in order."""
    secs = []
    if elf is not None:
        try:
            _, table = import_report.load_elf(elf)
        except (OSError, ValueError, struct.error, IndexError, KeyError) as e:
            raise BadInput(f"--elf: malformed or unreadable ELF: {e!r}") from None
        secs = sorted((s["addr"], s["addr"] + s["size"], name) for name, s in table.items()
                      if s["flags"] & SHF_ALLOC and s["size"])
    out, end = [], base + size
    img_lo = min((lo for lo, _, _ in secs), default=0)
    img_hi = max((hi for _, hi, _ in secs), default=0)

    def fill(lo, hi):                       # outside every section
        cuts = sorted({lo, hi} | {c for c in (USER, img_lo, img_hi) if lo < c < hi})
        for a, b in zip(cuts, cuts[1:]):
            if img_lo <= a < img_hi:
                label = "image (no section)"
            else:
                label = "kernel partition" if a < USER else "user partition"
            if out and out[-1][2] == label and out[-1][1] == a:
                out[-1] = (out[-1][0], b, label)
            else:
                out.append((a, b, label))

    at = base
    for lo, hi, name in secs:
        lo, hi = max(lo, at), min(hi, end)
        if lo >= hi:
            continue
        fill(at, lo)
        out.append((lo, hi, name))
        at = hi
    fill(at, end)
    return out


def ignore_spec(text):
    m = re.fullmatch(r"(0x[0-9A-Fa-f]+)-(0x[0-9A-Fa-f]+)", text)
    if not m or int(m.group(1), 16) >= int(m.group(2), 16):
        raise argparse.ArgumentTypeError(f"expected LO-HI in hex with LO < HI, got {text!r}")
    return int(m.group(1), 16), int(m.group(2), 16)


def runs(a, b, base):
    """Yield (lo, hi) address runs where every byte differs, in order."""
    ma, mb = memoryview(a), memoryview(b)
    for off in range(0, len(a), BLOCK):
        x, y = ma[off:off + BLOCK], mb[off:off + BLOCK]
        if x == y:
            continue
        n = len(x)
        xor = (int.from_bytes(x, "little") ^ int.from_bytes(y, "little")).to_bytes(n, "little")
        for m in NONZERO.finditer(xor):
            yield base + off + m.start(), base + off + m.end()


def split(pieces, cuts):
    """Cut sorted (lo, hi) pieces at the sorted `cuts` [(lo, hi, label)]:
    yield (lo, hi, index into cuts) for the parts inside a cut."""
    i = 0
    for lo, hi in pieces:
        while lo < hi:
            while i < len(cuts) and cuts[i][1] <= lo:
                i += 1
            if i == len(cuts):
                return
            c_lo, c_hi, _ = cuts[i]
            if hi <= c_lo:
                break
            lo = max(lo, c_lo)
            yield lo, min(hi, c_hi), i
            lo = min(hi, c_hi)


def subtract(pieces, ignore, dropped):
    """Yield the parts of sorted pieces outside every ignore range; add the
    number of bytes taken away to dropped[0]."""
    holes = merge_intervals(ignore)
    for lo, hi in pieces:
        parts = [(lo, hi)]
        for h_lo, h_hi in holes:
            nxt = []
            for p_lo, p_hi in parts:
                if h_hi <= p_lo or p_hi <= h_lo:
                    nxt.append((p_lo, p_hi))
                    continue
                if p_lo < h_lo:
                    nxt.append((p_lo, h_lo))
                if h_hi < p_hi:
                    nxt.append((h_hi, p_hi))
            parts = nxt
        dropped[0] += hi - lo - sum(p_hi - p_lo for p_lo, p_hi in parts)
        yield from parts


def merge_intervals(iv):
    out = []
    for lo, hi in sorted(iv):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def compare(a, b, base, regs, ignore=(), gap=16):
    """([(lo, hi, differing bytes, region index)], ignored differing bytes).
    The regions are also cut at the --ignore boundaries, so no range bridges
    an ignored hole."""
    edges = sorted({e for h in merge_intervals(ignore) for e in h})
    cuts = []                                # (lo, hi, region index)
    for ri, (lo, hi, _) in enumerate(regs):
        points = [lo] + [e for e in edges if lo < e < hi] + [hi]
        cuts += [(x, y, ri) for x, y in zip(points, points[1:])]
    ranges, last_cut, dropped = [], None, [0]
    for lo, hi, ci in split(subtract(runs(a, b, base), ignore, dropped), cuts):
        last = ranges[-1] if ranges else None
        if last and ci == last_cut and lo - last[1] <= gap:
            ranges[-1] = (last[0], hi, last[2] + hi - lo, last[3])
        else:
            ranges.append((lo, hi, hi - lo, cuts[ci][2]))
        last_cut = ci
    return ranges, dropped[0]


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def describe(path, meta):
    keys = ("side", "event", "nid", "ra")
    return f"{path} (" + ", ".join(f"{k} {meta[k]}" for k in keys if k in meta) + ")"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--elf", help="label ranges with this module's sections")
    ap.add_argument("--ignore", type=ignore_spec, action="append", default=[], metavar="LO-HI")
    ap.add_argument("--gap", type=int, default=16, help="equal bytes a range may bridge (default 16)")
    ap.add_argument("--limit", type=int, default=100, help="ranges to print (0: all; default 100)")
    o = ap.parse_args(argv)
    try:
        if o.gap < 0 or o.limit < 0:
            raise BadInput("--gap and --limit must not be negative")
        a, ma = load(o.a)
        b, mb = load(o.b)
        if (ma["base"], ma["size"]) != (mb["base"], mb["size"]):
            raise BadInput(f"the dumps cover different ranges: 0x{ma['base']:08X}+0x{ma['size']:X} "
                           f"and 0x{mb['base']:08X}+0x{mb['size']:X}")
        regs = regions(ma["base"], ma["size"], o.elf)
    except BadInput as e:
        print(f"mem_diff: {e}", file=sys.stderr)
        return 2
    if ma.get("event") != mb.get("event"):
        print(f"mem_diff: warning: the snapshots were taken at different events "
              f"({ma.get('event')} and {mb.get('event')})", file=sys.stderr)
    print(f"A: {describe(o.a, ma)}")
    print(f"B: {describe(o.b, mb)}")
    ranges, ignored = compare(a, b, ma["base"], regs, o.ignore, o.gap)
    if not ranges and not ignored:
        print(f"the dumps are identical ({ma['size']} bytes from 0x{ma['base']:08X})")
        return 0
    total = sum(r[2] for r in ranges)
    print(f"{plural(total, 'byte')} differ in {plural(len(ranges), 'range')}"
          f" ({ignored} more inside --ignore)")
    shown = ranges if o.limit == 0 else ranges[:o.limit]
    for lo, hi, n, ri in shown:
        print(f"  0x{lo:08X}-0x{hi:08X}  {plural(hi - lo, 'byte'):>14}, {n} differ  {regs[ri][2]}")
        off = lo - ma["base"]
        print(f"      A: {a[off:off + min(SHOW, hi - lo)].hex(' ')}")
        print(f"      B: {b[off:off + min(SHOW, hi - lo)].hex(' ')}")
    if len(ranges) > len(shown):
        print(f"  ... {plural(len(ranges) - len(shown), 'more range')} (--limit 0 prints all)")
    per = {}                                 # label -> [bytes, ranges], in address order
    for _, _, n, ri in ranges:
        counts = per.setdefault(regs[ri][2], [0, 0])
        counts[0] += n
        counts[1] += 1
    if per:
        print("by section:")
        width = max(map(len, per))
        for label, (n, k) in per.items():
            print(f"  {label:<{width}}  {plural(n, 'byte')} in {plural(k, 'range')}")
    return 1 if ranges else 0


if __name__ == "__main__":
    sys.exit(main())
