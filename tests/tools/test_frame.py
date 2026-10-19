"""frame.py's frame counting, checks and outputs, with a fake PPSSPP session
and a synthetic ELF. Every value is made up."""
import contextlib, io, json, pathlib, re, struct, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from elfgen import MODULE_STUBS, make_module_elf  # noqa: E402
from oracle import frame, png  # noqa: E402
from oracle.ppsspp import PpssppError  # noqa: E402

STUB = MODULE_STUBS + 8
OTHER = 0x08A00000
TOP = 0x04044000


class FakeSession:
    """A Ppsspp stopped at the entry: each resume() is followed by the next
    stop in `stops` (a pc, or an exception to raise). At a stop, a0..a2 are
    `args`; VRAM holds format-1 pixels whose red is the column (mod 32) and
    blue the line (mod 32)."""
    def __init__(self, stops, args=(TOP, 512, 1), short=0, drop=None):
        self.stops, self.args, self.calls = list(stops), args, []
        self.short, self.drop = short, drop          # bytes missing from a read; a register left out
        self.breakpoints = {}
        self.close = mock.Mock()

    def add_breakpoint(self, addr, enabled=True, log=False, condition=None, log_format=None):
        self.breakpoints[addr] = enabled

    def resume(self):
        self.calls.append("resume")

    def wait_stepping(self, timeout=None):
        stop = self.stops.pop(0)
        if isinstance(stop, Exception):
            raise stop
        return {"pc": stop, "ticks": 1}

    def regs(self):
        a0, a1, a2 = self.args
        regs = {"a0": a0, "a1": a1, "a2": a2, "ra": 0x08A8BE30}
        regs.pop(self.drop, None)
        return regs

    def read_memory(self, addr, size):
        self.calls.append(("read", addr, size))
        stride = self.args[1]
        data = b"".join(struct.pack("<H", (x & 31) | (y & 31) << 10)
                        for y in range(size // 2 // stride) for x in range(stride))
        return data[:len(data) - self.short]


class Frame(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.elf = self.tmp / "module.elf"
        self.elf.write_bytes(make_module_elf([("sceDisplay", [0x0E20F177, frame.NID])]))
        self.out = self.tmp / "o/frame.png"
        self.raw = self.tmp / "o/frame.bin"

    def run_main(self, p, *extra):
        argv = ["--iso", "game.iso", "--elf", str(self.elf), "--frame", "2", "-o", str(self.out), *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(frame.Ppsspp, "launch", return_value=p) as launch:
            code = frame.main(argv)
        return code, out.getvalue(), err.getvalue(), launch

    def test_grab_counts_frames_and_reads_the_buffer(self):
        p = FakeSession([STUB, OTHER, STUB])
        topaddr, stride, fmt, data, other = frame.grab(p, {STUB: frame.NID}, 2)
        self.assertEqual((topaddr, stride, fmt, other), (TOP, 512, 1, 1))
        self.assertEqual(p.breakpoints, {STUB: True})
        self.assertEqual(p.calls, ["resume"] * 3 + [("read", TOP, 512 * 272 * 2)])
        self.assertEqual(len(data), 512 * 272 * 2)

    def test_main_writes_png_and_raw(self):
        p = FakeSession([STUB, STUB])
        code, out, err, _ = self.run_main(p, "--raw", str(self.raw))
        self.assertEqual(code, 0, err)
        self.assertIn("topaddr 0x04044000, stride 512, format 1", out)
        p.close.assert_called_once()
        width, height, rows = png.read(self.out)
        self.assertEqual((width, height, len(rows[0])), (480, 272, 480 * 4))
        self.assertEqual(rows[3][5 * 4:5 * 4 + 4], bytes([41, 0, 24, 255]))    # R 5, B 3
        self.assertEqual(self.raw.stat().st_size, 512 * 272 * 2)
        side = (self.tmp / "o/frame.bin.json").read_text()
        self.assertEqual(json.loads(side), {"topaddr": "0x04044000", "stride": 512, "format": 1, "frame": 2})

    def test_bad_framebuffer_fails_and_cleans_up(self):
        self.out.parent.mkdir()
        self.out.write_bytes(b"old")
        for args, msg in (((0, 512, 1), "no framebuffer"), ((TOP, 512, 7), "unknown pixel format"),
                          ((TOP, 256, 1), "stride outside")):
            with self.subTest(msg):
                p = FakeSession([STUB, STUB], args)
                code, _, err, _ = self.run_main(p)
                self.assertEqual(code, 1)
                self.assertIn(msg, err)
                p.close.assert_called_once()
                self.assertFalse(self.out.exists())

    def test_grab_errors(self):
        cases = [(dict(short=2), "frame 1: topaddr 0x04044000, stride 512, format 1: read 278526 bytes of "
                                 "VRAM, expected 278528"),
                 (dict(drop="a2"), "frame 1: PPSSPP gave no a2 register"),
                 (dict(args=(0x041F0000, 512, 1)), "0x041F0000..0x04234000 is not inside VRAM "
                                                   "0x04000000..0x04200000"),
                 (dict(args=(0x08800000, 512, 1)), "is not inside VRAM"),
                 (dict(args=(0x03FFF000, 512, 1)), "is not inside VRAM")]
        for kw, msg in cases:
            with self.subTest(msg):
                p = FakeSession([STUB], **kw)
                with self.assertRaisesRegex(frame.FrameError, re.escape(msg)):
                    frame.grab(p, {STUB: frame.NID}, 1)
                if "args" in kw:
                    self.assertNotIn("read", [c[0] for c in p.calls if isinstance(c, tuple)])
        # The uncached mirror is read at the plain address.
        p = FakeSession([STUB], args=(0x44044000, 512, 1))
        self.assertEqual(frame.grab(p, {STUB: frame.NID}, 1)[0], 0x44044000)
        self.assertEqual(p.calls[-1], ("read", TOP, 512 * 272 * 2))
        p = FakeSession([STUB], args=(0x441F0000, 512, 1))
        with self.assertRaisesRegex(frame.FrameError, "0x041F0000..0x04234000 is not inside VRAM"):
            frame.grab(p, {STUB: frame.NID}, 1)
        # The last byte of VRAM is fine.
        p = FakeSession([STUB], args=(0x04200000 - 512 * 272 * 2, 512, 1))
        self.assertEqual(frame.grab(p, {STUB: frame.NID}, 1)[0], 0x04200000 - 512 * 272 * 2)

    def test_main_reports_a_missing_register(self):
        code, _, err, _ = self.run_main(FakeSession([STUB, STUB], drop="a0"))
        self.assertEqual(code, 1)
        self.assertIn("frame: frame 2: PPSSPP gave no a0 register", err)

    def test_ppsspp_error_closes(self):
        p = FakeSession([PpssppError("PPSSPP exited")])
        code, _, err, _ = self.run_main(p)
        self.assertEqual(code, 1)
        self.assertIn("frame: PPSSPP exited (0 of 2 calls seen", err)
        p.close.assert_called_once()

    def test_module_without_setframebuf(self):
        self.elf.write_bytes(make_module_elf([("sceDisplay", [0x0E20F177])]))
        code, _, err, launch = self.run_main(FakeSession([]))
        self.assertEqual(code, 1)
        self.assertIn("does not import sceDisplaySetFrameBuf (0x289D82FE)", err)
        launch.assert_not_called()

    def test_refuses_to_overwrite_inputs(self):
        for extra in (["-o", str(self.elf)], ["-o", str(self.out), "--raw", str(self.elf)],
                      ["-o", str(self.raw), "--raw", str(self.raw)],
                      ["-o", str(self.tmp / "o/x/../frame.bin.json"), "--raw", str(self.raw)]):
            with self.subTest(extra):
                argv = ["--iso", "game.iso", "--elf", str(self.elf), "--frame", "1", *extra]
                err = io.StringIO()
                with contextlib.redirect_stderr(err), mock.patch.object(frame.Ppsspp, "launch") as launch:
                    self.assertEqual(frame.main(argv), 2)
                launch.assert_not_called()
                self.assertIn("refusing output", err.getvalue())
                self.assertTrue(self.elf.exists())

    def test_frame_must_be_positive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            frame.main(["--iso", "x", "--elf", "y", "--frame", "0", "-o", "z"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
