#!/usr/bin/env python3
"""Snapshot PPSSPP's guest RAM just before the K-th call to a firmware function.

The event is NAME:K or 0xNID:K (K >= 1), as for `khbbs --snapshot-at`. NAME
is looked up among the module's imports by its resolved name or its SHA-1
NID. A stopping breakpoint goes on that import stub; at the K-th stop there,
RAM [0x08000000, 0x0A000000) is read (while stepping: reads while running
are torn, docs/oracle.md) and written raw to OUT, with the sidecar OUT.json
that tools/oracle/mem_diff.py reads:
  {"base":"0x08000000","size":33554432,"event":"NAME:K","side":"ppsspp",
   "nid":"0x...","ra":"0x..."}

Stops at other addresses (anything else that stops the CPU) are resumed and
not counted; 100 in a row at the same address abort the run as a stuck or
faulting guest. OUT and OUT.json are removed first and on any failure, and
may not be the ISO or the ELF. Exit 0 on success, 1 on failure, 2 on bad
usage.

Usage:
  tools/oracle/snapshot.py --iso ISO --elf game/EBOOT.dec.ELF \\
      --at sceKernelCreateThread:1 -o build/oracle/ppsspp-mem.bin
"""
import argparse, json, pathlib, re, struct, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import import_report  # noqa: E402
from oracle import imports  # noqa: E402
from oracle.ppsspp import Ppsspp, PpssppError  # noqa: E402

BASE, SIZE = 0x08000000, 0x02000000
# The grammar of runtime/snapshot.c: ASCII only, K from 1 to 0xFFFFFFFF.
EVENT = re.compile(r"(?:(0[xX][0-9a-fA-F]{1,8})|([A-Za-z_]\w*)):(0[xX][0-9a-fA-F]+|\d+)", re.ASCII)
K_MAX = 0xFFFFFFFF
STUCK = 100                # consecutive stops at one non-target pc that abort


class SnapshotError(Exception):
    pass


def parse_event(text):
    """(nid or None, name or None, k) from NAME:K or 0xNID:K."""
    m = EVENT.fullmatch(text)
    if not m:
        raise SnapshotError(f"bad event {text!r} (want NAME:K or 0xNID:K, K >= 1)")
    k = int(m.group(3), 16 if m.group(3)[:2] in ("0x", "0X") else 10)
    if not 1 <= k <= K_MAX:
        raise SnapshotError(f"bad event {text!r}: K must be from 1 to 0x{K_MAX:X}")
    return (int(m.group(1), 16) if m.group(1) else None), m.group(2), k


def event_spec(text):
    try:
        return text, parse_event(text)
    except SnapshotError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def resolve(stubs, nid, name):
    """{stub_addr: nid} of the stubs that import the event's function.
    `stubs` is imports.stubs()."""
    if name is not None:
        want = import_report.nid_of(name)
        found = {a: n for a, (n, nm) in stubs.items() if nm == name or n == want}
    else:
        found = {a: n for a, (n, _) in stubs.items() if n == nid}
    if not found:
        raise SnapshotError(f"the module does not import {name or '0x%08X' % nid}")
    return found


def sidecar(event, nid, ra):
    return json.dumps({"base": "0x%08X" % BASE, "size": SIZE, "event": event, "side": "ppsspp",
                       "nid": "0x%08X" % nid, "ra": "0x%08X" % ra}, separators=(",", ":")) + "\n"


def outputs(out):
    out = pathlib.Path(out)
    return out, pathlib.Path(str(out) + ".json")


def same_file(a, b):
    """Same resolved path, or both exist and are the same file (hard links)."""
    a, b = pathlib.Path(a), pathlib.Path(b)
    try:
        return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))
    except OSError:
        return False


def take(p, targets, k, event, out, timeout=120.0):
    """With PPSSPP `p` stopped at the entry: stop at the k-th call through any
    stub in `targets` ({stub_addr: nid}), read RAM and write `out` and its
    sidecar. Stops elsewhere are resumed and not counted; STUCK in a row at
    one pc raise SnapshotError. Returns the number of other stops. On any
    error neither `out` nor its sidecar is left."""
    out, side = outputs(out)
    for f in (out, side):
        f.unlink(missing_ok=True)
    try:
        return _take(p, targets, k, event, out, side, timeout)
    except BaseException:
        for f in (out, side):
            f.unlink(missing_ok=True)
        raise


def _take(p, targets, k, event, out, side, timeout):
    pc, other = stop_at(p, targets, k, timeout)
    regs = p.regs()
    data = p.read_memory(BASE, SIZE)
    if len(data) != SIZE:
        raise SnapshotError(f"read {len(data)} bytes of RAM, expected {SIZE}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    side.write_text(sidecar(event, targets[pc], regs["ra"]))
    return other


def stop_at(p, targets, k, timeout):
    """With PPSSPP `p` stopped: put a stopping breakpoint on every address in
    `targets` and run until the k-th stop at any of them; `p` is left
    stepping there. Stops elsewhere are resumed and not counted; STUCK in a
    row at one pc raise SnapshotError, and so do a timeout or a lost
    session. Returns (pc, number of other stops). frame.py uses it too."""
    for addr in targets:
        p.add_breakpoint(addr, enabled=True)
    deadline = time.monotonic() + timeout
    seen = other = same = 0
    last = None                                  # pc of the last non-target stop

    def where():
        tail = f"; last other stop at 0x{last:08X}" if last is not None else ""
        return f"{seen} of {k} calls seen, {other} other stops{tail}"

    while True:
        p.resume()
        left = deadline - time.monotonic()
        if left <= 0:
            raise SnapshotError(f"timed out after {timeout} s ({where()})")
        try:
            pc = p.wait_stepping(left)["pc"]
        except PpssppError as e:
            raise SnapshotError(f"{e} ({where()})") from e
        if pc in targets:
            same = 0
            seen += 1
            if seen == k:
                break
            continue
        other += 1
        same = same + 1 if pc == last else 1
        last = pc
        if same >= STUCK:
            raise SnapshotError(f"guest stuck/faulting at 0x{pc:08X}: {STUCK} stops in a row there "
                                f"({where()})")
    return pc, other


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--at", required=True, type=event_spec, metavar="EVENT", help="NAME:K or 0xNID:K")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--home", default="build/oracle/ppsspp-home")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds to reach the event")
    a = ap.parse_args(argv)
    for f in outputs(a.out):
        for x in (a.iso, a.elf):
            if same_file(f, x):
                print(f"snapshot: refusing -o {a.out}: it or its sidecar is also {x}", file=sys.stderr)
                return 2
    try:
        for f in outputs(a.out):                 # a failed run must not leave an old dump
            f.unlink(missing_ok=True)
        event, (nid, name, k) = a.at
        try:
            stubs = imports.stubs(imports.load(a.elf))
        except (OSError, ValueError, KeyError, struct.error) as e:
            raise SnapshotError(f"cannot read the import table of {a.elf}: {e}") from None
        targets = resolve(stubs, nid, name)
        p = Ppsspp.launch(a.iso, a.home)
        try:
            other = take(p, targets, k, event, a.out, a.timeout)
        finally:
            p.close()
    except (PpssppError, SnapshotError, OSError) as e:
        print(f"snapshot: {e}", file=sys.stderr)
        return 1
    extra = f" ({other} other stops resumed)" if other else ""
    print(f"snapshot: {event} written to {a.out}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
