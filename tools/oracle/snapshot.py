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
EVENT = re.compile(r"(?:(0[xX][0-9a-fA-F]{1,8})|([A-Za-z_]\w*)):(0[xX][0-9a-fA-F]+|\d+)")


class SnapshotError(Exception):
    pass


def parse_event(text):
    """(nid or None, name or None, k) from NAME:K or 0xNID:K."""
    m = EVENT.fullmatch(text)
    if not m:
        raise SnapshotError(f"bad event {text!r} (want NAME:K or 0xNID:K, K >= 1)")
    k = int(m.group(3), 16 if m.group(3)[:2] in ("0x", "0X") else 10)
    if k < 1:
        raise SnapshotError(f"bad event {text!r}: K must be at least 1")
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


def take(p, targets, k, event, out, timeout=120.0):
    """With PPSSPP `p` stopped at the entry: stop at the k-th call through any
    stub in `targets` ({stub_addr: nid}), read RAM and write `out` and its
    sidecar. Stops elsewhere are resumed and not counted. Returns the number
    of other stops."""
    out = pathlib.Path(out)
    side = pathlib.Path(str(out) + ".json")
    out.unlink(missing_ok=True)
    side.unlink(missing_ok=True)
    for addr in targets:
        p.add_breakpoint(addr, enabled=True)
    deadline = time.monotonic() + timeout
    seen = other = 0
    while True:
        p.resume()
        left = deadline - time.monotonic()
        if left <= 0:
            raise SnapshotError(f"timed out after {timeout} s with {seen} of {k} calls seen")
        try:
            pc = p.wait_stepping(left)["pc"]
        except PpssppError as e:
            raise SnapshotError(f"{e} ({seen} of {k} calls seen)") from e
        if pc in targets:
            seen += 1
            if seen == k:
                break
        else:
            other += 1
    regs = p.regs()
    data = p.read_memory(BASE, SIZE)
    if len(data) != SIZE:
        raise SnapshotError(f"read {len(data)} bytes of RAM, expected {SIZE}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    side.write_text(sidecar(event, targets[pc], regs["ra"]))
    return other


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--at", required=True, type=event_spec, metavar="EVENT", help="NAME:K or 0xNID:K")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--home", default="build/oracle/ppsspp-home")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds to reach the event")
    a = ap.parse_args(argv)
    try:
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
    except (PpssppError, SnapshotError) as e:
        print(f"snapshot: {e}", file=sys.stderr)
        return 1
    extra = f" ({other} other stops resumed)" if other else ""
    print(f"snapshot: {event} written to {a.out}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
