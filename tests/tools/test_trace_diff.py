"""trace_diff.py on synthetic traces."""
import json, pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from import_report import nid_of  # noqa: E402
from oracle import sigs  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "oracle" / "trace_diff.py"


def rec(seq, nid, args, ret, **kw):
    r = {"seq": seq, "nid": f"0x{nid:08X}", "name": None, "ra": "0x08A8BD7C",
         "args": [f"0x{a:08X}" for a in args] + ["0x00000000"] * (8 - len(args)),
         "ret": f"0x{ret:08X}", "ret_hi": "0x00000000"}
    r.update(kw)
    return r


def write(recs):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("".join(json.dumps(r) + "\n" for r in recs))
    f.close()
    return f.name


def run(a, b, *opts):
    return subprocess.run([sys.executable, str(TOOL), write(a), write(b), *opts], capture_output=True, text=True)


CREATE, START, DELAY = 0x446D8DE6, 0xF475845D, 0xCEADEB47
RESUME_INTR = nid_of("sceKernelCpuResumeIntr")      # void
TIME_WIDE = nid_of("sceKernelGetSystemTimeWide")    # 64-bit
UNKNOWN = 0x12345678                                # not in hle_sigs.txt


class TraceDiff(unittest.TestCase):
    def test_identical(self):
        t = [rec(0, CREATE, [1, 2], 0x40000), rec(1, START, [0x40000], 0)]
        r = run(t, t)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)

    def test_first_divergence_named(self):
        a = [rec(0, CREATE, [1, 2], 0x40000), rec(1, DELAY, [100], 0)]
        b = [rec(0, CREATE, [1, 2], 0x40000), rec(1, DELAY, [200], 0)]
        r = run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1", r.stdout)
        self.assertIn("args[0]", r.stdout)

    def test_uid_mapping(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, START, [0x40000], 0)]
        b = [rec(0, CREATE, [1], 0x04A1B2C3), rec(1, START, [0x04A1B2C3], 0)]
        self.assertEqual(run(a, b).returncode, 1)
        self.assertEqual(run(a, b, "--map-uids").returncode, 0)

    def test_null_fields_are_not_compared(self):
        a = [rec(0, CREATE, [1], 0x40000)]
        b = [rec(0, CREATE, [1], 0x40000)]
        b[0]["ret"] = None
        self.assertEqual(run(a, b).returncode, 0)

    def test_per_thread_alignment(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="sound")]
        b = [rec(0, DELAY, [2], 0, thread="sound"), rec(1, DELAY, [1], 0, thread="main")]
        self.assertEqual(run(a, b).returncode, 0)

    def test_limit(self):
        a = [rec(0, CREATE, [1], 1), rec(1, DELAY, [1], 0)]
        b = [rec(0, CREATE, [1], 1), rec(1, DELAY, [9], 0)]
        self.assertEqual(run(a, b, "-n", "1").returncode, 0)

    def test_signature_limits_arity(self):
        a = [rec(0, DELAY, [100, 0, 0, 0, 0, 0x1111], 0)]
        b = [rec(0, DELAY, [100, 0, 0, 0, 0, 0x2222], 0)]
        self.assertEqual(run(a, b).returncode, 0)
        a[0]["nid"] = b[0]["nid"] = f"0x{UNKNOWN:08X}"
        r = run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("args[5]", r.stdout)

    def test_void_return_not_compared(self):
        self.assertEqual(run([rec(0, RESUME_INTR, [1], 0x1234)], [rec(0, RESUME_INTR, [1], 0x5678)]).returncode, 0)

    def test_ret_hi_compared_for_64_bit_returns_only(self):
        a = [rec(0, TIME_WIDE, [], 0x100, ret_hi="0x00000001")]
        b = [rec(0, TIME_WIDE, [], 0x100, ret_hi="0x00000002")]
        r = run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("differing: ret_hi", r.stdout)
        a[0]["nid"] = b[0]["nid"] = f"0x{DELAY:08X}"
        self.assertEqual(run(a, b).returncode, 0)
        a[0]["nid"] = b[0]["nid"] = f"0x{UNKNOWN:08X}"
        self.assertEqual(run(a, b).returncode, 0)

    def test_missing_signature_hint(self):
        r = run([rec(0, UNKNOWN, [1], 0)], [rec(0, UNKNOWN, [2], 0)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("no signature for 0x12345678 in hle_sigs.txt", r.stdout)
        r = run([rec(0, DELAY, [1], 0)], [rec(0, DELAY, [2], 0)])
        self.assertNotIn("no signature", r.stdout)

    def test_names_come_from_the_table(self):
        r = run([rec(0, DELAY, [1], 0)], [rec(0, DELAY, [2], 0, name="sceKernelDelayThread?")])
        self.assertIn('"name": "sceKernelDelayThread"', r.stdout)
        self.assertIn('"name": "sceKernelDelayThread?"', r.stdout)

    def test_ignore_arg(self):
        a = [rec(0, DELAY, [1], 0), rec(1, CREATE, [1, 2], 0x40000)]
        b = [rec(0, DELAY, [1], 0x80020001), rec(1, CREATE, [1, 3], 0x40000)]
        self.assertEqual(run(a, b, "--ignore-arg", "sceKernelDelayThread:ret").returncode, 1)
        self.assertEqual(run(a, b, "--ignore-arg", "sceKernelDelayThread:ret",
                             "--ignore-arg", "0x446D8DE6:1").returncode, 0)
        self.assertEqual(run(a, b, "--ignore-arg", "sceKernelDelayThread").returncode, 2)
        self.assertEqual(run(a, b, "--ignore-arg", "sceKernelDelayThread:8").returncode, 2)

    def test_one_stream_unless_both_sides_have_threads(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="sound")]
        b = [rec(0, DELAY, [2], 0), rec(1, DELAY, [1], 0)]
        r = run(a, b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 0 (A seq 0, B seq 0)", r.stdout)
        b = [rec(0, DELAY, [2], 0, thread="sound"), rec(1, DELAY, [1], 0, thread="main")]
        b[0]["seq"], b[1]["seq"] = 7, 8
        b[1]["args"][0] = "0x00000009"
        r = run(a, b)
        self.assertIn("first divergence at record 0 of thread main (A seq 0, B seq 8)", r.stdout)

    def test_window(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [2], 0, thread="main"),
             rec(2, DELAY, [3], 0, thread="sound")]
        b = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [3], 0, thread="sound"),
             rec(2, DELAY, [4], 0, thread="sound")]
        r = run(a, b, "-n", "3")                 # both traces go on past the window
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)
        self.assertIn("B's thread main ends at record 1, beyond the compared window", r.stdout)
        self.assertIn("A's thread sound ends at record 1, beyond the compared window", r.stdout)
        r = run(a, b[:1], "-n", "3")             # B really ended
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1 of thread main", r.stdout)
        self.assertIn("differing: missing", r.stdout)
        r = run(a[:2], b, "-n", "3")             # A really ended
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 0 of thread sound (A seq -, B seq 1)", r.stdout)
        self.assertEqual(run(a, b).returncode, 1)  # no -n: both traces are whole

    def test_uid_map_must_stay_consistent(self):
        a = [rec(0, CREATE, [1], 0x40000), rec(1, CREATE, [2], 0x40001)]
        b = [rec(0, CREATE, [1], 0x114), rec(1, CREATE, [2], 0x114)]
        r = run(a, b, "--map-uids")
        self.assertEqual(r.returncode, 1)
        self.assertIn("first divergence at record 1", r.stdout)
        self.assertIn("B 0x00000114 already maps to A 0x00040000", r.stdout)
        b[1]["ret"] = "0x80020001"                 # error codes are never learned
        self.assertIn("no UID pair", run(a, b, "--map-uids").stdout)

    def test_uid_map_ignores_equal_values(self):
        # A value that happens to equal a B uid still matches itself.
        a = [rec(0, CREATE, [1], 0x40000), rec(1, DELAY, [0x114], 0)]
        b = [rec(0, CREATE, [1], 0x114), rec(1, DELAY, [0x114], 0)]
        self.assertEqual(run(a, b, "--map-uids").returncode, 0)

    def test_bad_input(self):
        good = write([rec(0, DELAY, [1], 0)])
        bad = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        bad.write(json.dumps(rec(0, DELAY, [1], 0)) + "\n{not json\n")
        bad.close()
        for files in ([good, bad.name], [bad.name, good], [good, "/nonexistent/trace.jsonl"]):
            r = subprocess.run([sys.executable, str(TOOL), *files], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("trace_diff:", r.stderr)
        no_args = dict(rec(0, DELAY, [1], 0), args="0x1")
        self.assertEqual(run([no_args], [rec(0, DELAY, [1], 0)]).returncode, 2)


class SignatureTable(unittest.TestCase):
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
                  "sceKernelExitThread": (1, "i")}
        for name, (nargs, ret) in expect.items():
            self.assertEqual(table[nid_of(name)], (name, tuple(range(nargs)), ret), name)
        self.assertEqual(table[0x35669D4C], (None, (0,), "i"))
        self.assertEqual(table[nid_of("sceIoLseek")].args, (0, 2, 3, 4))

    def test_name_nids_are_unique(self):
        names = [line.split()[0] for line in sigs.SIGS.read_text().splitlines()
                 if line.split("#")[0].strip() and not line.startswith("0x")]
        nids = [nid_of(n) for n in names]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(nids)), len(nids))

    def test_bad_lines(self):
        for text in ("sceFoo 1\n", "sceFoo 1 x\n", "sceFoo 8,9 i\n", "sceFoo 1 i\nsceFoo 2 i\n",
                     "0xZZ 1 i\n"):
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                f.write(text)
            with self.assertRaises(ValueError, msg=text):
                sigs.load(f.name)


if __name__ == "__main__":
    unittest.main()
