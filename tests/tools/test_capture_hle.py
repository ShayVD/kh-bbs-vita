"""capture_hle.parse and imports.py on synthetic log lines and a synthetic
ELF. Every value is made up; the line format is docs/oracle.md's."""
import contextlib, io, json, pathlib, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from elfgen import MODULE_STUBS, MODULE_TEXT, make_module_elf  # noqa: E402
from import_report import nid_of  # noqa: E402
from oracle import capture_hle, imports  # noqa: E402

PREFIX = "00:12:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC="
ROOT, WORKER = 0x113, 0x122
DB = 0xDEADBEEF

STUBS = {
    0x08B30010: (0x1A2B3C4D, "sceAlphaGet"),
    0x08B30028: (0x2B3C4D5E, "sceKernelCreateThread"),
    0x08B30030: (0x3C4D5E6F, "sceKernelWaitSemaCB"),
    0x08B30048: (0x4D5E6F70, None),
    0x08B30050: (0x5E6F7081, "sceBetaSet"),
}


def S(pc, uid, ra, *args):
    args = list(args) + [DB] * (8 - len(args))
    return PREFIX + "%08x: S %s" % (pc, " ".join("%08x" % v for v in (pc, uid, ra, *args)))


def R(pc, uid, v0, v1=DB):
    return PREFIX + "%08x: R %08x %08x %08x %08x" % (pc, pc, uid, v0, v1)


def create_info(uid_dec, name):
    return ("00:12:118              I[SCEKERNEL]: HLE/sceKernelThread.cpp:1902 "
            f"{uid_dec}=sceKernelCreateThread({name}, 08a90400, 00000020, 16384, 80000000, 00000000)")


def parse(lines, names={ROOT: "root"}):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        recs = list(capture_hle.parse(lines, STUBS, names))
    return recs, err.getvalue()


class ParseTest(unittest.TestCase):
    def test_parse_lines(self):
        lines = [
            S(0x08B30010, ROOT, 0x08A90104, 0x40, 0x09FFF000),
            R(0x08A90104, ROOT, 0),
            S(0x08B30028, ROOT, 0x08A90230, 0x08B51200, 0x08A90400, 0x20, 0x4000,
              0x80000000, 0, 0x08B40000, 0x08B7A000),
            create_info(290, "worker"),
            R(0x08A90230, ROOT, 290, 0x08B60000),
            S(0x08B30030, ROOT, 0x08A90240, 290, 0, 0),         # blocks
            S(0x08B30048, WORKER, 0x08A90410),
            R(0x08A90410, WORKER, 0x1234ABCD, 0x00000005),      # 64-bit return
            R(0x08A90240, ROOT, 0),
            "00:12:120              N[BOOT]: UI/EmuScreen.cpp:391 Booted disc0:/sample.iso...",
        ]
        recs, err = parse(lines)
        self.assertEqual(err, "")
        self.assertEqual(recs[0], {
            "seq": 0, "nid": "0x1A2B3C4D", "name": "sceAlphaGet", "ra": "0x08A90104",
            "args": ["0x00000040", "0x09FFF000"] + [None] * 6,
            "ret": "0x00000000", "ret_hi": "0xDEADBEEF", "thread": "root"})
        self.assertEqual(recs[1]["args"], ["0x08B51200", "0x08A90400", "0x00000020", "0x00004000",
                                           "0x80000000", "0x00000000", "0x08B40000", "0x08B7A000"])
        self.assertEqual((recs[1]["ret"], recs[1]["ret_hi"]), ("0x00000122", "0x08B60000"))
        # The worker's call returns before root's blocking wait: completion order.
        self.assertEqual([(r["seq"], r["name"], r["thread"]) for r in recs],
                         [(0, "sceAlphaGet", "root"), (1, "sceKernelCreateThread", "root"),
                          (2, None, "worker"), (3, "sceKernelWaitSemaCB", "root")])
        self.assertEqual((recs[2]["nid"], recs[2]["ret"], recs[2]["ret_hi"]),
                         ("0x4D5E6F70", "0x1234ABCD", "0x00000005"))
        self.assertEqual(recs[3]["args"], ["0x00000122", "0x00000000", "0x00000000"] + [None] * 5)

    def test_callback_calls_come_before_their_wait(self):
        lines = [
            S(0x08B30030, ROOT, 0x08A90240, 7),                 # ...CB wait
            S(0x08B30050, ROOT, 0x08A95000, 1),                 # inside the callback
            R(0x08A95000, ROOT, 0),
            S(0x08B30010, ROOT, 0x08A95010),
            R(0x08A95010, ROOT, 3),
            R(0x08A90240, ROOT, 0),
        ]
        recs, _ = parse(lines)
        self.assertEqual([(r["seq"], r["name"], r["ra"]) for r in recs],
                         [(0, "sceBetaSet", "0x08A95000"), (1, "sceAlphaGet", "0x08A95010"),
                          (2, "sceKernelWaitSemaCB", "0x08A90240")])

    def test_return_closes_the_newest_call_with_its_ra(self):
        # An inner call that never returns stays open; the outer one still closes.
        lines = [
            S(0x08B30030, ROOT, 0x08A90240),
            S(0x08B30050, ROOT, 0x08A95000),
            R(0x08A90240, ROOT, 0),
        ]
        recs, _ = parse(lines)
        self.assertEqual([(r["name"], r["ret"]) for r in recs],
                         [("sceKernelWaitSemaCB", "0x00000000"), ("sceBetaSet", None)])

    def test_unmatched_return_is_reported_and_skipped(self):
        lines = [
            R(0x08A90104, ROOT, 0),                             # nothing open
            S(0x08B30010, ROOT, 0x08A90104),
            R(0x08A90104, WORKER, 0),                           # other thread
            R(0x08A90108, ROOT, 0),                             # other ra
            R(0x08A90104, ROOT, 5),
        ]
        recs, err = parse(lines)
        self.assertEqual([(r["seq"], r["ret"]) for r in recs], [(0, "0x00000005")])
        self.assertEqual(err.count("closes no open call"), 3)
        self.assertIn("R at 08a90104 on thread 0x122", err)

    def test_open_calls_come_last_in_call_order(self):
        lines = [
            S(0x08B30030, ROOT, 0x08A90240, 1),
            S(0x08B30048, WORKER, 0x08A90410),
            S(0x08B30050, ROOT, 0x08A95000),
            S(0x08B30010, WORKER, 0x08A90420),
            R(0x08A90420, WORKER, 9),
        ]
        recs, _ = parse(lines, {ROOT: "root", WORKER: "worker"})
        self.assertEqual([(r["seq"], r["ra"], r["ret"], r["ret_hi"]) for r in recs],
                         [(0, "0x08A90420", "0x00000009", "0xDEADBEEF"),
                          (1, "0x08A90240", None, None), (2, "0x08A90410", None, None),
                          (3, "0x08A95000", None, None)])

    def test_unknown_thread(self):
        recs, _ = parse([S(0x08B30010, 0x1F0, 0x08A90104), R(0x08A90104, 0x1F0, 0)])
        self.assertEqual(recs[0]["thread"], "uid:0x1F0")


class ImportsTest(unittest.TestCase):
    NIDS = [nid_of("sceKernelCreateMutex"), 0x12345678, nid_of("sceAlphaGet"), nid_of("sceBetaSet")]

    def setUp(self):
        d = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.elf = d / "module.elf"
        # Stub 0 is called twice, stub 3 never; one tail call into stub 2.
        self.elf.write_bytes(make_module_elf([("LibA", self.NIDS[:2]), ("LibB", self.NIDS[2:])],
                                             calls=[0, 1, 0, 2], tail_calls=[2]))
        self.names = d / "names.txt"
        self.names.write_text("# names\nsceKernelCreateMutex\n0x12345678 Pinned_12345678(role)\n")
        hle = d / "psprecomp/src/hle"
        hle.mkdir(parents=True)
        (hle / "a.c").write_text(
            f'psp_hle_register(0x{self.NIDS[2]:08X}, "LibB", "sceAlphaGet", f);\n'
            f'psp_hle_register(0x{self.NIDS[3]:08X}, "LibB", "notItsName", f);\n')
        self.psprecomp = d / "psprecomp"

    def test_stubs(self):
        st = imports.stubs(self.elf, self.psprecomp, self.names)
        self.assertEqual(st, {
            MODULE_STUBS: (self.NIDS[0], "sceKernelCreateMutex"),        # names file
            MODULE_STUBS + 8: (0x12345678, "Pinned_12345678(role)?"),    # pinned
            MODULE_STUBS + 16: (self.NIDS[2], "sceAlphaGet"),            # psprecomp
            MODULE_STUBS + 24: (self.NIDS[3], None)})                    # SHA-1 mismatch
        self.assertEqual(imports.stubs(self.elf, None, None)[MODULE_STUBS], (self.NIDS[0], None))

    def test_return_sites(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sites = imports.return_sites(self.elf, imports.stubs(self.elf, None, None))
        self.assertEqual(sites, {MODULE_TEXT + 8: MODULE_STUBS, MODULE_TEXT + 16: MODULE_STUBS + 8,
                                 MODULE_TEXT + 24: MODULE_STUBS, MODULE_TEXT + 32: MODULE_STUBS + 16})
        self.assertIn("1 tail calls", err.getvalue())


class FakeSession:
    """Stands in for a Ppsspp stopped at the entry: resume() 'runs the game'
    by writing log lines."""
    def __init__(self, log_path, lines):
        self.log_path, self.lines, self.proc = log_path, lines, None
        self.breakpoints, self.calls = {}, []

    def request(self, event, **params):
        self.calls.append(event)
        assert event == "hle.thread.list"
        return {"threads": [{"id": ROOT, "name": "root"}, {"id": 0x110, "name": "idle0"}]}

    def add_breakpoint(self, addr, enabled=True, log=False, condition=None, log_format=None):
        self.breakpoints[addr] = (enabled, log, condition, log_format)

    def resume(self):
        self.calls.append("resume")
        with open(self.log_path, "a") as f:
            f.write("".join(line + "\n" for line in self.lines))

    def pause(self):
        self.calls.append("pause")


class CaptureTest(unittest.TestCase):
    def test_capture_writes_khbbs_lines(self):
        d = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        elf = d / "module.elf"
        elf.write_bytes(make_module_elf([("LibA", [0x0A0B0C0D, 0x01020304])]))
        log = d / "log.txt"
        log.write_text("00:00:001              I[BOOT]: loading\n")
        stub0, ra0 = MODULE_STUBS, MODULE_TEXT + 8
        p = FakeSession(log, [S(stub0, ROOT, ra0, 0x06020010), R(ra0, ROOT, 0),
                              S(stub0 + 8, ROOT, MODULE_TEXT + 16, 1), R(MODULE_TEXT + 16, ROOT, 2),
                              S(stub0, 0x110, ra0)])
        out = d / "out/trace.jsonl"
        with contextlib.redirect_stdout(io.StringIO()) as so:
            self.assertEqual(capture_hle.capture(p, 2, out, elf, timeout=5), 2)
        self.assertEqual(p.calls, ["hle.thread.list", "resume", "pause"])
        self.assertEqual(p.breakpoints[stub0], (False, True, None, capture_hle.S_FORMAT))
        self.assertEqual(p.breakpoints[ra0], (False, True, "pc == ra", capture_hle.R_FORMAT))
        self.assertEqual(len(p.breakpoints), 4)
        lines = out.read_text().splitlines()
        self.assertEqual(lines[0], '{"seq":0,"nid":"0x0A0B0C0D","name":null,"ra":"0x%08X",'
                                   '"args":["0x06020010",null,null,null,null,null,null,null],'
                                   '"ret":"0x00000000","ret_hi":"0xDEADBEEF","thread":"root"}' % ra0)
        self.assertEqual([json.loads(x)["seq"] for x in lines], [0, 1])
        self.assertIn("wrote 2 records", so.getvalue())
        self.assertIn("1 still open", so.getvalue())


if __name__ == "__main__":
    unittest.main()
