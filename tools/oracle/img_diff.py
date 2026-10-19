#!/usr/bin/env python3
"""Compare two PNG frames (for example khbbs's and PPSSPP's frame N).

Prints the PSNR over RGB (alpha is ignored), and the count and bounding box
of the pixels where any RGB channel differs by more than --tolerance.
--mask writes those pixels red over a dimmed copy of the first image.

Exit 0 if the images are identical or the PSNR is at least --psnr-min,
1 otherwise, 2 if the sizes differ, a file cannot be read or --mask is one
of the inputs.

Usage:
  tools/oracle/img_diff.py build/oracle/khbbs-frame-600.png \\
      build/oracle/ppsspp-frame-600.png --mask build/oracle/frame-600-mask.png
"""
import argparse, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from oracle import png, snapshot  # noqa: E402

RED = b"\xff\x00\x00\xff"


def compare(a, b, tolerance):
    """a, b: (width, height, rows) of the same size, as png.read() returns.
    {"psnr": dB or math.inf, "count": pixels over tolerance,
     "bbox": (x0, y0, x1, y1) inclusive, or None, "diff": set of (x, y)}."""
    width, height, arows = a
    brows = b[2]
    sq, diff = 0, set()
    for y in range(height):
        ra, rb = arows[y], brows[y]
        if ra == rb:
            continue
        for x in range(width):
            i = x * 4
            d = [abs(ra[i + c] - rb[i + c]) for c in range(3)]
            sq += d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
            if max(d) > tolerance:
                diff.add((x, y))
    mse = sq / (width * height * 3) if width and height else 0
    psnr = math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse)
    bbox = None
    if diff:
        xs, ys = [p[0] for p in diff], [p[1] for p in diff]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    return {"psnr": psnr, "count": len(diff), "bbox": bbox, "diff": diff}


def mask_rows(a, diff):
    """Rows of `a` dimmed to a third, with the pixels in `diff` red."""
    width, height, rows = a
    out = []
    for y in range(height):
        row = bytearray(v // 3 for v in rows[y])
        row[3::4] = b"\xff" * width
        for x in range(width):
            if (x, y) in diff:
                row[x * 4:x * 4 + 4] = RED
        out.append(bytes(row))
    return out


def tolerance(text):
    n = int(text)
    if n < 0:
        raise argparse.ArgumentTypeError(f"{text}: want a channel difference >= 0")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--mask", help="write a PNG of the differing pixels")
    ap.add_argument("--psnr-min", type=float, default=40.0, help="pass threshold in dB (default 40)")
    ap.add_argument("--tolerance", type=tolerance, default=2,
                    help="largest channel difference that does not count (default 2)")
    args = ap.parse_args(argv)
    for x in (args.a, args.b):
        if args.mask and snapshot.same_file(args.mask, x):
            print(f"img_diff: refusing --mask {args.mask}: it is also {x}", file=sys.stderr)
            return 2
    try:
        a, b = png.read(args.a), png.read(args.b)
    except (OSError, ValueError) as e:
        print(f"img_diff: {e}", file=sys.stderr)
        return 2
    if a[:2] != b[:2]:
        print(f"img_diff: size mismatch: {a[0]}x{a[1]} vs {b[0]}x{b[1]}", file=sys.stderr)
        return 2
    r = compare(a, b, args.tolerance)
    psnr = "inf" if r["psnr"] == math.inf else f"{r['psnr']:.2f}"
    where = ""
    if r["bbox"]:
        x0, y0, x1, y1 = r["bbox"]
        where = f", bbox x {x0}..{x1}, y {y0}..{y1}"
    print(f"img_diff: psnr {psnr} dB; {r['count']} pixels differ by more than {args.tolerance}{where}")
    if args.mask:
        try:
            pathlib.Path(args.mask).parent.mkdir(parents=True, exist_ok=True)
            png.write(args.mask, a[0], a[1], mask_rows(a, r["diff"]))
        except OSError as e:
            print(f"img_diff: cannot write the mask: {e}", file=sys.stderr)
            return 2
    return 0 if r["psnr"] >= args.psnr_min else 1


if __name__ == "__main__":
    sys.exit(main())
