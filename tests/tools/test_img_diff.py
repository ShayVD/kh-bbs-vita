"""img_diff.py on synthetic 16x16 images. Every pixel is made up."""
import contextlib, io, math, pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle import img_diff, png  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "oracle" / "img_diff.py"
W = H = 16


def image():
    """A 16x16 RGBA gradient, as mutable rows."""
    return [bytearray(b"".join(bytes((x * 16, y * 16, (x + y) * 4, 255)) for x in range(W)))
            for y in range(H)]


class ImgDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.a = self.tmp / "a.png"
        png.write(self.a, W, H, [bytes(r) for r in image()])

    def write(self, name, rows, width=W, height=H):
        path = self.tmp / name
        png.write(path, width, height, [bytes(r) for r in rows])
        return path

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = img_diff.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_identical(self):
        b = self.write("b.png", image())
        self.assertEqual(img_diff.compare(png.read(self.a), png.read(b), 2)["psnr"], math.inf)
        code, out, _ = self.run_main(self.a, b, "--psnr-min", 1000)
        self.assertEqual(code, 0)
        self.assertIn("psnr inf dB", out)
        self.assertIn("0 pixels differ", out)

    def test_one_pixel_under_tolerance(self):
        rows = image()
        rows[5][7 * 4 + 1] += 1
        b = self.write("b.png", rows)
        r = img_diff.compare(png.read(self.a), png.read(b), 2)
        self.assertEqual((r["count"], r["bbox"]), (0, None))
        self.assertAlmostEqual(r["psnr"], 10 * math.log10(255 ** 2 * W * H * 3), places=6)
        self.assertEqual(self.run_main(self.a, b, "--tolerance", 2)[0], 0)

    def test_block_changed(self):
        rows = image()
        for y in range(9, 13):
            for x in range(3, 7):
                rows[y][x * 4:x * 4 + 3] = b"\xff\x00\xff"
        b = self.write("b.png", rows)
        mask = self.tmp / "out/mask.png"
        code, out, _ = self.run_main(self.a, b, "--psnr-min", 60, "--mask", mask)
        self.assertEqual(code, 1)
        self.assertIn("16 pixels differ", out)
        self.assertIn("bbox x 3..6, y 9..12", out)
        self.assertEqual(img_diff.compare(png.read(self.a), png.read(b), 2)["bbox"], (3, 9, 6, 12))
        width, height, mrows = png.read(mask)
        self.assertEqual((width, height), (W, H))
        self.assertEqual(mrows[9][3 * 4:3 * 4 + 4], b"\xff\x00\x00\xff")            # red
        a_px = png.read(self.a)[2][0][4:8]
        self.assertEqual(mrows[0][4:8], bytes(v // 3 for v in a_px[:3]) + b"\xff")  # dimmed a
        # The same block passes a low enough PSNR threshold.
        self.assertEqual(self.run_main(self.a, b, "--psnr-min", 10)[0], 0)

    def test_size_mismatch(self):
        b = self.write("b.png", [r[:8 * 4] for r in image()], width=8)
        code, _, err = self.run_main(self.a, b)
        self.assertEqual(code, 2)
        self.assertIn("16x16 vs 8x16", err)

    def test_unreadable(self):
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"nope")
        code, _, err = self.run_main(self.a, bad)
        self.assertEqual(code, 2)
        self.assertIn("img_diff:", err)

    def test_refuses_mask_over_an_input(self):
        b = self.write("b.png", image())
        (self.tmp / "sub").mkdir()
        (self.tmp / "link.png").hardlink_to(b)
        for mask in (self.a, self.tmp / "sub/../b.png", self.tmp / "link.png"):
            with self.subTest(mask):
                code, _, err = self.run_main(self.a, b, "--mask", mask)
                self.assertEqual(code, 2)
                self.assertIn("refusing --mask", err)
        self.assertEqual(png.read(self.a), png.read(b))

    def test_negative_tolerance(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as e:
            img_diff.main([str(self.a), str(self.a), "--tolerance", "-1"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("--tolerance", err.getvalue())

    def test_command_line(self):
        r = subprocess.run([sys.executable, str(TOOL), str(self.a), str(self.a)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("psnr inf dB", r.stdout)


if __name__ == "__main__":
    unittest.main()
