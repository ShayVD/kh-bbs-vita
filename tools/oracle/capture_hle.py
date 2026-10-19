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
import argparse, json, pathlib, re, sys, time

from . import imports
from .ppsspp import Ppsspp, PpssppError

S_FORMAT = "S {pc} {[k0+0xc0]} {ra} {a0} {a1} {a2} {a3} {t0} {t1} {t2} {t3}"
R_FORMAT = "R {pc} {[k0+0xc0]} {v0} {v1}"
BKP = re.compile(r"BKP PC=[0-9a-f]{8}: ([SR]) (.+)$")
CREATE = re.compile(r"^.*\b(\d+)=sceKernelCreateThread\((.*?), ")
# PPSSPP fills a0..t9 with this after a syscall: an unused argument slot.
FILL = 0xDEADBEEF


class CaptureError(Exception):
    pass


def hex32(v):
    return None if v is None else "0x%08X" % v


def parse(lines, stubs, thread_names):
    """Yield trace records from PPSSPP log lines. `stubs` is
    {stub_addr: (nid, name)}, `thread_names` {uid: name} (from
    hle.thread.list); sceKernelCreateThread info lines add to it. Lines that
    are neither breakpoint lines nor thread creations are skipped. An `R`
    that closes no open call is reported on stderr and skipped."""
    names = dict(thread_names)
    open_calls = {}                  # uid -> stack of open calls
    order = 0                        # S order, for the calls left open
    seq = 0

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
            m = CREATE.match(line)
            if m:
                names[int(m.group(1))] = m.group(2)
            continue
        f = [int(x, 16) for x in m.group(2).split(" ")]
        if m.group(1) == "S":
            pc, uid, ra, *args = f
            if pc not in stubs:
                print(f"capture_hle: S at {pc:08x} is not an import stub, skipped", file=sys.stderr)
                continue
            open_calls.setdefault(uid, []).append(
                {"stub": pc, "uid": uid, "ra": ra, "args": args, "order": order})
            order += 1
        else:
            pc, uid, v0, v1 = f
            stack = open_calls.get(uid, [])
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["ra"] == pc:
                    yield record(stack.pop(i), v0, v1)
                    break
            else:
                print(f"capture_hle: R at {pc:08x} on thread 0x{uid:x} closes no open call, skipped",
                      file=sys.stderr)
    for call in sorted((c for s in open_calls.values() for c in s), key=lambda c: c["order"]):
        yield record(call)


def capture(p, n, out_path, elf, timeout=120.0, poll=0.05):
    """With PPSSPP `p` stopped at the entry: trace until at least `n` calls
    have returned, pause, and write the first `n` records to `out_path`.
    Returns the number written (fewer than `n` after a timeout)."""
    stubs = imports.stubs(elf)
    sites = imports.return_sites(elf, stubs)
    thread_names = {t["id"]: t["name"] for t in p.request("hle.thread.list")["threads"]}
    for addr in stubs:
        p.add_breakpoint(addr, enabled=False, log=True, log_format=S_FORMAT)
    for addr in sites:
        p.add_breakpoint(addr, enabled=False, log=True, condition="pc == ra", log_format=R_FORMAT)
    p.resume()
    returns, timed_out, partial = 0, False, ""
    deadline = time.monotonic() + timeout
    with open(p.log_path, errors="replace") as f:
        while returns < n:
            *done, partial = (partial + f.read()).split("\n")
            returns += sum(1 for line in done if BKP.search(line) and ": R " in line)
            if returns >= n:
                break
            if p.proc and p.proc.poll() is not None:
                raise CaptureError(f"PPSSPP exited with {p.proc.returncode} after {returns} returns")
            if time.monotonic() > deadline:
                timed_out = True
                break
            time.sleep(poll)
    p.pause()
    text = pathlib.Path(p.log_path).read_text(errors="replace")
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        lines.pop()                                    # a line still being written
    records = list(parse(lines, stubs, thread_names))
    out = records[:n]
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for rec in out:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    still_open = sum(1 for r in records if r["ret"] is None)
    threads = sorted({r["thread"] for r in out})
    print(f"capture_hle: wrote {len(out)} records to {out_path} (parsed {len(records)} calls, "
          f"{still_open} still open at the end; threads: {', '.join(threads)})")
    if timed_out:
        print(f"capture_hle: timed out after {timeout} s with {returns} returns", file=sys.stderr)
    return len(out)


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
        p = Ppsspp.launch(a.iso, a.home)
        try:
            written = capture(p, a.n, a.out, a.elf, a.timeout)
        finally:
            p.close()
    except (PpssppError, CaptureError) as e:
        sys.exit(f"capture_hle: {e}")
    sys.exit(0 if written >= a.n else 1)


if __name__ == "__main__":
    main()
