"""trace_diff.py on synthetic traces."""
import collections, contextlib, io, json, pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from import_report import nid_of  # noqa: E402
from oracle import sigs, trace_diff  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "oracle" / "trace_diff.py"
Result = collections.namedtuple("Result", "returncode stdout stderr")


def rec(seq, nid, args, ret, **kw):
    r = {"seq": seq, "nid": f"0x{nid:08X}", "name": None, "ra": "0x08A8BD7C",
         "args": [f"0x{a:08X}" for a in args] + ["0x00000000"] * (8 - len(args)),
         "ret": f"0x{ret:08X}", "ret_hi": "0x00000000"}
    r.update(kw)
    return r


CREATE, START, DELAY = 0x446D8DE6, 0xF475845D, 0xCEADEB47
RESUME_INTR = nid_of("sceKernelCpuResumeIntr")      # void
TIME_WIDE = nid_of("sceKernelGetSystemTimeWide")    # 64-bit
THREAD_ID = nid_of("sceKernelGetThreadId")          # u
IO_OPEN, IO_CLOSE, IO_READ = nid_of("sceIoOpen"), nid_of("sceIoClose"), nid_of("sceIoRead")
UNKNOWN = 0x12345678                                # not in hle_sigs.txt


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write_text(self, text, suffix=".jsonl"):
        path = self.tmp / f"{len(list(self.tmp.iterdir()))}{suffix}"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def write(self, recs):
        return self.write_text("".join(json.dumps(r) + "\n" for r in recs))


class TraceDiff(TempDir):
    def run_paths(self, paths, *opts):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = trace_diff.main([*paths, *opts])
            except SystemExit as e:                     # argparse errors
                code = e.code
        return Result(code, out.getvalue(), err.getvalue())

    def diff(self, a, b, *opts):
        return self.run_paths([self.write(a), self.write(b)], *opts)

    def test_identical(self):
        t = [rec(0, CREATE, [1, 2], 0x40000), rec(1, START, [0x40000], 0)]
        r = self.diff(t, t)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)

    def test_command_line(self):
        t = [rec(0, CREATE, [1, 2], 0x40000), rec(1, START, [0x40000], 0)]
        u = [rec(0, CREATE, [1, 2], 0x40000), rec(1, START, [0x40001], 0)]
        for b, code in ((t, 0), (u, 1)):
            r = subprocess.run([sys.executable, str(TOOL), self.write(t), self.write(b)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, str(TOOL), self.write(t), self.write_text("{x\n")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("trace_diff:", r.stderr)

    def test_first_divergence_named(self):
        a = [rec(0, CREATE, [1, 2], 0x40000), rec(1, DELAY, [100], 0)]
        b = [rec(0, CREATE, [1, 2], 0x40000), rec(1, DELAY, [200], 0)]
        r = self.diff(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1", r.stdout)
        self.assertIn("args[0]", r.stdout)

    def test_uid_mapping(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, START, [0x40000], 0)]
        b = [rec(0, CREATE, [1], 0x04A1B2C3), rec(1, START, [0x04A1B2C3], 0)]
        self.assertEqual(self.diff(a, b).returncode, 1)
        self.assertEqual(self.diff(a, b, "--map-uids").returncode, 0)

    def test_uid_map_applies_to_later_returns(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, THREAD_ID, [], 0x40000), rec(2, UNKNOWN, [], 0x40000)]
        b = [rec(0, CREATE, [1], 0x114), rec(1, THREAD_ID, [], 0x114), rec(2, UNKNOWN, [], 0x114)]
        r = self.diff(a, b, "--map-uids")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("1 uid pairs learned", r.stdout)

    def test_open_uid_call_learns_nothing(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, START, [0x40000], 0)]
        b = [dict(rec(0, CREATE, [1], 0), ret=None), rec(1, START, [0x114], 0)]
        r = self.diff(a, b, "--map-uids")
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1", r.stdout)
        self.assertIn("differing: args[0]", r.stdout)

    def test_uid_map_forgets_freed_values(self):
        # A reuses fd 3 after closing it; B hands out a new fd 5.
        a = [rec(0, IO_OPEN, [1], 3), rec(1, IO_CLOSE, [3], 0), rec(2, IO_OPEN, [1], 3),
             rec(3, IO_READ, [3, 0x100, 4], 4)]
        b = [rec(0, IO_OPEN, [1], 4), rec(1, IO_CLOSE, [4], 0), rec(2, IO_OPEN, [1], 5),
             rec(3, IO_READ, [5, 0x100, 4], 4)]
        r = self.diff(a, b, "--map-uids")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 uid pairs learned", r.stdout)
        b[1]["ret"] = "0x80010009"                     # a failed close frees nothing
        r = self.diff(a, b, "--map-uids", "--ignore-arg", "sceIoClose:ret")
        self.assertEqual(r.returncode, 1)
        self.assertIn("A 0x00000003 is already mapped from B 0x00000004", r.stdout)

    def test_null_fields_are_not_compared(self):
        a = [rec(0, CREATE, [1], 0x40000)]
        b = [rec(0, CREATE, [1], 0x40000)]
        b[0]["ret"] = None
        self.assertEqual(self.diff(a, b).returncode, 0)

    def test_per_thread_alignment(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="sound")]
        b = [rec(0, DELAY, [2], 0, thread="sound"), rec(1, DELAY, [1], 0, thread="main")]
        self.assertEqual(self.diff(a, b).returncode, 0)

    def test_limit(self):
        a = [rec(0, CREATE, [1], 1), rec(1, DELAY, [1], 0)]
        b = [rec(0, CREATE, [1], 1), rec(1, DELAY, [9], 0)]
        self.assertEqual(self.diff(a, b, "-n", "1").returncode, 0)

    def test_limit_must_be_positive(self):
        t = [rec(0, DELAY, [1], 0)]
        for n in ("0", "-1", "x"):
            self.assertEqual(self.diff(t, t, "-n", n).returncode, 2, n)

    def test_limit_larger_than_both_traces(self):
        t = [rec(0, DELAY, [1], 0), rec(1, DELAY, [2], 0)]
        r = self.diff(t, t, "-n", "10")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)
        r = self.diff(t, t[:1], "-n", "10")           # both ended inside the window
        self.assertEqual(r.returncode, 1)
        self.assertIn("differing: missing", r.stdout)

    def test_signature_limits_arity(self):
        a = [rec(0, DELAY, [100, 0, 0, 0, 0, 0x1111], 0)]
        b = [rec(0, DELAY, [100, 0, 0, 0, 0, 0x2222], 0)]
        self.assertEqual(self.diff(a, b).returncode, 0)
        a[0]["nid"] = b[0]["nid"] = f"0x{UNKNOWN:08X}"
        r = self.diff(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("args[5]", r.stdout)

    def test_void_return_not_compared(self):
        r = self.diff([rec(0, RESUME_INTR, [1], 0x1234)], [rec(0, RESUME_INTR, [1], 0x5678)])
        self.assertEqual(r.returncode, 0)

    def test_ret_hi_compared_for_64_bit_returns_only(self):
        a = [rec(0, TIME_WIDE, [], 0x100, ret_hi="0x00000001")]
        b = [rec(0, TIME_WIDE, [], 0x100, ret_hi="0x00000002")]
        r = self.diff(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("differing: ret_hi", r.stdout)
        self.assertEqual(self.diff(a, b, "--ignore-arg", "sceKernelGetSystemTimeWide:ret_hi").returncode, 0)
        a[0]["nid"] = b[0]["nid"] = f"0x{DELAY:08X}"
        self.assertEqual(self.diff(a, b).returncode, 0)
        a[0]["nid"] = b[0]["nid"] = f"0x{UNKNOWN:08X}"
        self.assertEqual(self.diff(a, b).returncode, 0)

    def test_compare_ra(self):
        a = [rec(0, DELAY, [1], 0)]
        b = [rec(0, DELAY, [1], 0, ra="0x08A90000")]
        self.assertEqual(self.diff(a, b).returncode, 0)
        r = self.diff(a, b, "--compare-ra")
        self.assertEqual(r.returncode, 1)
        self.assertIn("differing: ra", r.stdout)

    def test_missing_signature_hint(self):
        r = self.diff([rec(0, UNKNOWN, [1], 0)], [rec(0, UNKNOWN, [2], 0)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("no signature for 0x12345678 in hle_sigs.txt", r.stdout)
        r = self.diff([rec(0, DELAY, [1], 0)], [rec(0, DELAY, [2], 0)])
        self.assertNotIn("no signature", r.stdout)

    def test_names_come_from_the_table(self):
        r = self.diff([rec(0, DELAY, [1], 0)], [rec(0, DELAY, [2], 0, name="sceKernelDelayThread?")])
        self.assertIn('"name": "sceKernelDelayThread"', r.stdout)
        self.assertIn('"name": "sceKernelDelayThread?"', r.stdout)

    def test_context_keeps_every_argument_slot(self):
        lseek = nid_of("sceIoLseek")
        a = [rec(0, lseek, [3, 7, 0x10, 0, 1], 0x10), rec(1, UNKNOWN, [1], 0), rec(2, DELAY, [1], 0)]
        b = [dict(a[0]), dict(a[1], args=["0x00000001", None] + ["0x00000000"] * 6), rec(2, DELAY, [2], 0)]
        r = self.diff(a, b)
        self.assertIn("sceIoLseek(0x00000003, _, 0x00000010, 0x00000000, 0x00000001) = 0x00000010:0x00000000",
                      r.stdout)
        self.assertIn("0x12345678(0x00000001, null, 0x00000000,", r.stdout)

    def test_ignore_arg(self):
        a = [rec(0, DELAY, [1], 0), rec(1, CREATE, [1, 2], 0x40000)]
        b = [rec(0, DELAY, [1], 0x80020001), rec(1, CREATE, [1, 3], 0x40000)]
        self.assertEqual(self.diff(a, b, "--ignore-arg", "sceKernelDelayThread:ret").returncode, 1)
        r = self.diff(a, b, "--ignore-arg", "sceKernelDelayThread:ret", "--ignore-arg", "0x446D8DE6:1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")
        self.assertEqual(self.diff(a, b, "--ignore-arg", "sceKernelDelayThread").returncode, 2)
        self.assertEqual(self.diff(a, b, "--ignore-arg", "sceKernelDelayThread:8").returncode, 2)

    def test_ignore_arg_for_an_unknown_nid_warns(self):
        t = [rec(0, DELAY, [1], 0), rec(1, UNKNOWN, [1], 0)]
        r = self.diff(t, t, "--ignore-arg", "0x12345678:0")      # in a trace
        self.assertEqual(r.stderr, "")
        r = self.diff(t, t, "--ignore-arg", "0x0BADF00D:0")
        self.assertEqual(r.returncode, 0)
        self.assertIn("warning: --ignore-arg NID 0x0BADF00D", r.stderr)

    def test_one_stream_unless_both_sides_have_threads(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="sound")]
        b = [rec(0, DELAY, [2], 0), rec(1, DELAY, [1], 0)]
        r = self.diff(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 0 (A seq 0, B seq 0)", r.stdout)
        b = [rec(7, DELAY, [2], 0, thread="sound"), rec(8, DELAY, [9], 0, thread="main")]
        r = self.diff(a, b)
        self.assertIn("first divergence at record 0 of thread main (A seq 0, B seq 8)", r.stdout)

    def test_window(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="main"),
             rec(2, DELAY, [3], 0, thread="sound")]
        b = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [3], 0, thread="sound"),
             rec(2, DELAY, [4], 0, thread="sound")]
        r = self.diff(a, b, "-n", "3")                 # both traces go on past the window
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)
        self.assertIn("B's thread main ends at record 1, beyond the compared window", r.stdout)
        self.assertIn("A's thread sound ends at record 1, beyond the compared window", r.stdout)
        r = self.diff(a, b[:1], "-n", "3")             # B really ended
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1 of thread main", r.stdout)
        self.assertIn("differing: missing", r.stdout)
        r = self.diff(a[:2], b, "-n", "3")             # A really ended
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 0 of thread sound (A seq -, B seq 1)", r.stdout)
        self.assertEqual(self.diff(a, b).returncode, 1)  # no -n: both traces are whole

    def test_uid_map_must_stay_consistent(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, CREATE, [2], 0x40001)]
        b = [rec(0, CREATE, [1], 0x114), rec(1, CREATE, [2], 0x114)]
        r = self.diff(a, b, "--map-uids")
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1", r.stdout)
        self.assertIn("B 0x00000114 already maps to A 0x00040000", r.stdout)
        b[1]["ret"] = "0x80020001"                     # error codes are never learned
        self.assertIn("no UID pair", self.diff(a, b, "--map-uids").stdout)

    def test_uid_map_ignores_equal_values(self):
        # A value that happens to equal a B uid still matches itself.
        a = [rec(0, CREATE, [1], 0x40000), rec(1, DELAY, [0x114], 0)]
        b = [rec(0, CREATE, [1], 0x114), rec(1, DELAY, [0x114], 0)]
        self.assertEqual(self.diff(a, b, "--map-uids").returncode, 0)

    def test_bad_input(self):
        good = self.write([rec(0, DELAY, [1], 0)])
        bad = self.write_text(json.dumps(rec(0, DELAY, [1], 0)) + "\n{not json\n")
        records = {"args not a list": dict(rec(0, DELAY, [1], 0), args="0x1"),
                   "nid not a string": dict(rec(0, DELAY, [1], 0), nid=5),
                   "thread not a string": dict(rec(0, DELAY, [1], 0), thread=7),
                   "arg not a string": dict(rec(0, DELAY, [1], 0), args=[1] * 8),
                   "not an object": [1, 2]}
        cases = [[good, bad], [bad, good], [good, str(self.tmp / "nonexistent.jsonl")],
                 *([self.write([r]), good] for r in records.values()),
                 [self.write_text(""), self.write_text("\n")]]
        for paths in cases:
            r = self.run_paths(paths)
            self.assertEqual(r.returncode, 2, (paths, r.stdout))
            self.assertIn("trace_diff:", r.stderr)
        self.assertIn("both traces are empty", r.stderr)

    def test_values_must_be_8_hex_digits(self):
        good = self.write([rec(0, DELAY, [1], 0)])
        for v in (" 0x1 ", "1_0", "-0x1", "0x1", "0x0000000G", "0x000000001", "00000001"):
            r = self.run_paths([self.write([dict(rec(0, DELAY, [1], 0), ret=v)]), good])
            self.assertEqual(r.returncode, 2, v)
            self.assertIn("8-digit hex", r.stderr)

    def test_non_utf8_input(self):
        path = self.tmp / "latin1.jsonl"
        path.write_bytes(json.dumps(rec(0, DELAY, [1], 0)).encode() + b"\n\xff\xfe\n")
        r = self.run_paths([str(path), self.write([rec(0, DELAY, [1], 0)])])
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read", r.stderr)

    def test_unfinished_last_line_is_dropped(self):
        t = [rec(0, DELAY, [1], 0)]
        cut = self.write_text(json.dumps(t[0]) + "\n" + json.dumps(rec(1, DELAY, [2], 0))[:30])
        r = self.run_paths([cut, self.write(t)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 of 1 records match", r.stdout)
        self.assertIn("dropped the unfinished last line", r.stderr)
        # With a newline it is a bad line, and so is a bad line before the last.
        for text in (json.dumps(t[0]) + "\n{cut\n", "{cut\n" + json.dumps(t[0])):
            r = self.run_paths([self.write_text(text), self.write(t)])
            self.assertEqual(r.returncode, 2, text)
            self.assertNotIn("dropped", r.stderr)

    def test_missing_signature_table(self):
        t = [rec(0, DELAY, [1], 0)]
        r = self.diff(t, t, "--sigs", str(self.tmp / "nonexistent.txt"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("signature table", r.stderr)


class SignatureTable(TempDir):
    def test_parses(self):
        table = sigs.load()
        self.assertGreater(len(table), 100)
        expect = {"sceKernelCreateThread": (6, "u"), "sceKernelStartThread": (3, "i"),
                  "sceKernelAllocPartitionMemory": (5, "u"), "sceKernelGetBlockHeadAddr": (1, "i"),
                  "sceKernelCreateMutex": (4, "u"), "sceKernelCreateLwMutex": (5, "i"),
                  "sceKernelLockLwMutexCB": (3, "i"), "sceKernelUnlockLwMutex": (2, "i"),
                  "sceKernelDelayThreadCB": (1, "i"), "sceIoOpen": (3, "u"), "sceIoGetstat": (2, "i"),
                  "sceUmdWaitDriveStat": (1, "i"), "sceUmdActivate": (2, "i"),
                  "sceKernelGetSystemTimeWide": (0, "l"), "sceKernelSetCompilerVersion": (1, "i"),
                  "sceKernelExitThread": (1, "i"), "sceIoClose": (1, "d"),
                  "sceKernelDeleteThread": (1, "d"), "sceKernelFreePartitionMemory": (1, "d")}
        for name, (nargs, ret) in expect.items():
            self.assertEqual(table[nid_of(name)], (name, tuple(range(nargs)), ret), name)
        self.assertEqual(table[0x35669D4C], (None, (0,), "i"))
        self.assertEqual(table[nid_of("sceIoLseek")].args, (0, 2, 3, 4))

    def test_name_nids_are_unique(self):
        names = [line.split()[0] for line in sigs.SIGS.read_text(encoding="utf-8").splitlines()
                 if line.split("#")[0].strip() and not line.startswith("0x")]
        nids = [nid_of(n) for n in names]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(nids)), len(nids))

    def test_counts_and_indexes(self):
        table = sigs.load(self.write_text("sceA 0 i\nsceB 8 v\nsceC 7 l\nsceD 1,7 d\n", ".txt"))
        self.assertEqual([s.args for s in table.values()], [(), tuple(range(8)), tuple(range(7)), (1, 7)])

    def test_bad_lines(self):
        for text in ("sceFoo 1\n", "sceFoo 1 x\n", "sceFoo 1 iu\n", "sceFoo 8,9 i\n", "sceFoo 1,1 i\n",
                     "sceFoo 9 i\n", "sceFoo -1 i\n", "sceFoo 1 i\nsceFoo 2 i\n", "0xZZ 1 i\n"):
            with self.assertRaises(ValueError, msg=text):
                sigs.load(self.write_text(text, ".txt"))


if __name__ == "__main__":
    unittest.main()
