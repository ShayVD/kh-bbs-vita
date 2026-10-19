"""Drive PPSSPP's remote debugger: launch it with an isolated home, send
requests and match replies by ticket, queue every other event.

How PPSSPP is pointed at build/oracle/ppsspp-home instead of the user's own
config, the ini that makes the debugger listen, and every message used here
are documented in docs/oracle.md (found by P2 Task 1).
"""
import base64, collections, json, os, pathlib, shutil, socket, subprocess, time

from .ws import WebSocket

APP = pathlib.Path.home() / "Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL"
PORT = 45045
PROTOCOL = "debugger.ppsspp.org"
BUILD = pathlib.Path(__file__).resolve().parents[2] / "build"
LOG_NAME = "ppsspp-log.txt"

# Rewritten before every launch: PPSSPP rewrites the ini when it quits, and
# would keep a random port there if 45045 was taken.
INI = """[General]
FirstRun = False
AutoRun = False
CheckForNewVersion = False
RemoteDebuggerOnStartup = True
RemoteISOPort = 45045
[Graphics]
SoftwareRenderer = True
[Log]
SCEKERNELLevel = 4
"""


class PpssppError(Exception):
    pass


class Ppsspp:
    def __init__(self, ws, proc=None, timeout=10.0):
        self.ws, self.proc, self.timeout = ws, proc, timeout
        self.n = 0
        self.events = collections.deque()
        self.log_path = None

    @classmethod
    def connect(cls, host="127.0.0.1", port=PORT, timeout=10.0, proc=None):
        """Connect to a running PPSSPP. `timeout` is the default wait for any
        reply or event."""
        return cls(WebSocket.connect(host, port, "/debugger", PROTOCOL, timeout), proc, timeout)

    @classmethod
    def launch(cls, iso, home, port=PORT, wait=30.0):
        """Start PPSSPP on `iso` with a fresh config under `home` (inside
        build/), connect, and return once the game is stopped at its entry."""
        iso = pathlib.Path(iso)
        if not iso.is_file():
            raise PpssppError(f"no ISO at {iso}")
        if not APP.is_file():
            raise PpssppError(f"no PPSSPP at {APP}")
        home = prepare_home(home)
        if not port_free(port):
            raise PpssppError(f"port {port} is in use: PPSSPP would silently listen on another one")
        log = home / LOG_NAME
        env = dict(os.environ)
        env.update(isolated_env(home))
        proc = subprocess.Popen([str(APP), f"--log={log}", str(iso)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p = None
        try:
            deadline = time.monotonic() + wait
            while p is None:
                try:
                    p = cls.connect(port=port, proc=proc)
                except OSError as e:
                    # Also a WebSocketError: PPSSPP drops early handshakes while starting.
                    if proc.poll() is not None:
                        raise PpssppError(f"PPSSPP exited with {proc.returncode} before opening "
                                          f"the debugger (last error: {e})") from e
                    if time.monotonic() > deadline:
                        raise PpssppError(f"PPSSPP did not open the debugger on port {port} "
                                          f"within {wait} s (last error: {e})") from e
                    time.sleep(0.25)
            p.log_path = log
            p.wait_for_entry(max(deadline - time.monotonic(), 1.0))
            # Only now: before the game is loaded PPSSPP rejects the key
            # "logger" ("Unsupported 'disallowed' object key").
            p.request("broadcast.config.set", disallowed={"logger": True, "input": True})
            p._drop("log", "input.analog")
            return p
        except BaseException:
            if p:
                p.close()
            else:
                _stop(proc)
            raise

    def send(self, event, **params):
        """Send a request without waiting; returns its ticket."""
        self.n += 1
        ticket = str(self.n)
        self.ws.sock.settimeout(self.timeout)                     # _wait may have shortened it
        self.ws.send_text(json.dumps({"event": event, "ticket": ticket, **params}))
        return ticket

    def request(self, event, **params):
        """Send a request and return the reply with its ticket. An error reply
        raises PpssppError. Only for events that reply (not cpu.resume or
        cpu.stepping, docs/oracle.md)."""
        ticket = self.send(event, **params)
        msg = self._wait(lambda m: m.get("ticket") == ticket, None, event)
        if msg.get("event") == "error":
            raise PpssppError(f"{event}: {msg.get('message')}")
        return msg

    def next_event(self, name=None, timeout=None):
        """The next queued or incoming event (of type `name`, if given)."""
        return self._wait(lambda m: name is None or m.get("event") == name, timeout,
                          name or "an event")

    def wait_stepping(self, timeout=None):
        """The next `cpu.stepping` event ({pc, ticks, ...}), for example after
        a stopping breakpoint."""
        return self._wait(lambda m: m.get("event") == "cpu.stepping" and "ticket" not in m,
                          timeout, "cpu.stepping")

    def resume(self):
        """Run the game. Queued `cpu.stepping` events describe stops that are
        over now, so they are dropped first."""
        self._drop("cpu.stepping", "cpu.resume")
        ticket = self.send("cpu.resume")
        msg = self._wait(lambda m: m.get("ticket") == ticket or
                         (m.get("event") == "cpu.resume" and "ticket" not in m), None, "cpu.resume")
        if msg.get("event") == "error":
            raise PpssppError(f"cpu.resume: {msg.get('message')}")

    def pause(self, timeout=None):
        """Stop the game (if it runs) and return the pc."""
        status = self.request("cpu.status")
        if status["stepping"]:
            return status["pc"]
        self._drop("cpu.stepping")
        ticket = self.send("cpu.stepping")
        msg = self._wait(lambda m: m.get("ticket") == ticket or
                         (m.get("event") == "cpu.stepping" and "ticket" not in m), timeout, "cpu.stepping")
        if msg.get("event") == "error":
            raise PpssppError(f"cpu.stepping: {msg.get('message')}")
        return msg["pc"]

    def wait_for_entry(self, timeout, poll=0.1):
        """Poll `cpu.status` until the game stops at its entry (AutoRun = False).
        Before the game is loaded it reads `paused: true, pc: 0`."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.request("cpu.status")
            if status["stepping"]:
                return status
            if time.monotonic() > deadline:
                raise PpssppError(f"the game did not stop at its entry within {timeout} s "
                                  f"(last cpu.status: {status})")
            time.sleep(poll)

    def regs(self, thread=None):
        """The GPRs (zero..ra, pc, hi, lo) of the current or the given thread, name -> uint."""
        params = {} if thread is None else {"thread": thread}
        for cat in self.request("cpu.getAllRegs", **params)["categories"]:
            if cat["id"] == 0:
                return dict(zip(cat["registerNames"], cat["uintValues"]))
        raise PpssppError("cpu.getAllRegs: no GPR category")

    def add_breakpoint(self, addr, enabled=True, log=False, condition=None, log_format=None):
        """`enabled` stops execution, `log` writes `log_format` to the log file.
        A breakpoint at the same address is replaced."""
        params = {"address": addr, "enabled": enabled, "log": log}
        if condition is not None:
            params["condition"] = condition
        if log_format is not None:
            params["logFormat"] = log_format
        return self.request("cpu.breakpoint.add", **params)

    def remove_breakpoint(self, addr):
        return self.request("cpu.breakpoint.remove", address=addr)

    def read_memory(self, addr, size, chunk=1 << 20):
        out = bytearray()
        for a in range(addr, addr + size, chunk):
            n = min(chunk, addr + size - a)
            out += base64.b64decode(self.request("memory.read", address=a, size=n)["base64"])
        return bytes(out)

    def close(self):
        """Close the connection and stop the PPSSPP this object started, if any."""
        try:
            self.ws.close()
        except OSError:
            pass
        if self.proc:
            _stop(self.proc)
            self.proc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _wait(self, match, timeout, what):
        """The first queued, then the first incoming message that `match`es;
        every other message is queued. A timeout raises PpssppError and leaves
        the connection unusable (ws.py): close the session."""
        for i, m in enumerate(self.events):
            if match(m):
                del self.events[i]
                return m
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise PpssppError(f"timed out after {limit} s waiting for {what}")
            self.ws.sock.settimeout(left)
            try:
                m = json.loads(self.ws.recv_text())
            except TimeoutError as e:
                raise PpssppError(f"timed out after {limit} s waiting for {what}") from e
            if match(m):
                return m
            self.events.append(m)

    def _drop(self, *names):
        self.events = collections.deque(m for m in self.events if m.get("event") not in names)


def prepare_home(home, root=BUILD):
    """Delete and rewrite `home`/.config/ppsspp (fresh memory stick, minimal
    ini) and remove the log file. `home` must lie under `root`. Returns the
    absolute `home`."""
    if not str(home):
        raise PpssppError("empty PPSSPP home")
    home, root = pathlib.Path(home).resolve(), pathlib.Path(root).resolve()
    if root not in home.parents:
        raise PpssppError(f"refusing PPSSPP home {home}: not under {root}")
    cfg = home / ".config/ppsspp"
    if cfg.exists():
        shutil.rmtree(cfg)
    (cfg / "PSP/SYSTEM").mkdir(parents=True)
    (cfg / "PSP/SYSTEM/ppsspp.ini").write_text(INI)
    (home / LOG_NAME).unlink(missing_ok=True)
    return home


def isolated_env(home):
    """Environment that keeps PPSSPP, and AppKit's saved window state, under
    `home` (docs/oracle.md)."""
    home = str(pathlib.Path(home).resolve())
    return {"HOME": home, "CFFIXED_USER_HOME": home}


def port_free(port):
    """True if nothing listens on `port`. Both binds are needed on macOS: the
    dual-stack one misses an IPv4 wildcard listener, the IPv4 one misses ::1."""
    for family, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        with socket.socket(family) as s:
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            try:
                s.bind((addr, port))
            except OSError:
                return False
    return True


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
