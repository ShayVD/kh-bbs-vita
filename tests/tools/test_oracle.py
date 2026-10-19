"""oracle.py first-divergence with both sides stubbed: a fake runner stands in
for khbbs, PPSSPP's capture, the snapshot tools and trace_diff, so no process
starts. Every value is made up."""
import contextlib, io, os, pathlib, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle import oracle  # noqa: E402

PY = sys.executable
STOP = "khbbs: stopped: dispatch budget exhausted  (200000 dispatches, 589623 traced firmware calls)"


class FakeRunner:
    """Records every command. khbbs and the capture write their outputs
    (unless told not to); each tool returns the exit code set for it."""
    def __init__(self, codes=None, write=True, khbbs_snapshot=True):
        self.cmds, self.logs, self.codes, self.write, self.khbbs_snapshot = [], [], codes or {}, write, khbbs_snapshot

    def tool(self, cmd):
        if cmd[0] == "perl":
            return "khbbs"
        return cmd[2].split(".")[-1] if cmd[:2] == [PY, "-m"] else cmd[0]

    def __call__(self, cmd, log=None):
        self.cmds.append(cmd)
        self.logs.append(log)
        tool = self.tool(cmd)
        if self.write and tool == "khbbs":
            pathlib.Path(cmd[cmd.index("--trace") + 1]).write_text("{}\n")
            if "--snapshot" in cmd and self.khbbs_snapshot:
                pathlib.Path(cmd[cmd.index("--snapshot") + 1] + ".json").write_text("{}\n")
        if self.write and tool == "capture_hle":
            pathlib.Path(cmd[cmd.index("-o") + 1]).write_text("{}\n")
        if self.write and tool == "snapshot":
            pathlib.Path(cmd[cmd.index("-o") + 1] + ".json").write_text("{}\n")
        if self.write and log is not None:
            pathlib.Path(log).write_text("psprecomp: unimplemented firmware call 0x12345678\n" + STOP + "\n")
        return self.codes.get(tool, 3 if tool == "khbbs" else 0)

    def by_tool(self):
        return {self.tool(c): c for c in self.cmds}


class FirstDivergence(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.out = self.tmp / "oracle"

    def run_oracle(self, runner, *extra):
        argv = ["first-divergence", "--iso", "game.iso", "--elf", "EBOOT.ELF", "-n", "200",
                "--out-dir", str(self.out), "--khbbs", "khbbs", *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = oracle.main(argv, runner=runner)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_commands(self):
        r = FakeRunner()
        code, out, _ = self.run_oracle(r)
        self.assertEqual(code, 0)
        k, p = str(self.out / "khbbs.jsonl"), str(self.out / "ppsspp.jsonl")
        self.assertEqual(r.cmds, [
            ["perl", "-e", "alarm shift; exec @ARGV", "120", "khbbs", "--elf", "EBOOT.ELF",
             "--trace", k, "--budget", "200000"],
            [PY, "-m", "oracle.capture_hle", "--iso", "game.iso", "--elf", "EBOOT.ELF", "-n", "200",
             "-o", p, "--home", str(oracle.HOME), "--timeout", "120.0"],
            [PY, "-m", "oracle.trace_diff", k, p, "--map-uids", "-n", "200"],
        ])
        self.assertEqual(r.logs, [self.out / "khbbs.log", None, None])
        self.assertIn("khbbs exited with 3; " + STOP, out)
        self.assertNotIn("unimplemented", out)

    def test_budget_and_alarm(self):
        r = FakeRunner()
        self.run_oracle(r, "--budget", "5000", "--alarm", "30", "--timeout", "60")
        cmds = r.by_tool()
        self.assertEqual(cmds["khbbs"][3], "30")
        self.assertEqual(cmds["khbbs"][-2:], ["--budget", "5000"])
        self.assertEqual(cmds["capture_hle"][-2:], ["--timeout", "60.0"])

    def test_snapshot_commands(self):
        r = FakeRunner()
        code, _, _ = self.run_oracle(r, "--snapshot-at", "sceKernelCreateThread:1")
        self.assertEqual(code, 0)
        km, pm = str(self.out / "khbbs-mem.bin"), str(self.out / "ppsspp-mem.bin")
        cmds = r.by_tool()
        self.assertEqual(cmds["khbbs"][-4:], ["--snapshot-at", "sceKernelCreateThread:1", "--snapshot", km])
        self.assertEqual(cmds["snapshot"], [PY, "-m", "oracle.snapshot", "--iso", "game.iso", "--elf",
                                            "EBOOT.ELF", "--at", "sceKernelCreateThread:1", "-o", pm,
                                            "--home", str(oracle.HOME), "--timeout", "120.0"])
        self.assertEqual(cmds["mem_diff"], [PY, "-m", "oracle.mem_diff", km, pm, "--elf", "EBOOT.ELF",
                                            "--ignore", "0x08000000-0x08800000"])
        self.assertEqual([r.tool(c) for c in r.cmds], ["khbbs", "capture_hle", "snapshot", "trace_diff",
                                                       "mem_diff"])

    def test_no_snapshot_without_option(self):
        r = FakeRunner()
        self.run_oracle(r)
        self.assertNotIn("snapshot", r.by_tool())
        self.assertNotIn("mem_diff", r.by_tool())

    def test_propagates_trace_diff_status(self):
        for status in (0, 1, 2):
            with self.subTest(status=status):
                code, _, _ = self.run_oracle(FakeRunner({"trace_diff": status}))
                self.assertEqual(code, status)

    def test_mem_diff_difference_keeps_trace_diff_status(self):
        code, out, _ = self.run_oracle(FakeRunner({"mem_diff": 1}), "--snapshot-at", "sceKernelCreateThread:1")
        self.assertEqual(code, 0)
        self.assertIn("memory differs", out)

    def test_mem_diff_bad_input_fails(self):
        code, _, err = self.run_oracle(FakeRunner({"mem_diff": 2}), "--snapshot-at", "sceKernelCreateThread:1")
        self.assertEqual(code, 2)
        self.assertIn("oracle: mem_diff failed", err)

    def test_failing_capture(self):
        r = FakeRunner({"capture_hle": 1})
        code, _, err = self.run_oracle(r)
        self.assertEqual(code, 2)
        self.assertIn("oracle: capture_hle failed (exit 1)", err)
        self.assertNotIn("trace_diff", r.by_tool())

    def test_missing_khbbs_trace(self):
        r = FakeRunner({"khbbs": 1}, write=False)
        code, _, err = self.run_oracle(r)
        self.assertEqual(code, 2)
        self.assertIn("wrote no trace", err)
        self.assertEqual(len(r.cmds), 1)

    def test_missing_khbbs_snapshot(self):
        r = FakeRunner(khbbs_snapshot=False)
        code, _, err = self.run_oracle(r, "--snapshot-at", "sceKernelCreateThread:1")
        self.assertEqual(code, 2)
        self.assertIn("khbbs wrote no snapshot", err)
        self.assertNotIn("mem_diff", r.by_tool())

    def test_stale_outputs_removed(self):
        self.out.mkdir()
        stale = ("khbbs.jsonl", "khbbs.log", "ppsspp.jsonl", "khbbs-mem.bin", "khbbs-mem.bin.json",
                 "ppsspp-mem.bin", "ppsspp-mem.bin.json")
        kept = ("khbbs.jsonl.json", "ppsspp.jsonl.json", "khbbs.log.json", "notes.json")
        for name in stale + kept:
            (self.out / name).write_text("stale\n")
        r = FakeRunner(write=False)                  # khbbs exits 3 and writes nothing
        code, out, err = self.run_oracle(r, "--snapshot-at", "sceKernelCreateThread:1")
        self.assertEqual(code, 2)
        self.assertIn(f"wrote no trace to {self.out / 'khbbs.jsonl'} (exit 3); see {self.out / 'khbbs.log'}",
                      err)
        self.assertIn("khbbs exited with 3; no stop line", out)
        for name in stale:
            self.assertFalse((self.out / name).exists(), name)
        for name in kept:
            self.assertTrue((self.out / name).exists(), name)

    def test_khbbs_killed_by_signal(self):
        for signal, note in ((14, "killed by signal 14 (SIGALRM: --alarm 120 s ran out)"),
                             (9, "killed by signal 9")):
            with self.subTest(signal=signal):
                code, out, _ = self.run_oracle(FakeRunner({"khbbs": -signal}))
                self.assertEqual(code, 0)            # the trace was written; trace_diff decides
                self.assertIn("oracle: khbbs " + note + "; ", out)
                self.assertNotIn("exited with", out)

    def test_timeout_must_be_positive(self):
        for bad in ("0", "-1", "nan", "x"):
            with self.subTest(timeout=bad), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                self.run_oracle(FakeRunner(), "--timeout", bad)


class Run(unittest.TestCase):
    """The real runner, on a Python child instead of a tool."""
    def test_pythonpath_and_log(self):
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        log = tmp / "child.log"
        child = "import os, sys; print(os.environ['PYTHONPATH'], flush=True); print('err', file=sys.stderr); sys.exit(3)"
        with mock.patch.dict(oracle.os.environ, {"PYTHONPATH": "/elsewhere"}):
            code = oracle.run([PY, "-c", child], log=log)
        self.assertEqual(code, 3)
        self.assertEqual(log.read_text().splitlines(),
                         [os.pathsep.join([str(oracle.TOOLS), "/elsewhere"]), "err"])


    def test_iso_required(self):
        with mock.patch.dict(oracle.os.environ, {}, clear=True):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as e:
                oracle.main(["first-divergence", "--elf", "EBOOT.ELF"], runner=FakeRunner())
        self.assertEqual(e.exception.code, 2)
        self.assertIn("--iso", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
