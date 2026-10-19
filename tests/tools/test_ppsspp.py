"""ppsspp.py's request/ticket protocol against FakePpsspp. Never starts PPSSPP."""
import pathlib, socket, sys, tempfile, time, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from fakeppsspp import FakePpsspp  # noqa: E402
from oracle.ppsspp import Ppsspp, PpssppError, isolated_env, port_free, prepare_home  # noqa: E402


def reply(m, **kw):
    return {"event": m["event"], "ticket": m["ticket"], **kw}


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
            "cpu.status": lambda m: reply(m, stepping=True, paused=False, pc=0x08A8BD58, ticks=0),
        })
        p = self.connect(srv)
        self.assertEqual(p.pause(), 0x08A8BD58)
        p.close()
        srv.join_checked(self)
        self.assertEqual([m["event"] for m in srv.received], ["cpu.status"])

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


if __name__ == "__main__":
    unittest.main()
