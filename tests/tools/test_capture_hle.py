"""capture_hle and imports.py on synthetic log lines and a synthetic ELF.
Every value is made up; the line format is docs/oracle.md's."""
import collections, contextlib, io, json, pathlib, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from elfgen import MODULE_STUBS, MODULE_TEXT, make_module_elf  # noqa: E402
from import_report import nid_of  # noqa: E402
from oracle import capture_hle, imports  # noqa: E402
from oracle.capture_hle import CaptureError  # noqa: E402
from oracle.ppsspp import PpssppError  # noqa: E402

PREFIX = "00:12:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC="
ROOT, WORKER, IDLE = 0x113, 0x122, 0x110
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


def parse(lines, names=None):
    """(records, stderr text, stats)."""
    err, stats = io.StringIO(), collections.Counter()
    with contextlib.redirect_stderr(err):
        recs = list(capture_hle.parse(lines, STUBS, {ROOT: "root"} if names is None else names, stats))
    return recs, err.getvalue(), stats


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
        recs, err, stats = parse(lines)
        self.assertEqual(err, "")
        self.assertEqual(stats["open"], 0)
        # Unused argument slots become null; ret_hi is v1 as logged.
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

    def test_thread_name_with_comma(self):
        recs, _, _ = parse([create_info(290, "Sd, Update(1)"),
                            S(0x08B30010, WORKER, 0x08A90104), R(0x08A90104, WORKER, 0)])
        self.assertEqual(recs[0]["thread"], "Sd, Update(1)")

    def test_callback_calls_come_before_their_wait(self):
        lines = [
            S(0x08B30030, ROOT, 0x08A90240, 7),                 # ...CB wait
            S(0x08B30050, ROOT, 0x08A95000, 1),                 # inside the callback
            R(0x08A95000, ROOT, 0),
            S(0x08B30010, ROOT, 0x08A95010),
            R(0x08A95010, ROOT, 3),
            R(0x08A90240, ROOT, 0),
        ]
        recs, _, _ = parse(lines)
        self.assertEqual([(r["seq"], r["name"], r["ra"]) for r in recs],
                         [(0, "sceBetaSet", "0x08A95000"), (1, "sceAlphaGet", "0x08A95010"),
                          (2, "sceKernelWaitSemaCB", "0x08A90240")])

    def test_interrupt_on_idle_thread_nests_inside_its_open_call(self):
        # idle0 has a call open; an interrupt handler on idle0 calls the same
        # stub from the same site. Each R closes the newest call with its ra.
        lines = [
            S(0x08B30010, IDLE, 0x08A90104, 1),
            S(0x08B30050, IDLE, 0x08A97000, 2),                 # handler
            R(0x08A97000, IDLE, 0),
            S(0x08B30010, IDLE, 0x08A90104, 3),                 # handler, same site
            R(0x08A90104, IDLE, 0x33),
            R(0x08A90104, IDLE, 0x11),
        ]
        recs, err, _ = parse(lines, {IDLE: "idle0"})
        self.assertEqual(err, "")
        self.assertEqual([(r["name"], r["args"][0], r["ret"], r["thread"]) for r in recs],
                         [("sceBetaSet", "0x00000002", "0x00000000", "idle0"),
                          ("sceAlphaGet", "0x00000003", "0x00000033", "idle0"),
                          ("sceAlphaGet", "0x00000001", "0x00000011", "idle0")])

    def test_return_closes_the_newest_call_with_its_ra(self):
        # An inner call that never returns stays open; the outer one still closes.
        lines = [
            S(0x08B30030, ROOT, 0x08A90240),
            S(0x08B30050, ROOT, 0x08A95000),
            R(0x08A90240, ROOT, 0),
        ]
        recs, _, stats = parse(lines)
        self.assertEqual([(r["name"], r["ret"]) for r in recs],
                         [("sceKernelWaitSemaCB", "0x00000000"), ("sceBetaSet", None)])
        self.assertEqual(stats["open"], 1)

    def test_unmatched_return_is_reported_and_skipped(self):
        lines = [
            R(0x08A90104, ROOT, 0),                             # nothing open
            S(0x08B30010, ROOT, 0x08A90104),
            R(0x08A90104, WORKER, 0),                           # other thread
            R(0x08A90108, ROOT, 0),                             # other ra
            R(0x08A90104, ROOT, 5),
        ]
        recs, err, stats = parse(lines)
        self.assertEqual([(r["seq"], r["ret"]) for r in recs], [(0, "0x00000005")])
        self.assertEqual(err.count("closes no open call"), 3)
        self.assertIn("R at 08a90104 on thread 0x122", err)
        self.assertEqual(stats["unmatched"], 3)

    def test_malformed_and_off_stub_lines_are_counted_and_skipped(self):
        good_s = S(0x08B30010, ROOT, 0x08A90104, 1)
        lines = [
            good_s.rsplit(" ", 1)[0],                           # S with 10 fields
            R(0x08A90104, ROOT, 0).rsplit(" ", 1)[0],           # R with 3 fields
            PREFIX + "08b30010: S 08b30010 00000113 zz",
            S(0x08B30018, ROOT, 0x08A90100),                    # not a stub
            good_s + "  ",                                      # trailing spaces are fine
            R(0x08A90104, ROOT, 7) + " ",
        ]
        recs, err, stats = parse(lines)
        self.assertEqual([(r["args"][0], r["ret"]) for r in recs], [("0x00000001", "0x00000007")])
        self.assertEqual((stats["malformed"], stats["not_stub"], stats["unmatched"]), (3, 1, 0))
        self.assertEqual(err.count("malformed breakpoint line"), 3)
        self.assertIn("S at 08b30018 is not an import stub", err)

    def test_only_the_first_problems_are_printed(self):
        recs, err, stats = parse([R(0x08A90104 + 4 * i, ROOT, 0) for i in range(7)])
        self.assertEqual(recs, [])
        self.assertEqual(stats["unmatched"], 7)
        self.assertEqual(len(err.splitlines()), capture_hle.REPORT_FIRST)

    def test_open_calls_come_last_in_call_order(self):
        lines = [
            S(0x08B30030, ROOT, 0x08A90240, 1),
            S(0x08B30048, WORKER, 0x08A90410),
            S(0x08B30050, ROOT, 0x08A95000),
            S(0x08B30010, WORKER, 0x08A90420),
            R(0x08A90420, WORKER, 9),
        ]
        recs, _, stats = parse(lines, {ROOT: "root", WORKER: "worker"})
        self.assertEqual([(r["seq"], r["ra"], r["ret"], r["ret_hi"]) for r in recs],
                         [(0, "0x08A90420", "0x00000009", "0xDEADBEEF"),
                          (1, "0x08A90240", None, None), (2, "0x08A90410", None, None),
                          (3, "0x08A95000", None, None)])
        self.assertEqual(stats["open"], 3)

    def test_unknown_thread(self):
        recs, _, _ = parse([S(0x08B30010, 0x1F0, 0x08A90104), R(0x08A90104, 0x1F0, 0)])
        self.assertEqual(recs[0]["thread"], "uid:0x1F0")


class ImportsTest(unittest.TestCase):
    NIDS = [nid_of("sceKernelCreateMutex"), 0x12345678, nid_of("sceAlphaGet"), nid_of("sceBetaSet")]
    OTHER = 0x08805000                       # a jal target that is not a stub

    def setUp(self):
        d = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.elf = d / "module.elf"
        # Stub 0 is called twice, stub 3 never; one tail call into stub 2.
        self.elf.write_bytes(make_module_elf([("LibA", self.NIDS[:2]), ("LibB", self.NIDS[2:])],
                                             calls=[0, 1, 0, 2], tail_calls=[2],
                                             other_calls=[self.OTHER]))
        self.names = d / "names.txt"
        self.names.write_text("# names\nsceKernelCreateMutex\n0x12345678 Pinned_12345678(role)\n")
        hle = d / "psprecomp/src/hle"
        hle.mkdir(parents=True)
        (hle / "a.c").write_text(
            f'psp_hle_register(0x{self.NIDS[2]:08X}, "LibB", "sceAlphaGet", f);\n'
            f'psp_hle_register(0x{self.NIDS[3]:08X}, "LibB", "notItsName", f);\n')
        self.psprecomp = d / "psprecomp"

    def test_stubs(self):
        module = imports.load(self.elf)
        st = imports.stubs(module, self.psprecomp, self.names)
        self.assertEqual(st, {
            MODULE_STUBS: (self.NIDS[0], "sceKernelCreateMutex"),        # names file
            MODULE_STUBS + 8: (0x12345678, "Pinned_12345678(role)?"),    # pinned
            MODULE_STUBS + 16: (self.NIDS[2], "sceAlphaGet"),            # psprecomp
            MODULE_STUBS + 24: (self.NIDS[3], None)})                    # SHA-1 mismatch
        self.assertEqual(imports.stubs(module, None, None)[MODULE_STUBS], (self.NIDS[0], None))

    def test_return_sites(self):
        module = imports.load(self.elf)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sites = imports.return_sites(module, imports.stubs(module, None, None))
        # The jal to OTHER (at MODULE_TEXT + 32) has no site.
        self.assertEqual(sites, {MODULE_TEXT + 8: MODULE_STUBS, MODULE_TEXT + 16: MODULE_STUBS + 8,
                                 MODULE_TEXT + 24: MODULE_STUBS, MODULE_TEXT + 32: MODULE_STUBS + 16})
        self.assertIn("1 tail calls", err.getvalue())

    def test_load_rejects_a_non_elf(self):
        with self.assertRaisesRegex(ValueError, "not an ELF"):
            imports.load(self.names)


class FakeProc:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeSession:
    """Stands in for a Ppsspp stopped at the entry: resume() 'runs the game'
    by writing `text` to the log."""
    def __init__(self, log_path, text, proc=None):
        self.log_path, self.text, self.proc = log_path, text, proc
        self.breakpoints, self.calls = {}, []

    def request(self, event, **params):
        self.calls.append(event)
        assert event == "hle.thread.list"
        return {"threads": [{"id": ROOT, "name": "root"}, {"id": IDLE, "name": "idle0"}]}

    def add_breakpoint(self, addr, enabled=True, log=False, condition=None, log_format=None):
        self.breakpoints[addr] = (enabled, log, condition, log_format)

    def resume(self):
        self.calls.append("resume")
        with open(self.log_path, "a") as f:
            f.write(self.text)

    def pause(self):
        self.calls.append("pause")


class CaptureTest(unittest.TestCase):
    STUB0, RA0 = MODULE_STUBS, MODULE_TEXT + 8
    STUB1, RA1 = MODULE_STUBS + 8, MODULE_TEXT + 16

    def setUp(self):
        self.d = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        elf = self.d / "module.elf"
        elf.write_bytes(make_module_elf([("LibA", [0x0A0B0C0D, 0x01020304])]))
        module = imports.load(elf)
        self.stubs = imports.stubs(module)
        self.sites = imports.return_sites(module, self.stubs)
        self.log = self.d / "log.txt"
        self.log.write_text("00:00:001              I[BOOT]: loading\n")
        self.out = self.d / "out/trace.jsonl"

    def capture(self, p, n, timeout=5):
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            got = capture_hle.capture(p, n, self.out, self.stubs, self.sites, timeout, poll=0.01)
        return got, so.getvalue(), se.getvalue()

    def test_capture_writes_khbbs_lines(self):
        lines = [S(self.STUB0, ROOT, self.RA0, 0x07010020), R(self.RA0, ROOT, 0),
                 S(self.STUB1, ROOT, self.RA1, 1), R(self.RA1, ROOT, 2),
                 S(self.STUB0, IDLE, self.RA0), R(self.RA0, IDLE, 4)]
        p = FakeSession(self.log, "".join(x + "\n" for x in lines))
        got, so, _ = self.capture(p, 2)
        self.assertEqual(got, 2)
        self.assertEqual(p.calls, ["hle.thread.list", "resume", "pause"])
        self.assertEqual(p.breakpoints[self.STUB0], (False, True, None, capture_hle.S_FORMAT))
        self.assertEqual(p.breakpoints[self.RA0], (False, True, "pc == ra", capture_hle.R_FORMAT))
        self.assertEqual(len(p.breakpoints), 4)
        out = self.out.read_text().splitlines()
        self.assertEqual(out[0], '{"seq":0,"nid":"0x0A0B0C0D","name":null,"ra":"0x%08X",'
                                 '"args":["0x07010020",null,null,null,null,null,null,null],'
                                 '"ret":"0x00000000","ret_hi":"0xDEADBEEF","thread":"root"}' % self.RA0)
        self.assertEqual([json.loads(x)["seq"] for x in out], [0, 1])
        # Tracing stops at the second return, so idle0's call was never read.
        self.assertIn("wrote 2 records (0 of them open)", so)
        self.assertIn("0 calls were open when tracing stopped", so)

    def test_timeout_writes_what_there_is(self):
        # The last line has no newline yet, so the second call is still open.
        text = "".join(x + "\n" for x in [S(self.STUB0, ROOT, self.RA0, 1), R(self.RA0, ROOT, 0),
                                          S(self.STUB1, ROOT, self.RA1, 2)]) + R(self.RA1, ROOT, 3)
        p = FakeSession(self.log, text)
        got, so, se = self.capture(p, 3, timeout=0.2)
        self.assertEqual(got, 1)
        self.assertEqual(p.calls[-1], "pause")
        out = [json.loads(x) for x in self.out.read_text().splitlines()]
        self.assertEqual([(r["seq"], r["ret"]) for r in out], [(0, "0x00000000"), (1, None)])
        self.assertIn("wrote 2 records (1 of them open)", so)
        self.assertIn("timed out after 0.2 s with 1 of 3 calls returned", se)

    def test_ppsspp_exit_while_tracing(self):
        p = FakeSession(self.log, S(self.STUB0, ROOT, self.RA0) + "\n", FakeProc(returncode=-9))
        with self.assertRaisesRegex(CaptureError, "PPSSPP exited with -9"):
            self.capture(p, 2)
        self.assertNotIn("pause", p.calls)
        self.assertFalse(self.out.exists())

    def test_pause_failure_keeps_the_original_error(self):
        class BrokenPause(FakeSession):
            def resume(self):
                self.calls.append("resume")

            def pause(self):
                raise PpssppError("pause broke")

        p = BrokenPause(self.d / "missing-log.txt", "")
        with self.assertRaises(FileNotFoundError) as cm:
            self.capture(p, 2)
        self.assertIn("pausing failed too: PpssppError('pause broke')", cm.exception.__notes__)


if __name__ == "__main__":
    unittest.main()
