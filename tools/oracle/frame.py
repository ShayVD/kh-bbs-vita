#!/usr/bin/env python3
"""Grab PPSSPP's frame N as a 480x272 PNG.

Frame N is the N-th call to sceDisplaySetFrameBuf(topaddr, bufferwidth,
pixelformat, sync) (docs/oracle.md, "Frames"). A stopping breakpoint goes
on that import stub; at the N-th stop the buffer the game is about to show
is read from VRAM (PPSSPP runs with SoftwareRenderer = True, so it draws
there), converted with vram.py and cropped to 480 pixels wide.

--raw FILE also writes the VRAM bytes (bufferwidth * 272 pixels) and the
sidecar FILE.json: {"topaddr":"0x...","stride":512,"format":1,"frame":N}.
Stops at other addresses are resumed and not counted (snapshot.stop_at).
The outputs are removed first and on any failure, and may not be an input
or each other. Exit 0 on success, 1 on failure, 2 on bad usage.

Usage:
  tools/oracle/frame.py --iso ISO --elf game/EBOOT.dec.ELF --frame 300 \\
      -o build/oracle/ppsspp-frame-300.png
"""
import argparse, json, pathlib, struct, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from oracle import imports, png, snapshot, vram  # noqa: E402
from oracle.ppsspp import Ppsspp, PpssppError  # noqa: E402
from oracle.snapshot import SnapshotError  # noqa: E402

NID = 0x289D82FE                 # sceDisplaySetFrameBuf
WIDTH, HEIGHT = 480, 272
MAX_STRIDE = 1024                # sceDisplaySetFrameBuf rejects wider buffers
VRAM = (0x04000000, 0x04200000)  # 2 MiB; the buffer must lie inside


class FrameError(Exception):
    pass


def grab(p, targets, n, timeout=120.0):
    """With PPSSPP `p` stopped at the entry: stop at the n-th call through any
    stub in `targets` and read the framebuffer it names. Returns
    (topaddr, stride, fmt, data, other stops)."""
    _pc, other = snapshot.stop_at(p, targets, n, timeout)
    regs = p.regs()
    for r in ("a0", "a1", "a2"):
        if r not in regs:
            raise FrameError(f"frame {n}: PPSSPP gave no {r} register")
    topaddr, stride, fmt = regs["a0"], regs["a1"], regs["a2"]
    where = f"frame {n}: topaddr 0x{topaddr:08X}, stride {stride}, format {fmt}"
    if topaddr == 0:
        raise FrameError(f"{where}: no framebuffer (display off)")
    if fmt not in vram.BPP:
        raise FrameError(f"{where}: unknown pixel format")
    if not WIDTH <= stride <= MAX_STRIDE:
        raise FrameError(f"{where}: stride outside {WIDTH}..{MAX_STRIDE}")
    want = vram.size(stride, HEIGHT, fmt)
    addr = topaddr & 0x3FFFFFFF                  # 0x44000000 is the uncached mirror of VRAM
    if not VRAM[0] <= addr <= VRAM[1] - want:
        raise FrameError(f"{where}: 0x{addr:08X}..0x{addr + want:08X} is not inside VRAM "
                         f"0x{VRAM[0]:08X}..0x{VRAM[1]:08X}")
    data = p.read_memory(addr, want)
    if len(data) != want:
        raise FrameError(f"{where}: read {len(data)} bytes of VRAM, expected {want}")
    return topaddr, stride, fmt, data, other


def sidecar(topaddr, stride, fmt, n):
    return json.dumps({"topaddr": "0x%08X" % topaddr, "stride": stride, "format": fmt, "frame": n},
                      separators=(",", ":")) + "\n"


def outputs(a):
    files = [pathlib.Path(a.out)]
    if a.raw:
        files += snapshot.outputs(a.raw)
    return files


def positive(text):
    n = int(text, 0)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text}: want a frame number >= 1")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--frame", required=True, type=positive, metavar="N")
    ap.add_argument("-o", "--out", required=True, help="the PNG")
    ap.add_argument("--raw", metavar="FILE", help="also write the raw VRAM bytes and FILE.json")
    ap.add_argument("--home", default="build/oracle/ppsspp-home")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds to reach the frame")
    a = ap.parse_args(argv)
    files = outputs(a)
    for i, f in enumerate(files):
        for x in (a.iso, a.elf, *files[:i]):         # an input, or an earlier output
            if snapshot.same_file(f, x):
                print(f"frame: refusing output {f}: it is also {x}", file=sys.stderr)
                return 2
    try:
        for f in files:                          # a failed run must not leave an old frame
            f.unlink(missing_ok=True)
        try:
            stubs = imports.stubs(imports.load(a.elf))
        except (OSError, ValueError, KeyError, struct.error) as e:
            raise FrameError(f"cannot read the import table of {a.elf}: {e}") from None
        targets = {s: n for s, (n, _) in stubs.items() if n == NID}
        if not targets:
            raise FrameError(f"{a.elf} does not import sceDisplaySetFrameBuf (0x{NID:08X})")
        p = Ppsspp.launch(a.iso, a.home)
        try:
            topaddr, stride, fmt, data, other = grab(p, targets, a.frame, a.timeout)
        finally:
            p.close()
        try:
            rows = vram.vram_to_rgba(data, stride, WIDTH, HEIGHT, fmt)
            pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            png.write(a.out, WIDTH, HEIGHT, rows)
            if a.raw:
                raw, side = snapshot.outputs(a.raw)
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(data)
                side.write_text(sidecar(topaddr, stride, fmt, a.frame))
        except BaseException:
            for f in files:
                f.unlink(missing_ok=True)
            raise
    except (PpssppError, SnapshotError, FrameError, ValueError, OSError) as e:
        print(f"frame: {e}", file=sys.stderr)
        return 1
    extra = f" ({other} other stops resumed)" if other else ""
    print(f"frame: frame {a.frame}: topaddr 0x{topaddr:08X}, stride {stride}, format {fmt}; "
          f"written to {a.out}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
