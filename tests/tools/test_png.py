"""png.py on synthetic images built in the test. Every pixel is made up."""
import pathlib, struct, sys, tempfile, unittest, zlib

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


def rgb_png(width, rows, filters):
    """An 8-bit RGB PNG whose row y uses filters[y]."""
    prev, raw = bytes(width * 3), b""
    for row, f in zip(rows, filters):
        raw += filtered(f, row, prev, 3)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    return png.SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


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

    def test_bad_crc(self):
        data = bytearray(rgb_png(2, [bytes(6)], [0]))
        data[29] ^= 1                                   # last byte of the IHDR CRC
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
