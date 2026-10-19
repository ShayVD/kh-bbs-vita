"""snapshot.py's event parsing, stub lookup and stop counting, with a fake
PPSSPP session and a synthetic ELF. Every value is made up."""
import contextlib, io, json, pathlib, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from elfgen import MODULE_STUBS, make_module_elf  # noqa: E402
from import_report import nid_of  # noqa: E402
from oracle import imports, snapshot  # noqa: E402
from oracle.ppsspp import PpssppError  # noqa: E402
from oracle.snapshot import SnapshotError  # noqa: E402

CREATE = nid_of("sceKernelCreateThread")
STUB_A, STUB_CREATE = MODULE_STUBS, MODULE_STUBS + 8
OTHER = 0x08A00000


class FakeSession:
    """Stands in for a Ppsspp stopped at the entry: each resume() is followed
    by the next stop in `stops` (a pc, or an exception to raise)."""
    def __init__(self, stops):
        self.stops, self.breakpoints, self.calls = list(stops), {}, []
        self.pc = None

    def add_breakpoint(self, addr, enabled=True, log=False, condition=None, log_format=None):
        self.breakpoints[addr] = enabled

    def resume(self):
        self.calls.append("resume")

    def wait_stepping(self, timeout=None):
        stop = self.stops.pop(0)
        if isinstance(stop, Exception):
            raise stop
        self.pc = stop
        return {"pc": stop, "ticks": 1}

    def regs(self):
        self.calls.append("regs")
        return {"pc": self.pc} if getattr(self, "no_ra", False) else {"pc": self.pc, "ra": 0x08A8BE30}

    def read_memory(self, addr, size):
        self.calls.append(("read", addr, size, self.pc))
        return bytes([0xAB]) * size


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        elf = self.tmp / "module.elf"
        elf.write_bytes(make_module_elf([("ThreadManForUser", [0x12345678, CREATE])]))
        self.stubs = imports.stubs(imports.load(elf), None, None)
        self.out = self.tmp / "o/mem.bin"

    def test_parse_event(self):
        self.assertEqual(snapshot.parse_event("sceKernelCreateThread:1"), (None, "sceKernelCreateThread", 1))
        self.assertEqual(snapshot.parse_event("0x446d8de6:0x10"), (0x446D8DE6, None, 16))
        self.assertEqual(snapshot.parse_event("_x9:010"), (None, "_x9", 10))
        self.assertEqual(snapshot.parse_event("f:4294967295")[2], 0xFFFFFFFF)
        self.assertEqual(snapshot.parse_event("f:0xFFFFFFFF")[2], 0xFFFFFFFF)
        for bad in ("", "sceFoo", "sceFoo:", "sceFoo:0", ":1", "0x:1", "0xZZ:1", "0x123456789:1",
                    "sceFoo:-1", "sceFoo:1x", "1abc:1", "sce Foo:1", "sceFoo:1:2",
                    "sceFoo:4294967296", "sceFoo:0x100000000", "sceFoé:1", "sceFoo:\u0661"):
            with self.subTest(bad), self.assertRaises(SnapshotError):
                snapshot.parse_event(bad)

    def test_resolve(self):
        # By SHA-1 of the name (stubs() was given no names), and by NID.
        self.assertEqual(snapshot.resolve(self.stubs, None, "sceKernelCreateThread"), {STUB_CREATE: CREATE})
        self.assertEqual(snapshot.resolve(self.stubs, 0x12345678, None), {STUB_A: 0x12345678})
        with self.assertRaisesRegex(SnapshotError, "does not import sceIoOpen"):
            snapshot.resolve(self.stubs, None, "sceIoOpen")
        with self.assertRaisesRegex(SnapshotError, "does not import 0x0BADF00D"):
            snapshot.resolve(self.stubs, 0x0BADF00D, None)

    def test_take_counts_only_stops_on_the_stub(self):
        p = FakeSession([STUB_CREATE, OTHER, STUB_CREATE])
        targets = snapshot.resolve(self.stubs, None, "sceKernelCreateThread")
        self.out.parent.mkdir()
        (self.tmp / "o/mem.bin.json").write_text("stale")
        other = snapshot.take(p, targets, 2, "sceKernelCreateThread:2", self.out)
        self.assertEqual(other, 1)
        self.assertEqual(p.breakpoints, {STUB_CREATE: True})
        self.assertEqual(p.calls, ["resume"] * 3 + ["regs", ("read", 0x08000000, 0x02000000, STUB_CREATE)])
        self.assertEqual(self.out.stat().st_size, 0x02000000)
        side = (self.tmp / "o/mem.bin.json").read_text()
        self.assertEqual(side, '{"base":"0x08000000","size":33554432,"event":"sceKernelCreateThread:2",'
                               '"side":"ppsspp","nid":"0x446D8DE6","ra":"0x08A8BE30"}\n')
        self.assertEqual(json.loads(side)["nid"], "0x%08X" % CREATE)

    def test_two_stubs_import_the_same_nid(self):
        # A second library also imports sceKernelCreateThread: its stub is the
        # third. Both get a breakpoint, and stops on either count towards K.
        elf = self.tmp / "two.elf"
        elf.write_bytes(make_module_elf([("ThreadManForUser", [0x12345678, CREATE]), ("Other", [CREATE])]))
        stubs = imports.stubs(imports.load(elf), None, None)
        second = MODULE_STUBS + 16
        targets = snapshot.resolve(stubs, None, "sceKernelCreateThread")
        self.assertEqual(targets, {STUB_CREATE: CREATE, second: CREATE})
        self.assertEqual(snapshot.resolve(stubs, CREATE, None), targets)
        p = FakeSession([second, OTHER, STUB_CREATE, second])
        self.assertEqual(snapshot.take(p, targets, 3, "sceKernelCreateThread:3", self.out), 1)
        self.assertEqual(p.breakpoints, {STUB_CREATE: True, second: True})
        self.assertEqual(p.calls[-1], ("read", 0x08000000, 0x02000000, second))
        self.assertEqual(json.loads((self.tmp / "o/mem.bin.json").read_text())["nid"], "0x%08X" % CREATE)

    def test_take_failure_leaves_no_stale_snapshot(self):
        self.out.parent.mkdir()
        self.out.write_bytes(b"old")
        (self.tmp / "o/mem.bin.json").write_text("{}")
        p = FakeSession([STUB_CREATE, PpssppError("timed out after 1 s waiting for cpu.stepping")])
        with self.assertRaisesRegex(SnapshotError, "1 of 2 calls seen"):
            snapshot.take(p, {STUB_CREATE: CREATE}, 2, "sceKernelCreateThread:2", self.out)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.tmp / "o/mem.bin.json").exists())

    def test_write_failure_leaves_nothing(self):
        targets = {STUB_CREATE: CREATE}
        with mock.patch.object(pathlib.Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                snapshot.take(FakeSession([STUB_CREATE]), targets, 1, "sceKernelCreateThread:1", self.out)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.tmp / "o/mem.bin.json").exists())

    def test_missing_ra(self):
        p = FakeSession([STUB_CREATE])
        p.no_ra = True
        with self.assertRaisesRegex(SnapshotError, "PPSSPP gave no ra register"):
            snapshot.take(p, {STUB_CREATE: CREATE}, 1, "x:1", self.out)
        self.assertFalse(self.out.exists())

    def test_deadline(self):
        with self.assertRaisesRegex(SnapshotError, r"timed out after 0 s \(0 of 1 calls seen"):
            snapshot.take(FakeSession([]), {STUB_CREATE: CREATE}, 1, "x:1", self.out, timeout=0)

    def test_errors_name_the_first_and_last_other_stop(self):
        p = FakeSession([OTHER, OTHER + 8, OTHER + 4, PpssppError("timed out after 1 s waiting for cpu.stepping")])
        with self.assertRaisesRegex(SnapshotError, "3 other stops; first at 0x08A00000, last at 0x08A00004"):
            snapshot.take(p, {STUB_CREATE: CREATE}, 1, "x:1", self.out)

    def test_stuck_guest(self):
        # 99 stops at OTHER, one elsewhere, then 100 at OTHER: the run stops at the 100th.
        stops = [OTHER] * 99 + [OTHER + 4] + [OTHER] * 100 + [STUB_CREATE]
        p = FakeSession(stops)
        with self.assertRaisesRegex(SnapshotError, "guest stuck/faulting at 0x08A00000"):
            snapshot.take(p, {STUB_CREATE: CREATE}, 1, "x:1", self.out)
        self.assertEqual(p.stops, [STUB_CREATE])
        # A target stop in between resets the count.
        p = FakeSession([OTHER] * 99 + [STUB_CREATE] + [OTHER] * 99 + [STUB_CREATE])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(snapshot.take(p, {STUB_CREATE: CREATE}, 2, "x:2", self.out), 198)

    def test_refuses_to_overwrite_inputs(self):
        elf, iso = self.tmp / "module.elf", self.tmp / "game.iso"
        iso.write_bytes(b"iso")
        (self.tmp / "sub").mkdir()
        (self.tmp / "link.bin").hardlink_to(iso)
        (self.tmp / "module.json").write_bytes(b"x")
        cases = [(elf, str(elf)),                                  # -o is the ELF
                 (elf, str(self.tmp / "sub/../module.elf")),       # the same, spelled differently
                 (elf, str(self.tmp / "link.bin")),                # a hard link to the ISO
                 (self.tmp / "module.json", str(self.tmp / "module"))]   # the sidecar is the ELF
        for elf_arg, out in cases:
            with self.subTest(out):
                err = io.StringIO()
                argv = ["--iso", str(iso), "--elf", str(elf_arg), "--at", "sceKernelCreateThread:1", "-o", out]
                with contextlib.redirect_stderr(err), mock.patch.object(snapshot.Ppsspp, "launch") as launch:
                    self.assertEqual(snapshot.main(argv), 2, err.getvalue())
                launch.assert_not_called()
                self.assertIn("refusing -o", err.getvalue())
        self.assertTrue(elf.exists() and iso.exists() and (self.tmp / "module.json").exists())

    def test_main_closes_ppsspp_when_take_fails(self):
        p = FakeSession([PpssppError("PPSSPP exited")])
        p.close = mock.Mock()
        argv = ["--iso", "game.iso", "--elf", str(self.tmp / "module.elf"),
                "--at", "sceKernelCreateThread:1", "-o", str(self.out)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(snapshot.Ppsspp, "launch", return_value=p):
            self.assertEqual(snapshot.main(argv), 1)
        p.close.assert_called_once()
        self.assertIn("snapshot: PPSSPP exited", err.getvalue())

    def test_main_success(self):
        p = FakeSession([STUB_CREATE])
        p.close = mock.Mock()
        argv = ["--iso", "game.iso", "--elf", str(self.tmp / "module.elf"),
                "--at", "sceKernelCreateThread:1", "-o", str(self.out)]
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(snapshot.Ppsspp, "launch", return_value=p):
            self.assertEqual(snapshot.main(argv), 0)
        p.close.assert_called_once()
        self.assertIn("written to", out.getvalue())
        self.assertTrue((self.tmp / "o/mem.bin.json").exists())

    def test_main_removes_stale_outputs_before_failing_early(self):
        self.out.parent.mkdir()
        self.out.write_bytes(b"old")
        (self.tmp / "o/mem.bin.json").write_text("{}")
        argv = ["--iso", "game.iso", "--elf", str(self.tmp / "module.elf"),
                "--at", "sceIoOpen:1", "-o", str(self.out)]        # not imported: fails before launch
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(snapshot.Ppsspp, "launch") as launch:
            self.assertEqual(snapshot.main(argv), 1)
        launch.assert_not_called()
        self.assertIn("does not import sceIoOpen", err.getvalue())
        self.assertFalse(self.out.exists())
        self.assertFalse((self.tmp / "o/mem.bin.json").exists())

    def test_main_os_error(self):
        argv = ["--iso", "game.iso", "--elf", str(self.tmp / "module.elf"),
                "--at", "sceKernelCreateThread:1", "-o", str(self.out)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(snapshot.Ppsspp, "launch", side_effect=PermissionError("denied")):
            self.assertEqual(snapshot.main(argv), 1)
        self.assertIn("snapshot: denied", err.getvalue())

    def test_bad_event_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            snapshot.main(["--iso", "x", "--elf", "y", "--at", "sceFoo:0", "-o", "z"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
