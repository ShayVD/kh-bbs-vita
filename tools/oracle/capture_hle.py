"""Capture PPSSPP's firmware calls as a khbbs-format trace.

Every import stub gets a log breakpoint that writes the call (`S` line) and
every return site one that writes the return (`R` line) to PPSSPP's --log
file; parse() pairs them (docs/oracle.md, "Firmware-call source").

Each record has runtime/trace.c's fields plus `thread`. Records come in the
order the calls returned, the order khbbs's trace hook sees them. Calls still
open at the end come last, with `ret` and `ret_hi` null.

Usage:
  PYTHONPATH=tools python3 -m oracle.capture_hle --iso ISO --elf game/EBOOT.dec.ELF \\
      -n 200 -o build/oracle/ppsspp.jsonl
"""
import argparse, collections, json, pathlib, re, struct, sys, time

from . import imports
from .ppsspp import Ppsspp, PpssppError

S_FORMAT = "S {pc} {[k0+0xc0]} {ra} {a0} {a1} {a2} {a3} {t0} {t1} {t2} {t3}"
R_FORMAT = "R {pc} {[k0+0xc0]} {v0} {v1}"
BKP = re.compile(r"BKP PC=[0-9a-f]{8}: (?:S((?: [0-9a-f]{8}){11})|R((?: [0-9a-f]{8}){4}))\s*$")
LOOKS_BKP = re.compile(r"BKP PC=.*: [SR] ")
# The uid (decimal) and the name; the five arguments after the name anchor
# it, so a name may contain ", ".
CREATE = re.compile(r"\b(\d+)=sceKernelCreateThread\((.*), [0-9a-f]{8}, [0-9a-f]{8}, \d+, "
                    r"[0-9a-f]{8}, [0-9a-f]{8}\)\s*$")
# PPSSPP fills a0..t9 with this after a syscall: an unused argument slot.
FILL = 0xDEADBEEF
# Skipped lines, counted in parse()'s `stats`; the first few are printed.
PROBLEMS = ("malformed", "not_stub", "unmatched")
REPORT_FIRST = 5


class CaptureError(Exception):
    pass


def hex32(v):
    return None if v is None else "0x%08X" % v


def parse(lines, stubs, thread_names, stats=None):
    """Yield trace records from PPSSPP log lines. `stubs` is
    {stub_addr: (nid, name)}, `thread_names` {uid: name} (from
    hle.thread.list); sceKernelCreateThread info lines add to it. Other lines
    are skipped. Breakpoint lines that do not parse, `S` lines off a stub and
    `R` lines that close no open call are skipped and counted in `stats` (a
    Counter, keys PROBLEMS); the first REPORT_FIRST go to stderr.
    stats["open"] is the number of calls still open at the end."""
    stats = collections.Counter() if stats is None else stats
    names = dict(thread_names)
    open_calls = {}                  # uid -> stack of open calls
    order = 0                        # S order, for the calls left open
    seq = 0

    def problem(kind, msg):
        stats[kind] += 1
        if sum(stats[k] for k in PROBLEMS) <= REPORT_FIRST:
            print(f"capture_hle: {msg}, skipped", file=sys.stderr)

    def record(call, ret=None, ret_hi=None):
        nonlocal seq
        nid, name = stubs[call["stub"]]
        rec = {"seq": seq, "nid": hex32(nid), "name": name, "ra": hex32(call["ra"]),
               "args": [None if a == FILL else hex32(a) for a in call["args"]],
               "ret": hex32(ret), "ret_hi": hex32(ret_hi),
               "thread": names.get(call["uid"], "uid:0x%X" % call["uid"])}
        seq += 1
        return rec

    for line in lines:
        line = line.rstrip("\r\n")
        m = BKP.search(line)
        if not m:
            if LOOKS_BKP.search(line):
                problem("malformed", f"malformed breakpoint line {line!r}")
            elif c := CREATE.search(line):
                names[int(c.group(1))] = c.group(2)
            continue
        if m.group(1):
            pc, uid, ra, *args = (int(x, 16) for x in m.group(1).split())
            if pc not in stubs:
                problem("not_stub", f"S at {pc:08x} is not an import stub")
                continue
            open_calls.setdefault(uid, []).append(
                {"stub": pc, "uid": uid, "ra": ra, "args": args, "order": order})
            order += 1
        else:
            pc, uid, v0, v1 = (int(x, 16) for x in m.group(2).split())
            stack = open_calls.get(uid, [])
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["ra"] == pc:
                    yield record(stack.pop(i), v0, v1)
                    break
            else:
                problem("unmatched", f"R at {pc:08x} on thread 0x{uid:x} closes no open call")
    left = sorted((c for s in open_calls.values() for c in s), key=lambda c: c["order"])
    stats["open"] = len(left)
    for call in left:
        yield record(call)


class Follow:
    """The complete lines of a file that is still being written, as they
    arrive (a last line without a newline is never yielded). Iteration ends
    once stop() is true (checked before every line) or after `timeout` s
    (then `timed_out` is set). If `proc` has exited and nothing is left to
    read, it raises CaptureError."""

    def __init__(self, f, stop, proc=None, timeout=120.0, poll=0.05):
        self.f, self.stop, self.proc, self.timeout, self.poll = f, stop, proc, timeout, poll
        self.timed_out = False

    def __iter__(self):
        deadline = time.monotonic() + self.timeout
        partial = ""
        while not self.stop():
            chunk = self.f.read()
            if chunk:
                *done, partial = (partial + chunk).split("\n")
                for line in done:
                    yield line
                    if self.stop():
                        return
            elif self.proc is not None and self.proc.poll() is not None:
                raise CaptureError(f"PPSSPP exited with {self.proc.returncode} while tracing")
            else:
                time.sleep(self.poll)
            if time.monotonic() > deadline:
                self.timed_out = True
                return


def capture(p, n, out_path, stubs, sites, timeout=120.0, poll=0.05):
    """With PPSSPP `p` stopped at the entry: trace until `n` calls have
    returned (or `timeout` s), pause, and write the first `n` records to
    `out_path`. `stubs` and `sites` come from imports.stubs() and
    imports.return_sites(). Returns the number of returned calls written
    (fewer than `n` after a timeout; then open calls fill the file up).

    The breakpoints stay installed afterwards; p.close() stops PPSSPP."""
    thread_names = {t["id"]: t["name"] for t in p.request("hle.thread.list")["threads"]}
    for addr in stubs:
        p.add_breakpoint(addr, enabled=False, log=True, log_format=S_FORMAT)
    for addr in sites:
        p.add_breakpoint(addr, enabled=False, log=True, condition="pc == ra", log_format=R_FORMAT)
    stats = collections.Counter()
    out, completed = [], 0
    p.resume()
    try:
        with open(p.log_path, encoding="utf-8", errors="replace") as f:
            lines = Follow(f, lambda: completed >= n, p.proc, timeout, poll)
            for rec in parse(lines, stubs, thread_names, stats):
                if len(out) < n:
                    out.append(rec)
                if rec["ret"] is not None:
                    completed += 1
    finally:
        if p.proc is None or p.proc.poll() is None:   # a dead PPSSPP cannot pause
            p.pause()
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    returned = sum(1 for r in out if r["ret"] is not None)
    threads = sorted({r["thread"] for r in out})
    print(f"capture_hle: wrote {len(out)} records ({len(out) - returned} of them open) to {out_path}; "
          f"{stats['open']} calls were open when tracing stopped; "
          f"threads in the output: {', '.join(threads)}; skipped lines: {stats['unmatched']} "
          f"unmatched R, {stats['not_stub']} S off a stub, {stats['malformed']} malformed")
    if lines.timed_out:
        print(f"capture_hle: timed out after {timeout} s with {completed} of {n} calls returned",
              file=sys.stderr)
    return returned


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("-n", type=int, default=200, help="number of records")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--home", default="build/oracle/ppsspp-home")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds of tracing")
    a = ap.parse_args()
    try:
        module = imports.load(a.elf)
        stubs = imports.stubs(module)
        sites = imports.return_sites(module, stubs)
    except (OSError, ValueError, KeyError, struct.error) as e:
        sys.exit(f"capture_hle: cannot read the import table of {a.elf}: {e}")
    try:
        p = Ppsspp.launch(a.iso, a.home)
        try:
            written = capture(p, a.n, a.out, stubs, sites, a.timeout)
        finally:
            p.close()
    except (PpssppError, CaptureError) as e:
        sys.exit(f"capture_hle: {e}")
    sys.exit(0 if written >= a.n else 1)


if __name__ == "__main__":
    main()
