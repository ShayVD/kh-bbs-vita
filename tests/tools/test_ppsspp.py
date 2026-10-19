"""ppsspp.py's request/ticket protocol against FakePpsspp. Never starts PPSSPP."""
import json, pathlib, socket, stat, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from fakeppsspp import FakePpsspp  # noqa: E402
from test_ws import Server, frame, read_client_frame  # noqa: E402
from oracle import ppsspp  # noqa: E402
from oracle.ppsspp import Ppsspp, PpssppError, isolated_env, port_free, prepare_home  # noqa: E402


def reply(m, **kw):
    return {"event": m["event"], "ticket": m["ticket"], **kw}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PpssppTest(unittest.TestCase):
    def serve(self, handlers):
        srv = FakePpsspp(handlers)
        self.addCleanup(srv.sock.close)
        srv.start()
        return srv

    def connect(self, srv, **kw):
        p = Ppsspp.connect(port=srv.port, **kw)
        self.addCleanup(p.close)
        return p

    def test_request_reply_and_queued_events(self):
        srv = self.serve({
            "version": lambda m: [{"event": "log", "message": "unrelated"},
                                  reply(m, name="PPSSPP", version="v1.20.4")],
            "memory.read": lambda m: reply(m, base64="AAECAw=="),
        })
        p = self.connect(srv)
        self.assertEqual(p.request("version")["version"], "v1.20.4")
        self.assertEqual(p.read_memory(0x08800000, 4), b"\x00\x01\x02\x03")
        self.assertEqual(p.next_event("log")["message"], "unrelated")
        with self.assertRaisesRegex(PpssppError, "unknown event"):
            p.request("nope")
        p.close()
        srv.join_checked(self)
        tickets = [m["ticket"] for m in srv.received]
        self.assertEqual(len(set(tickets)), len(tickets), "tickets are unique")
        self.assertEqual((srv.received[1]["address"], srv.received[1]["size"]), (0x08800000, 4))

    def test_read_memory_in_chunks(self):
        srv = self.serve({"memory.read": lambda m: reply(m, base64="AAEC" if m["size"] == 3 else "AA==")})
        p = self.connect(srv)
        self.assertEqual(p.read_memory(0x08804000, 4, chunk=3), b"\x00\x01\x02\x00")
        p.close()
        srv.join_checked(self)
        self.assertEqual([(m["address"], m["size"]) for m in srv.received],
                         [(0x08804000, 3), (0x08804003, 1)])

    def test_send_is_fire_and_forget(self):
        srv = self.serve({"cpu.stepping": lambda m: []})
        p = self.connect(srv)
        t1, t2 = p.send("cpu.stepping"), p.send("cpu.stepping")
        self.assertNotEqual(t1, t2)
        p.close()
        srv.join_checked(self)
        self.assertEqual([m["ticket"] for m in srv.received], [t1, t2])

    def test_resume_waits_for_the_untagged_event(self):
        srv = self.serve({
            # An old stop still queued from the version request must not be
            # taken for the stop after the resume.
            "version": lambda m: [{"event": "cpu.stepping", "pc": 1, "ticks": 0}, reply(m)],
            "cpu.resume": lambda m: [{"event": "cpu.resume"},
                                     {"event": "cpu.stepping", "pc": 0x08B307B8, "ticks": 99}],
        })
        p = self.connect(srv)
        p.request("version")
        p.resume()
        self.assertEqual(p.wait_stepping(5)["pc"], 0x08B307B8)
        p.close()
        srv.join_checked(self)

    def test_resume_while_running_fails(self):
        srv = self.serve({"cpu.resume": lambda m: {"event": "error", "ticket": m["ticket"],
                                                   "message": "CPU not stepping", "level": 2}})
        p = self.connect(srv)
        with self.assertRaisesRegex(PpssppError, "CPU not stepping"):
            p.resume()
        p.close()
        srv.join_checked(self)

    def test_pause_while_running(self):
        srv = self.serve({
            "cpu.status": lambda m: reply(m, stepping=False, paused=False, pc=0x08A8C000, ticks=5),
            "cpu.stepping": lambda m: {"event": "cpu.stepping", "pc": 0x08A8C010, "ticks": 6},
        })
        p = self.connect(srv)
        self.assertEqual(p.pause(), 0x08A8C010)
        p.close()
        srv.join_checked(self)

    def test_pause_while_stepping_sends_nothing(self):
        """PPSSPP never answers cpu.stepping when already stepping, so pause() must not wait for it."""
        srv = self.serve({
            "cpu.status": lambda m: [{"event": "cpu.stepping", "pc": 1, "ticks": 0},
                                     reply(m, stepping=True, paused=False, pc=0x08A8BD58, ticks=0)],
        })
        p = self.connect(srv)
        self.assertEqual(p.pause(), 0x08A8BD58)
        self.assertEqual(list(p.events), [], "the old stop is dropped")
        p.close()
        srv.join_checked(self)
        self.assertEqual([m["event"] for m in srv.received], ["cpu.status"])

    def test_pause_drops_an_old_stop(self):
        srv = self.serve({
            "version": lambda m: [{"event": "cpu.stepping", "pc": 1, "ticks": 0}, reply(m)],
            "cpu.status": lambda m: reply(m, stepping=False, paused=False, pc=0x08A8C000, ticks=5),
            "cpu.stepping": lambda m: {"event": "cpu.stepping", "pc": 0x08A8C010, "ticks": 6},
        })
        p = self.connect(srv)
        p.request("version")
        self.assertEqual(p.pause(), 0x08A8C010)
        p.close()
        srv.join_checked(self)

    def test_resume_waits(self):
        srv = self.serve({"cpu.resume": lambda m: []})
        p = self.connect(srv)
        with self.assertRaisesRegex(PpssppError, "timed out"):
            p.resume(timeout=0.3)
        p.close()
        srv.join_checked(self)

    def test_timeout_between_messages_keeps_the_session(self):
        srv = self.serve({"version": lambda m: reply(m, version="v1.20.4")})
        p = self.connect(srv)
        with self.assertRaisesRegex(PpssppError, "timed out"):
            p.wait_stepping(0.2)
        self.assertEqual(p.request("version")["version"], "v1.20.4")
        p.close()
        srv.join_checked(self)

    def test_zero_timeout_polls(self):
        srv = self.serve({
            "version": lambda m: [{"event": "gpu.stats.get", "fps": 60}, reply(m)],
            "poke": lambda m: {"event": "game.pause"},
        })
        p = self.connect(srv)
        t = time.monotonic()
        with self.assertRaises(PpssppError):
            p.next_event("game.pause", 0)                          # nothing yet
        self.assertLess(time.monotonic() - t, 0.5)
        p.request("version")
        self.assertEqual(p.next_event("gpu.stats.get", 0)["fps"], 60)  # queued
        p.send("poke")
        time.sleep(0.2)
        self.assertEqual(p.next_event("game.pause", 0)["event"], "game.pause")  # arrived, unread
        p.close()
        srv.join_checked(self)

    def test_lone_ping_keeps_the_session(self):
        """A ping and then silence: answered, the wait times out, the session stays usable."""
        got = []

        def script(conn):
            conn.sendall(frame(0x9, b"hi"))
            got.append(read_client_frame(conn))                    # pong
            op, data = read_client_frame(conn)
            conn.sendall(frame(0x1, json.dumps(reply(json.loads(data), version="v1.20.4")).encode()))
            read_client_frame(conn)                                # close

        srv = Server(script, status=b"HTTP/1.1 101 OK")
        self.addCleanup(srv.sock.close)
        srv.start()
        p = self.connect(srv)
        with self.assertRaisesRegex(PpssppError, "timed out after 0.3 s waiting"):
            p.wait_stepping(0.3)
        self.assertEqual(p.request("version")["version"], "v1.20.4")
        p.close()
        srv.join_checked(self)
        self.assertEqual(got, [(0xA, b"hi")])

    def test_deadline_holds_while_events_stream(self):
        stop = threading.Event()
        self.addCleanup(stop.set)

        batch = frame(0x1, b'{"event": "gpu.stats.get", "fps": 60}') * 50

        def script(conn):
            end = time.monotonic() + 3                             # so a broken deadline fails, not hangs
            while not stop.is_set() and time.monotonic() < end:    # faster than the client reads
                try:
                    conn.sendall(batch)
                except OSError:
                    return                                         # the client has gone

        srv = Server(script, status=b"HTTP/1.1 101 OK")
        self.addCleanup(srv.sock.close)
        srv.start()
        p = self.connect(srv)
        t = time.monotonic()
        with self.assertRaisesRegex(PpssppError, "timed out"):
            p.wait_stepping(0.3)
        self.assertLess(time.monotonic() - t, 0.8)
        self.assertGreater(len(p.events), 10)
        p.close()
        stop.set()
        srv.join_checked(self)

    def test_pause_shares_one_deadline(self):
        def slow_status(m):
            time.sleep(0.3)
            return reply(m, stepping=False, paused=False, pc=0x08A8C000, ticks=5)

        srv = self.serve({"cpu.status": slow_status, "cpu.stepping": lambda m: []})
        p = self.connect(srv)
        t = time.monotonic()
        with self.assertRaisesRegex(PpssppError, "timed out"):
            p.pause(timeout=0.5)
        self.assertLess(time.monotonic() - t, 0.7)                 # not 0.3 + 0.5
        p.close()
        srv.join_checked(self)

    def test_noise_is_not_queued(self):
        srv = self.serve({"version": lambda m: [{"event": "log", "message": "x"}] * 3 +
                          [{"event": "input.analog"}, {"event": "game.pause"}, reply(m)]})
        p = self.connect(srv)
        p.ignore = ppsspp.NOISE
        p.request("version")
        self.assertEqual([m["event"] for m in p.events], ["game.pause"])
        p.close()
        srv.join_checked(self)

    def test_closed_connection_is_a_ppsspp_error(self):
        srv = Server(read_client_frame, status=b"HTTP/1.1 101 OK")   # reads the request, then hangs up
        self.addCleanup(srv.sock.close)
        srv.start()
        p = self.connect(srv)
        with self.assertRaisesRegex(PpssppError, "waiting for version.*closed"):
            p.request("version")
        p.close()
        srv.join_checked(self)

    def test_wait_for_entry(self):
        """The debugger listens before the game is loaded: poll until it stops at the entry."""
        states = iter([dict(stepping=False, paused=True, pc=0, ticks=0)] * 2 +
                      [dict(stepping=True, paused=False, pc=0x08A8BD58, ticks=0)])
        srv = self.serve({"cpu.status": lambda m: reply(m, **next(states))})
        p = self.connect(srv)
        self.assertEqual(p.wait_for_entry(5, poll=0.01)["pc"], 0x08A8BD58)
        p.close()
        srv.join_checked(self)
        self.assertEqual(len(srv.received), 3)

    def test_regs(self):
        names = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3"] + [f"r{i}" for i in range(8, 31)] + ["ra", "pc", "hi", "lo"]
        values = list(range(35))
        values[4], values[32] = 0x06020010, 0x08A8BD58
        srv = self.serve({"cpu.getAllRegs": lambda m: reply(m, categories=[
            {"id": 1, "name": "FPU", "registerNames": ["f0"], "uintValues": [7], "floatValues": ["0"]},
            {"id": 0, "name": "GPR", "registerNames": names, "uintValues": values, "floatValues": []},
        ])})
        p = self.connect(srv)
        regs = p.regs()
        self.assertEqual((regs["a0"], regs["pc"], regs["lo"], len(regs)), (0x06020010, 0x08A8BD58, 34, 35))
        p.close()
        srv.join_checked(self)

    def test_breakpoints(self):
        srv = self.serve({"cpu.breakpoint.add": lambda m: reply(m),
                          "cpu.breakpoint.remove": lambda m: reply(m)})
        p = self.connect(srv)
        p.add_breakpoint(0x08B307B8)
        p.add_breakpoint(0x08A90104, enabled=False, log=True, condition="pc == ra",
                         log_format="R {pc} {[k0+0xc0]} {v0} {v1}")
        p.remove_breakpoint(0x08B307B8)
        p.close()
        srv.join_checked(self)
        strip = [{k: v for k, v in m.items() if k != "ticket"} for m in srv.received]
        self.assertEqual(strip, [
            {"event": "cpu.breakpoint.add", "address": 0x08B307B8, "enabled": True, "log": False},
            {"event": "cpu.breakpoint.add", "address": 0x08A90104, "enabled": False, "log": True,
             "condition": "pc == ra", "logFormat": "R {pc} {[k0+0xc0]} {v0} {v1}"},
            {"event": "cpu.breakpoint.remove", "address": 0x08B307B8},
        ])

    def test_timeout(self):
        srv = self.serve({"version": lambda m: []})
        p = self.connect(srv, timeout=0.3)
        t = time.monotonic()
        with self.assertRaisesRegex(PpssppError, "timed out"):
            p.request("version")
        self.assertLess(time.monotonic() - t, 2)
        p.close()
        srv.join_checked(self)

    def test_wait_stepping_timeout(self):
        srv = self.serve({})
        p = self.connect(srv)
        t = time.monotonic()
        with self.assertRaises(PpssppError):
            p.wait_stepping(0.3)
        self.assertLess(time.monotonic() - t, 2)
        p.close()
        srv.join_checked(self)


class HomeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()

    def test_prepare_home(self):
        home = self.root / "home"
        stale = home / ".config/ppsspp/PSP/SAVEDATA/ULUS10505/DATA.BIN"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"x")
        (home / "ppsspp-log.txt").write_text("old log\n")
        (home / "keep.txt").write_text("not ours\n")
        self.assertEqual(prepare_home(home, root=self.root), home)
        self.assertFalse(stale.exists())
        self.assertFalse((home / ".config/ppsspp/PSP/SAVEDATA").exists())
        self.assertFalse((home / "ppsspp-log.txt").exists())
        self.assertTrue((home / "keep.txt").exists())
        ini = (home / ".config/ppsspp/PSP/SYSTEM/ppsspp.ini").read_text()
        for line in ("[General]", "FirstRun = False", "AutoRun = False", "CheckForNewVersion = False",
                     "RemoteDebuggerOnStartup = True", "RemoteISOPort = 45045", "[Graphics]",
                     "SoftwareRenderer = True", "[Log]", "SCEKERNELLevel = 4"):
            self.assertIn(line + "\n", ini)

    def test_prepare_home_refuses_paths_outside_root(self):
        outside = self.root / "outside"
        (outside / ".config/ppsspp").mkdir(parents=True)
        for bad in (outside, "", ".", "/", pathlib.Path.home()):
            with self.subTest(bad=bad), self.assertRaises(PpssppError):
                prepare_home(bad)                                  # default root: the repo's build/
        for bad in (self.root, self.root / "home/../.."):
            with self.subTest(bad=bad), self.assertRaises(PpssppError):
                prepare_home(bad, root=self.root)
        self.assertTrue((outside / ".config/ppsspp").exists())

    def test_ini_names_the_port(self):
        home = prepare_home(self.root / "home", root=self.root, port=45999)
        self.assertIn("RemoteISOPort = 45999\n", (home / ".config/ppsspp/PSP/SYSTEM/ppsspp.ini").read_text())

    def test_prepare_home_refuses_symlinks(self):
        target = self.root / "elsewhere"
        (target / "ppsspp/PSP/SAVEDATA").mkdir(parents=True)
        for name, link, to in ((".config", ".config", target), ("ppsspp", ".config/ppsspp", target / "ppsspp")):
            with self.subTest(name):
                home = self.root / f"home-{name}"
                (home / link).parent.mkdir(parents=True, exist_ok=True)
                (home / link).symlink_to(to)
                with self.assertRaisesRegex(PpssppError, "symlink"):
                    prepare_home(home, root=self.root)
                self.assertTrue((target / "ppsspp/PSP/SAVEDATA").is_dir())
                self.assertFalse((target / "ppsspp/PSP/SYSTEM").exists())

    def test_relative_home_is_made_absolute(self):
        home = pathlib.Path("build/oracle/ppsspp-home")
        env = isolated_env(home)
        self.assertEqual(set(env), {"HOME", "CFFIXED_USER_HOME"})
        self.assertTrue(pathlib.Path(env["HOME"]).is_absolute())
        self.assertEqual(env["HOME"], env["CFFIXED_USER_HOME"])
        self.assertEqual(env["HOME"], str(home.resolve()))


class PortTest(unittest.TestCase):
    def test_port_free(self):
        for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "0.0.0.0"),
                             (socket.AF_INET6, "::"), (socket.AF_INET6, "::1")):
            with self.subTest(addr=addr):
                s = socket.socket(family)
                self.addCleanup(s.close)
                s.bind((addr, 0))
                s.listen(1)
                self.assertFalse(port_free(s.getsockname()[1]))
                s.close()

    def test_free_port(self):
        self.assertTrue(port_free(free_port()))

    def test_time_wait_does_not_count(self):
        """PPSSPP closing first leaves a TIME_WAIT on its own port."""
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port)) as c:
                conn, _ = srv.accept()
                conn.close()                                       # server side closes first
                c.recv(1)
        with socket.socket() as s, self.assertRaises(OSError):
            s.bind(("0.0.0.0", port))                              # the TIME_WAIT is there
        self.assertTrue(port_free(port))


class LaunchTest(unittest.TestCase):
    """launch()'s failure paths, with a shell script standing in for PPSSPP."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()
        self.iso = self.root / "game.iso"
        self.iso.write_bytes(b"")
        self.home = self.root / "home"

    def app(self, body):
        app = self.root / "fakeapp"
        app.write_text("#!/bin/sh\n" + body)
        app.chmod(app.stat().st_mode | stat.S_IXUSR)
        patcher = mock.patch.object(ppsspp, "APP", app)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_app_exits(self):
        self.app("exit 3\n")
        with self.assertRaisesRegex(PpssppError, "exited with 3"):
            Ppsspp.launch(self.iso, self.home, port=free_port(), wait=5, root=self.root)

    def spawned(self):
        """Record every process launch() starts."""
        procs, real = [], subprocess.Popen
        patcher = mock.patch.object(ppsspp.subprocess, "Popen",
                                    wraps=lambda *a, **kw: procs.append(real(*a, **kw)) or procs[-1])
        patcher.start()
        self.addCleanup(patcher.stop)
        return procs

    def test_debugger_never_opens(self):
        self.app("exec sleep 60\n")
        procs = self.spawned()
        with self.assertRaisesRegex(PpssppError, "did not open the debugger"):
            Ppsspp.launch(self.iso, self.home, port=free_port(), wait=0.5, root=self.root)
        self.assertEqual(len(procs), 1)
        self.assertIsNotNone(procs[0].returncode, "the process was stopped and reaped")

    def test_stop_failure_keeps_the_launch_error(self):
        self.app("exit 3\n")
        with mock.patch.object(ppsspp, "_stop", side_effect=PpssppError("stuck")):
            with self.assertRaisesRegex(PpssppError, "exited with 3") as cm:
                Ppsspp.launch(self.iso, self.home, port=free_port(), wait=5, root=self.root)
        self.assertIn("stopping PPSSPP failed too: PpssppError('stuck')", cm.exception.__notes__)

    def test_busy_port_leaves_the_home_alone(self):
        self.app("exit 0\n")
        stale = self.home / ".config/ppsspp/PSP/SAVEDATA/x"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"x")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            with self.assertRaisesRegex(PpssppError, "in use.*lsof"):
                Ppsspp.launch(self.iso, self.home, port=s.getsockname()[1], wait=5, root=self.root)
        self.assertTrue(stale.exists())


if __name__ == "__main__":
    unittest.main()
