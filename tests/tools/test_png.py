"""png.py on synthetic images built in the test. Every pixel is made up."""
import pathlib, re, struct, sys, tempfile, unittest, zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle import png  # noqa: E402


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filtered(ftype, row, prev, bpp):
    """`row` filtered with `ftype` against the row above, `prev` (the encoder's side)."""
    out = bytearray()
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = (0, a, b, (a + b) // 2, paeth(a, b, c))[ftype]
        out.append((x - pred) & 0xFF)
    return bytes([ftype]) + bytes(out)


def rgb_png(width, rows, filters, bpp=3):
    """An 8-bit RGB (bpp 3) or RGBA (bpp 4) PNG whose row y uses filters[y]."""
    prev, raw = bytes(width * bpp), b""
    for row, f in zip(rows, filters):
        raw += filtered(f, row, prev, bpp)
        prev = row
    return png_of(width, len(rows), raw, bpp)


def png_of(width, height, raw, bpp=3, idat=None):
    """A PNG of already filtered `raw` image data; `idat` overrides the
    compressed bytes, a list splits them into several IDAT chunks."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, {3: 2, 4: 6}[bpp], 0, 0, 0)
    idat = zlib.compress(raw) if idat is None else idat
    parts = idat if isinstance(idat, list) else [idat]
    return (png.SIGNATURE + chunk(b"IHDR", ihdr) + b"".join(chunk(b"IDAT", d) for d in parts) +
            chunk(b"IEND", b""))


# A 2x5 RGB image with one row per filter type, filtered by hand (not by
# filtered() above, so a predictor bug shared with png.py still fails).
GOLDEN = [bytes(r) for r in (
    (10, 20, 30, 40, 50, 60),
    (15, 25, 35, 200, 10, 70),
    (20, 20, 20, 100, 100, 100),
    (30, 200, 60, 90, 80, 7),
    (50, 10, 113, 100, 0, 255),
)]
GOLDEN_RAW = bytes([
    0, 10, 20, 30, 40, 50, 60,          # None: as is
    # Sub, a = the byte 3 to the left: 15 25 35, 200-15, 10-25+256, 70-35.
    1, 15, 25, 35, 185, 241, 35,
    # Up, b = the byte above: 20-15, 20-25+256, 20-35+256, 100-200+256, 100-10, 100-70.
    2, 5, 251, 241, 156, 90, 30,
    # Average, (a + b) >> 1 without wrapping. Pixel 0 (a = 0, b = 20): 30-10,
    # 200-10, 60-10. Pixel 1: 90-(30+100)/2 = 25, 80-(200+100)/2+256 = 186
    # (a wrapped sum would give 22), 7-(60+100)/2+256 = 183.
    3, 20, 190, 50, 25, 186, 183,
    # Paeth, p = a + b - c, predict whichever of a, b, c is nearest p (ties:
    # a, then b). Pixel 0 (a = c = 0): b, so 50-30, 10-200+256, 113-60.
    # Then a=50 b=90 c=30: p 110, distances 60 20 80, b: 100-90 = 10.
    # a=10 b=80 c=200: p -110, distances 120 190 310, a: 0-10+256 = 246.
    # a=113 b=7 c=60: p 60, distances 53 53 0, c: 255-60 = 195.
    4, 20, 66, 53, 10, 246, 195,
])


class Png(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_round_trip_rgba(self):
        rows = [bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0]),
                bytes([1, 2, 3, 4, 250, 251, 252, 253, 17, 34, 51, 68])]
        path = self.tmp / "a.png"
        png.write(path, 3, 2, rows)
        self.assertEqual(png.read(path), (3, 2, rows))

    def test_read_rgb_all_filters(self):
        # Five rows of 4 pixels, one per filter type, with values that make
        # every predictor matter (wrapping, averages, Paeth picking a, b and c).
        rgb = [bytes((x * 61 + y * 37 + c * 89) % 256 for x in range(4) for c in range(3))
               for y in range(5)]
        path = self.tmp / "f.png"
        path.write_bytes(rgb_png(4, rgb, [1, 2, 3, 4, 0]))
        width, height, rows = png.read(path)
        self.assertEqual((width, height), (4, 5))
        want = [b"".join(row[i:i + 3] + b"\xff" for i in range(0, 12, 3)) for row in rgb]
        self.assertEqual(rows, want)

    def test_golden_filters(self):
        path = self.tmp / "g.png"
        path.write_bytes(png_of(2, 5, GOLDEN_RAW))
        want = [r[:3] + b"\xff" + r[3:] + b"\xff" for r in GOLDEN]
        self.assertEqual(png.read(path), (2, 5, want))

    def test_read_rgba_all_filters(self):
        rgba = [bytes((x * 61 + y * 37 + c * 89) % 256 for x in range(4) for c in range(4))
                for y in range(5)]
        path = self.tmp / "rgba.png"
        path.write_bytes(rgb_png(4, rgba, [1, 2, 3, 4, 0], bpp=4))
        self.assertEqual(png.read(path), (4, 5, rgba))

    def test_several_idat_chunks(self):
        z = zlib.compress(GOLDEN_RAW)
        path = self.tmp / "multi.png"
        path.write_bytes(png_of(2, 5, GOLDEN_RAW, idat=[z[:3], z[3:10], b"", z[10:]]))
        self.assertEqual(png.read(path)[2][4], bytes([50, 10, 113, 255, 100, 0, 255, 255]))

    def test_broken_files(self):
        good = png_of(2, 5, GOLDEN_RAW)
        ihdr = good[8:8 + 25]
        iend = chunk(b"IEND", b"")
        cases = {
            "truncated": good[:40],
            "truncated (no IEND)": good[:-len(iend)],
            "bad image data": png_of(2, 5, GOLDEN_RAW, idat=b"\x78\x9c not zlib"),
            "bad filter type 5": png_of(2, 5, bytes([5]) + GOLDEN_RAW[1:]),
            "bytes of image data, expected 35": png_of(2, 5, GOLDEN_RAW[:-1]),
            "first chunk is b'IDAT', not IHDR": good[:8] + good[33:],
            "second IHDR": good[:33] + ihdr + good[33:],
        }
        for msg, data in cases.items():
            with self.subTest(msg):
                path = self.tmp / "broken.png"
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, re.escape(msg)):
                    png.read(path)

    def test_bad_crc(self):
        data = bytearray(rgb_png(2, [bytes(6)], [0]))
        data[29] ^= 1                                   # first byte of the IHDR CRC
        path = self.tmp / "bad.png"
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(ValueError, "CRC"):
            png.read(path)

    def test_rejects_other_formats(self):
        ihdr = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)      # 16-bit RGB
        path = self.tmp / "16.png"
        path.write_bytes(png.SIGNATURE + chunk(b"IHDR", ihdr) +
                         chunk(b"IDAT", zlib.compress(bytes(7))) + chunk(b"IEND", b""))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            png.read(path)
        path.write_bytes(b"not a png")
        with self.assertRaisesRegex(ValueError, "signature"):
            png.read(path)

    def test_write_checks_rows(self):
        with self.assertRaises(ValueError):
            png.write(self.tmp / "x.png", 2, 1, [bytes(7)])
        with self.assertRaises(ValueError):
            png.write(self.tmp / "x.png", 1, 2, [bytes(4)])


if __name__ == "__main__":
    unittest.main()
