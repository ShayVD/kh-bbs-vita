"""snapshot.py's event parsing, stub lookup and stop counting, with a fake
PPSSPP session and a synthetic ELF. Every value is made up."""
import contextlib, io, json, pathlib, sys, tempfile, unittest

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
        return {"pc": self.pc, "ra": 0x08A8BE30}

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
        for bad in ("", "sceFoo", "sceFoo:", "sceFoo:0", ":1", "0x:1", "0xZZ:1", "0x123456789:1",
                    "sceFoo:-1", "sceFoo:1x", "1abc:1", "sce Foo:1", "sceFoo:1:2"):
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

    def test_take_failure_leaves_no_stale_snapshot(self):
        self.out.parent.mkdir()
        self.out.write_bytes(b"old")
        (self.tmp / "o/mem.bin.json").write_text("{}")
        p = FakeSession([STUB_CREATE, PpssppError("timed out after 1 s waiting for cpu.stepping")])
        with self.assertRaisesRegex(SnapshotError, "1 of 2 calls seen"):
            snapshot.take(p, {STUB_CREATE: CREATE}, 2, "sceKernelCreateThread:2", self.out)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.tmp / "o/mem.bin.json").exists())

    def test_bad_event_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            snapshot.main(["--iso", "x", "--elf", "y", "--at", "sceFoo:0", "-o", "z"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
