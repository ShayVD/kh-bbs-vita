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
TIMEOUT = 10.0
# Events dropped once broadcast.config.set has disallowed them.
NOISE = ("log", "input.analog")

# Written before every launch, so every run starts from the same settings
# (PPSSPP fills in the other keys itself).
INI = """[General]
FirstRun = False
AutoRun = False
CheckForNewVersion = False
RemoteDebuggerOnStartup = True
RemoteISOPort = {port}
[Graphics]
SoftwareRenderer = True
[Log]
SCEKERNELLevel = 4
"""


class PpssppError(Exception):
    pass


class Ppsspp:
    def __init__(self, ws, proc=None, timeout=TIMEOUT):
        self.ws, self.proc, self.timeout = ws, proc, timeout
        self.n = 0
        self.events = collections.deque()
        self.ignore = ()
        self.log_path = None

    @classmethod
    def connect(cls, host="127.0.0.1", port=PORT, timeout=TIMEOUT, proc=None):
        """Connect to a running PPSSPP. `timeout` is the default wait for any
        reply or event."""
        return cls(WebSocket.connect(host, port, "/debugger", PROTOCOL, timeout), proc, timeout)

    @classmethod
    def launch(cls, iso, home, port=PORT, wait=30.0, root=BUILD):
        """Start PPSSPP on `iso` with a fresh config under `home` (inside
        `root`), connect, and return once the game is stopped at its entry."""
        iso = pathlib.Path(iso)
        if not iso.is_file():
            raise PpssppError(f"no ISO at {iso}")
        if not pathlib.Path(APP).is_file():
            raise PpssppError(f"no PPSSPP at {APP}")
        if not port_free(port):
            raise PpssppError(f"port {port} is in use (see `lsof -nP -iTCP:{port} -sTCP:LISTEN`): "
                              f"PPSSPP would silently listen on another one")
        home = prepare_home(home, root, port)
        log = home / LOG_NAME
        env = dict(os.environ)
        env.update(isolated_env(home))
        # Own session: a Ctrl-C in the terminal reaches only Python, and
        # PPSSPP is stopped by close() or by the cleanup below.
        proc = subprocess.Popen([str(APP), f"--log={log}", str(iso)], env=env, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p = None
        try:
            deadline = time.monotonic() + wait
            p = cls._connect_until(proc, port, deadline, wait)
            p.log_path = log
            p.wait_for_entry(max(deadline - time.monotonic(), 1.0))
            # Only now: before the game is loaded PPSSPP rejects the key
            # "logger" ("Unsupported 'disallowed' object key").
            p.request("broadcast.config.set", disallowed={"logger": True, "input": True})
            p.ignore = NOISE
            p._drop(*NOISE)
            return p
        except BaseException as e:
            try:
                if p:
                    p.close()
                else:
                    _stop(proc)
            except BaseException as stop_error:                   # keep `e` as the error raised
                e.add_note(f"stopping PPSSPP failed too: {stop_error!r}")
            raise

    @classmethod
    def _connect_until(cls, proc, port, deadline, wait):
        """Connect to the PPSSPP in `proc` once its debugger listens."""
        while True:
            try:
                p = cls.connect(port=port, proc=proc,
                                timeout=max(0.1, min(TIMEOUT, deadline - time.monotonic())))
                p.timeout = TIMEOUT
                return p
            except OSError as e:
                # Also a WebSocketError: PPSSPP drops early handshakes while starting.
                if proc.poll() is not None:
                    raise PpssppError(f"PPSSPP exited with {proc.returncode} before opening "
                                      f"the debugger (last error: {e})") from e
                if time.monotonic() > deadline:
                    raise PpssppError(f"PPSSPP did not open the debugger on port {port} "
                                      f"within {wait} s (last error: {e})") from e
                time.sleep(0.25)

    def send(self, event, **params):
        """Send a request without waiting; returns its ticket."""
        self.n += 1
        ticket = str(self.n)
        try:
            self.ws.sock.settimeout(self.timeout)                 # _wait may have shortened it
            self.ws.send_text(json.dumps({"event": event, "ticket": ticket, **params}))
        except OSError as e:
            raise self._error(f"sending {event}", e) from e
        return ticket

    def request(self, event, **params):
        """Send a request and return the reply with its ticket. An error reply
        raises PpssppError. Only for events that reply (not cpu.resume or
        cpu.stepping, docs/oracle.md)."""
        return self._request(event, None, params)

    def next_event(self, name=None, timeout=None):
        """The next queued or incoming event (of type `name`, if given).
        `timeout` 0 takes a queued event or reads at most one waiting frame:
        a ping, or the first fragment of a large reply, then gives a timeout
        although a message is arriving. A timeout raises PpssppError; in the
        rare case it hits in the middle of a frame the connection is unusable
        afterwards (close the session)."""
        return self._wait(lambda m: name is None or m.get("event") == name, timeout,
                          name or "an event")

    def wait_stepping(self, timeout=None):
        """The next `cpu.stepping` event ({pc, ticks, ...}), for example after
        a stopping breakpoint. Timeouts as in next_event."""
        return self._wait(lambda m: m.get("event") == "cpu.stepping" and "ticket" not in m,
                          timeout, "cpu.stepping")

    def resume(self, timeout=None):
        """Run the game. Queued `cpu.stepping` events describe stops that are
        over now, so they are dropped first. Timeouts as in next_event."""
        self._drop("cpu.stepping", "cpu.resume")
        ticket = self.send("cpu.resume")
        msg = self._wait(lambda m: m.get("ticket") == ticket or
                         (m.get("event") == "cpu.resume" and "ticket" not in m), timeout, "cpu.resume")
        if msg.get("event") == "error":
            raise PpssppError(f"cpu.resume: {msg.get('message')}")

    def pause(self, timeout=None):
        """Stop the game (if it runs) and return the pc. Queued `cpu.stepping`
        events are dropped either way. `timeout` covers the whole call;
        timeouts as in next_event."""
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        status = self._request("cpu.status", limit, {})
        self._drop("cpu.stepping")
        if status["stepping"]:
            return status["pc"]
        ticket = self.send("cpu.stepping")
        msg = self._wait(lambda m: m.get("ticket") == ticket or
                         (m.get("event") == "cpu.stepping" and "ticket" not in m),
                         max(deadline - time.monotonic(), 0.0), "cpu.stepping")
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
        finally:                                                  # even on a second Ctrl-C
            if self.proc:
                proc, self.proc = self.proc, None
                _stop(proc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, event, timeout, params):
        ticket = self.send(event, **params)
        msg = self._wait(lambda m: m.get("ticket") == ticket, timeout, event)
        if msg.get("event") == "error":
            raise PpssppError(f"{event}: {msg.get('message')}")
        return msg

    def _wait(self, match, timeout, what):
        """The first queued, then the first incoming message that `match`es;
        every other message is queued (events in `self.ignore` are dropped).
        The deadline holds even while unrelated messages keep arriving.

        Frames are read one at a time after a select, so running out of time
        between frames leaves the connection usable. A timeout inside a frame
        (data stopped mid-frame) leaves it unusable (ws.py)."""
        for i, m in enumerate(self.events):
            if match(m):
                del self.events[i]
                return m
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            left = max(deadline - time.monotonic(), 0.0)
            try:
                # Once a frame has started, give its rest at least a second.
                self.ws.sock.settimeout(max(left, 1.0))
                text = self.ws.poll_text(left)
                m = None if text is None else json.loads(text)
            except TimeoutError as e:
                raise PpssppError(f"timed out after {limit} s inside a message while waiting "
                                  f"for {what}; the connection is unusable now") from e
            except (OSError, ValueError) as e:
                raise self._error(f"waiting for {what}", e) from e
            if m is not None and m.get("event") not in self.ignore:
                if match(m):
                    return m
                self.events.append(m)
            if time.monotonic() >= deadline:
                raise PpssppError(f"timed out after {limit} s waiting for {what}")

    def _drop(self, *names):
        self.events = collections.deque(m for m in self.events if m.get("event") not in names)

    def _error(self, what, e):
        """A PpssppError for a broken connection, with PPSSPP's exit code if it died."""
        msg = f"{what}: {type(e).__name__}: {e}"
        if self.proc and self.proc.poll() is not None:
            msg += f" (PPSSPP exited with {self.proc.returncode})"
        return PpssppError(msg)


def prepare_home(home, root=BUILD, port=PORT):
    """Delete and rewrite `home`/.config/ppsspp (fresh memory stick, minimal
    ini) and remove the log file. `home` must lie under `root`, with no
    symlink below it on the way to the config. Returns the absolute `home`."""
    if not str(home):
        raise PpssppError("empty PPSSPP home")
    home, root = pathlib.Path(home).resolve(), pathlib.Path(root).resolve()
    if root not in home.parents:
        raise PpssppError(f"refusing PPSSPP home {home}: not under {root}")
    cfg = home / ".config/ppsspp"
    if cfg.resolve() != cfg:
        raise PpssppError(f"refusing PPSSPP home {home}: {cfg} goes through a symlink")
    if cfg.exists():
        shutil.rmtree(cfg)
    (cfg / "PSP/SYSTEM").mkdir(parents=True)
    (cfg / "PSP/SYSTEM/ppsspp.ini").write_text(INI.format(port=port))
    (home / LOG_NAME).unlink(missing_ok=True)
    return home


def isolated_env(home):
    """Environment that keeps PPSSPP, and AppKit's saved window state, under
    `home` (docs/oracle.md)."""
    home = str(pathlib.Path(home).resolve())
    return {"HOME": home, "CFFIXED_USER_HOME": home}


def port_free(port):
    """True if nothing listens on `port`. The binds (SO_REUSEADDR, so a
    TIME_WAIT left by an earlier run does not count) catch wildcard
    listeners; the connects catch listeners on 127.0.0.1 or ::1 only."""
    for family, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        with socket.socket(family) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            try:
                s.bind((addr, port))
            except OSError:
                return False
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family) as s:
            s.settimeout(1.0)
            if s.connect_ex((addr, port)) == 0:
                return False
    return True


def _stop(proc):
    """SIGKILL: on SIGTERM or SIGINT PPSSPP crashes while quitting, and macOS
    writes a crash report each time (docs/oracle.md)."""
    proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired as e:
        raise PpssppError(f"PPSSPP (pid {proc.pid}) did not exit after SIGKILL") from e
