# P2: Oracle Tooling, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run PPSSPP and `khbbs` on the same game and report automatically where they first disagree: in the sequence of firmware calls, in guest memory at a chosen event, and (once P6 renders) in a frame.

**Architecture:** Everything lives in `tools/oracle/` as standard-library Python. A small RFC 6455 client (`ws.py`) talks to PPSSPP's remote debugger over WebSocket. `ppsspp.py` adds launching and the request/ticket protocol on top. Separate tools capture an HLE trace from PPSSPP in the same JSON-lines format `khbbs --trace` writes, diff two traces (`trace_diff.py`), take and diff memory snapshots (`mem_diff.py`), and grab and diff frames (`png.py`, `img_diff.py`). `oracle.py` ties these into one command. On our side, `khbbs` gains a snapshot-at-event option. PPSSPP stays a separate process, and nothing of its code is copied or linked.

**Tech Stack:** Python 3.11+ standard library only (`socket`, `json`, `zlib`, `struct`, `subprocess`), PPSSPP 1.20.4 (`~/Applications/PPSSPPSDL.app`), C11 for the `khbbs` side.

**Context an implementer needs:**
- Read `docs/architecture.md` §"Verification against PPSSPP", `docs/bringup.md`, and `runtime/trace.c` (the JSON-lines format: `seq`, `nid`, `name`, `ra`, `args[8]`, `ret`, `ret_hi`).
- PPSSPP is an **oracle only**: run it as a process and talk to it over its documented WebSocket API. Never copy, vendor or link its code. Reading its public API documentation to learn message names is fine.
- **Do not change the user's PPSSPP settings.** All PPSSPP runs use a throwaway config/memstick directory under `build/oracle/ppsspp-home/` (Task 1 finds out how PPSSPP 1.20.4 on macOS is pointed at one). Ask before installing anything.
- The ISO is the user's dump; its path comes from `KHBBS_ISO` (for example `~/Games/dumps/kh-bbs/<name>.iso`, see `docs/setup.md`). Captures (traces, snapshots, screenshots) go under `build/oracle/` and are never committed. Test fixtures are synthetic.
- Every address is a ULUS10505 v1.00 address. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Run `tools/check_no_assets.sh` before every commit. Work on branch `p2-oracle-tooling`.
- Python tests: `python3 -m unittest discover -s tests/tools -v` (CI runs this; P3 added it).

**Known unknowns, settled by Task 1:** how PPSSPP 1.20.4 enables the remote debugger and which port it listens on; whether its config directory can be redirected; the exact shape of the messages used here (`log`, `cpu.breakpoint.add`, `cpu.stepping`, `cpu.resume`, `cpu.getAllRegs`, `memory.read`, `game.status`, `hle.thread.list`, `gpu.buffer.screenshot`); and which source gives each firmware call's name, arguments and return value (HLE debug log lines, or log breakpoints on the import stubs). Later tasks are written against the API as documented. Where Task 1 finds a different field name, update the code and the fixtures in the same task, and say so in the report.

## File map

| File | Responsibility |
|---|---|
| `docs/oracle.md` | how to run the oracle, the PPSSPP facts Task 1 found, what each tool compares |
| `tools/oracle/__init__.py` | package marker |
| `tools/oracle/ws.py` | RFC 6455 client: handshake, masked text frames, fragments, ping/pong, close |
| `tools/oracle/ppsspp.py` | launch PPSSPP with an isolated home; request/ticket protocol; event queue |
| `tools/oracle/imports.py` | the EBOOT's import stubs: address → (NID, name) |
| `tools/oracle/capture_hle.py` | PPSSPP firmware calls → JSON lines in `khbbs`'s trace format |
| `tools/oracle/trace_diff.py` | align two traces, report the first divergence |
| `tools/oracle/snapshot.py` | PPSSPP memory at an event → raw dump + JSON sidecar |
| `tools/oracle/mem_diff.py` | diff two dumps by range, annotated with ELF sections |
| `tools/oracle/png.py` | minimal PNG read/write (8-bit RGB/RGBA) |
| `tools/oracle/frame.py`, `img_diff.py` | PPSSPP screenshot at frame N; PSNR and mismatch mask |
| `tools/oracle/oracle.py` | `first-divergence`: run both sides, diff, report |
| `runtime/snapshot.c`, `runtime/include/khbbs/snapshot.h`, `hosts/pc/main.c` | `khbbs --snapshot-at NAME:K --snapshot FILE` |
| `tests/tools/test_ws.py`, `test_ppsspp.py`, `test_capture_hle.py`, `test_trace_diff.py`, `test_mem_diff.py`, `test_png.py`, `test_img_diff.py`, `tests/tools/fakeppsspp.py` | unit tests, synthetic only |
| `tests/runtime/test_snapshot.c` | runtime test |

---

### Task 1: Spike: what PPSSPP 1.20.4 actually offers

No code is committed except `docs/oracle.md`. The aim is facts.

**Files:** Create `docs/oracle.md`.

- [ ] **Step 1: Isolated home**

Find out from PPSSPP's documentation and `PPSSPPSDL --help` how to point the SDL build at a config/memstick directory other than the user's (`--memstick`/a `PSP` directory next to an `installed.txt`, or `XDG_CONFIG_HOME`/`HOME` for the SDL build). Run:

```bash
mkdir -p build/oracle/ppsspp-home
~/Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL --help 2>&1 | head -60
```

Then start it once with the isolation you chose, quit it, and confirm a fresh `ppsspp.ini` appeared under `build/oracle/ppsspp-home/` and that nothing changed under `~/Library/Application Support`, `~/.config` or `~/Documents/PPSSPP` (`ls -la` those before and after). Record the method.

- [ ] **Step 2: Enable the remote debugger and fix its port**

In the isolated `ppsspp.ini`, find the settings for the remote debugger (the developer-tools option "Allow remote debugger" and the web server port). Set them so the debugger starts at launch on a fixed port (pick `45045`). Start PPSSPP with the game:

```bash
~/Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL "$KHBBS_ISO" &
```

and check that something listens: `lsof -nP -iTCP:45045 -sTCP:LISTEN`. Record the ini keys and values.

- [ ] **Step 3: Talk to it by hand**

With a scratch script under `build/oracle/` (not committed) using the raw socket handshake from Task 2's design, connect to `ws://127.0.0.1:45045/debugger` with subprotocol `debugger.ppsspp.org`, and send, each with a `ticket`:
`{"event":"version"}`, `{"event":"game.status"}`, `{"event":"cpu.getAllRegs"}` (while stepping), `{"event":"memory.read","address":134217984,"size":16}`, `{"event":"hle.thread.list"}`, `{"event":"cpu.breakpoint.add","address":<a stub>,"enabled":false,"log":true,"logFormat":"{a0} {a1}"}`, `{"event":"gpu.buffer.screenshot","type":"base64"}`. Record each reply's fields, how errors look, how stepping is reported (`cpu.stepping`), and how `log` events look.

- [ ] **Step 4: Choose the firmware-call source**

Two candidates:
1. PPSSPP's HLE logging. Raise the HLE-related log channels to debug in the isolated ini, subscribe to `log` events, and look at the lines for the first calls (`sceKernelSetCompilerVersion`, `sceKernelCreateThread`, `sceKernelStartThread`). Is there one line per call with name, arguments and return value?
2. Log breakpoints on the 241 called import stubs (`.sceStub.text`, 8 bytes each, from `0x08B300A8`), each with a `logFormat` that prints the stub address and `a0`–`t3`. That gives arguments but no return value; check whether a log breakpoint on the caller's `$ra` (dynamic) is possible or whether returns must come from (1).

Pick the source (or the combination) that yields name, arguments and return per call, in order, with the thread. Record a few real lines with every argument value **replaced by a made-up one**; those rewritten lines become Task 4's fixtures.

- [ ] **Step 5: Write `docs/oracle.md`**

Sections: "Isolated PPSSPP home" (exact steps), "Remote debugger" (ini keys, port, URL, subprotocol), "Messages we use" (one line per event with request and reply fields), "Firmware-call source" (the choice and the line format, with the rewritten sample lines), "Determinism" (PPSSPP is not deterministic across runs; we key comparisons on events, not time).

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add docs/oracle.md
git commit -m "docs: oracle.md, what PPSSPP 1.20.4's remote debugger offers and how we run it"
```

---

### Task 2: A standard-library WebSocket client

**Files:** Create `tools/oracle/__init__.py` (empty), `tools/oracle/ws.py`, `tests/tools/test_ws.py`.

- [ ] **Step 1: Write the failing test**

`tests/tools/test_ws.py`:

```python
"""ws.py against an in-process RFC 6455 server. No network beyond localhost."""
import base64, hashlib, pathlib, socket, struct, sys, threading, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle.ws import WebSocket, WebSocketError  # noqa: E402

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(opcode, payload, fin=True):
    """A server frame: never masked."""
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def read_client_frame(conn):
    b0, b1 = conn.recv(2, socket.MSG_WAITALL)
    assert b1 & 0x80, "client frames must be masked"
    n = b1 & 0x7F
    if n == 126:
        n, = struct.unpack(">H", conn.recv(2, socket.MSG_WAITALL))
    elif n == 127:
        n, = struct.unpack(">Q", conn.recv(8, socket.MSG_WAITALL))
    mask = conn.recv(4, socket.MSG_WAITALL)
    data = conn.recv(n, socket.MSG_WAITALL) if n else b""
    return b0 & 0x0F, bytes(c ^ mask[i & 3] for i, c in enumerate(data))


class Server(threading.Thread):
    """Accepts one client, checks the handshake, then runs `script(conn)`."""

    def __init__(self, script, accept_key_ok=True, protocol="debugger.ppsspp.org"):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script, self.accept_key_ok, self.protocol = script, accept_key_ok, protocol
        self.request = b""

    def run(self):
        conn, _ = self.sock.accept()
        while b"\r\n\r\n" not in self.request:
            self.request += conn.recv(4096)
        key = [l.split(b":", 1)[1].strip() for l in self.request.split(b"\r\n")
               if l.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()) if self.accept_key_ok else b"bogus"
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\nSec-WebSocket-Protocol: " +
                     self.protocol.encode() + b"\r\n\r\n")
        try:
            self.script(conn)
        finally:
            conn.close()
            self.sock.close()


class WsTest(unittest.TestCase):
    def test_echo_ping_fragments_and_large(self):
        got = []

        def script(conn):
            op, data = read_client_frame(conn)
            got.append((op, data))
            conn.sendall(frame(0x9, b"hi"))                       # ping: client must pong
            op, data = read_client_frame(conn)
            got.append((op, data))
            conn.sendall(frame(0x1, b"echo:" + got[0][1]))
            conn.sendall(frame(0x1, b"frag", fin=False) + frame(0x0, b"ment"))
            conn.sendall(frame(0x1, b"x" * 70000))                # 64-bit length
            read_client_frame(conn)                                # close

        srv = Server(script)
        srv.start()
        ws = WebSocket.connect("127.0.0.1", srv.port, "/debugger", protocol="debugger.ppsspp.org")
        ws.send_text('{"event":"version"}')
        self.assertEqual(ws.recv_text(), 'echo:{"event":"version"}')
        self.assertEqual(ws.recv_text(), "fragment")
        self.assertEqual(len(ws.recv_text()), 70000)
        ws.close()
        srv.join(5)
        self.assertEqual(got[0], (0x1, b'{"event":"version"}'))
        self.assertEqual(got[1], (0xA, b"hi"))
        self.assertIn(b"GET /debugger HTTP/1.1", srv.request)
        self.assertIn(b"Sec-WebSocket-Protocol: debugger.ppsspp.org", srv.request)

    def test_bad_accept_refused(self):
        srv = Server(lambda conn: None, accept_key_ok=False)
        srv.start()
        with self.assertRaises(WebSocketError):
            WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        srv.join(5)

    def test_close_from_server(self):
        srv = Server(lambda conn: conn.sendall(frame(0x8, b"")))
        srv.start()
        ws = WebSocket.connect("127.0.0.1", srv.port, "/")
        with self.assertRaises(WebSocketError):
            ws.recv_text()
        srv.join(5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m unittest tests.tools.test_ws -v` (from the repo root; if module discovery complains, `python3 -m unittest discover -s tests/tools -p 'test_ws.py' -v`)
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle'`.

- [ ] **Step 3: Write the client**

`tools/oracle/ws.py`:

```python
"""A minimal RFC 6455 WebSocket client, standard library only.

Enough for PPSSPP's remote debugger: one connection, text messages, client
masking, fragmented and large (64-bit length) server frames, ping/pong and
close. No TLS, no extensions.
"""
import base64, hashlib, os, socket, struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    @classmethod
    def connect(cls, host, port, path="/", protocol=None, timeout=10.0):
        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16))
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key.decode()}\r\n"
               f"Sec-WebSocket-Version: 13\r\n")
        if protocol:
            req += f"Sec-WebSocket-Protocol: {protocol}\r\n"
        sock.sendall((req + "\r\n").encode())
        ws = cls(sock)
        head = ws._read_until(b"\r\n\r\n").decode("latin-1").split("\r\n")
        if not head[0].startswith("HTTP/1.1 101"):
            sock.close()
            raise WebSocketError(f"handshake refused: {head[0]}")
        hdrs = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in head[1:] if ":" in l)}
        want = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
        if hdrs.get("sec-websocket-accept") != want:
            sock.close()
            raise WebSocketError("bad Sec-WebSocket-Accept")
        return ws

    def _fill(self):
        chunk = self.sock.recv(1 << 20)
        if not chunk:
            raise WebSocketError("connection closed")
        self.buf += chunk

    def _read_until(self, marker):
        while (i := self.buf.find(marker)) < 0:
            self._fill()
        head = bytes(self.buf[:i])
        del self.buf[:i + len(marker)]
        return head

    def _read_exact(self, n):
        while len(self.buf) < n:
            self._fill()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _send(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        # Mask four bytes at a time through int arithmetic; per-byte Python is slow.
        m32 = int.from_bytes(mask * ((n + 3) // 4), "little")
        body = (int.from_bytes(payload + b"\0" * (-n % 4), "little") ^ m32).to_bytes(n + (-n % 4), "little")[:n]
        self.sock.sendall(head + mask + body)

    def send_text(self, text):
        self._send(OP_TEXT, text.encode())

    def recv_text(self):
        """The next complete text message. Answers pings; raises on close."""
        parts = []
        while True:
            b0, b1 = self._read_exact(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n, = struct.unpack(">H", self._read_exact(2))
            elif n == 127:
                n, = struct.unpack(">Q", self._read_exact(8))
            mask = self._read_exact(4) if b1 & 0x80 else None
            data = self._read_exact(n)
            if mask:
                data = bytes(c ^ mask[i & 3] for i, c in enumerate(data))
            if opcode == OP_PING:
                self._send(OP_PONG, data)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise WebSocketError("closed by peer")
            parts.append(data)
            if fin:
                return b"".join(parts).decode()

    def close(self):
        try:
            self._send(OP_CLOSE, b"")
        except OSError:
            pass
        finally:
            self.sock.close()
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/__init__.py tools/oracle/ws.py tests/tools/test_ws.py
git commit -m "oracle: standard-library WebSocket client"
```

---

### Task 3: The PPSSPP session

**Files:** Create `tools/oracle/ppsspp.py`, `tests/tools/fakeppsspp.py`, `tests/tools/test_ppsspp.py`.

- [ ] **Step 1: A fake PPSSPP for tests**

`tests/tools/fakeppsspp.py`: a `FakePpsspp` built on `test_ws.Server` (import its `frame`/`read_client_frame`/`Server`) that, for each client text frame, decodes the JSON and replies according to a dict of handlers `{event: fn(msg) -> reply dict or list of dicts}`. A list sends several messages (for example a `log` event and then the reply), so tests can check that unrelated events are queued. Unknown events get `{"event": "error", "ticket": msg["ticket"], "message": "unknown event"}`. The field names follow `docs/oracle.md` from Task 1.

```python
"""A stand-in for PPSSPP's remote debugger, for tests. Speaks JSON over test_ws.Server."""
import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_ws import Server, frame, read_client_frame  # noqa: E402


class FakePpsspp(Server):
    def __init__(self, handlers):
        self.handlers = handlers
        self.received = []
        super().__init__(self._serve)

    def _serve(self, conn):
        while True:
            try:
                op, data = read_client_frame(conn)
            except (ValueError, OSError):
                return
            if op == 0x8:
                return
            msg = json.loads(data)
            self.received.append(msg)
            fn = self.handlers.get(msg["event"])
            replies = fn(msg) if fn else {"event": "error", "ticket": msg.get("ticket"),
                                          "message": "unknown event"}
            for r in replies if isinstance(replies, list) else [replies]:
                conn.sendall(frame(0x1, json.dumps(r).encode()))
```

- [ ] **Step 2: Write the failing test**

`tests/tools/test_ppsspp.py`:

```python
"""ppsspp.py's request/ticket protocol against FakePpsspp."""
import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from fakeppsspp import FakePpsspp  # noqa: E402
from oracle.ppsspp import Ppsspp, PpssppError  # noqa: E402


class PpssppTest(unittest.TestCase):
    def test_request_reply_and_queued_events(self):
        srv = FakePpsspp({
            "version": lambda m: [{"event": "log", "message": "unrelated"},
                                  {"event": "version", "ticket": m["ticket"], "name": "PPSSPP", "version": "v1.20.4"}],
            "memory.read": lambda m: {"event": "memory.read", "ticket": m["ticket"], "base64": "AAECAw=="},
        })
        srv.start()
        p = Ppsspp.connect(port=srv.port)
        self.assertEqual(p.request("version")["version"], "v1.20.4")
        self.assertEqual(p.read_memory(0x08800000, 4), b"\x00\x01\x02\x03")
        self.assertEqual(p.next_event("log")["message"], "unrelated")
        with self.assertRaises(PpssppError):
            p.request("nope")
        p.close()
        srv.join(5)
        tickets = [m["ticket"] for m in srv.received]
        self.assertEqual(len(set(tickets)), len(tickets), "tickets are unique")
        self.assertEqual(srv.received[1]["address"], 0x08800000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python3 -m unittest discover -s tests/tools -p 'test_ppsspp.py' -v`
Expected: FAIL, no module `oracle.ppsspp`.

- [ ] **Step 4: Write the session**

`tools/oracle/ppsspp.py`:

```python
"""Drive PPSSPP's remote debugger: launch it with an isolated home, send
requests and match replies by ticket, queue every other event.

The settings that make the debugger listen, and the way PPSSPP is pointed at
build/oracle/ppsspp-home instead of the user's config, are documented in
docs/oracle.md (found by P2 Task 1).
"""
import base64, collections, json, os, pathlib, subprocess, time

from .ws import WebSocket, WebSocketError

APP = pathlib.Path.home() / "Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL"
PORT = 45045
PROTOCOL = "debugger.ppsspp.org"


class PpssppError(Exception):
    pass


class Ppsspp:
    def __init__(self, ws, proc=None):
        self.ws, self.proc = ws, proc
        self.n = 0
        self.events = collections.deque()

    @classmethod
    def connect(cls, host="127.0.0.1", port=PORT, timeout=10.0, proc=None):
        return cls(WebSocket.connect(host, port, "/debugger", PROTOCOL, timeout), proc)

    @classmethod
    def launch(cls, iso, home, port=PORT, wait=30.0):
        """Start PPSSPP on `iso` with config under `home`, and connect once it
        listens. `home` is prepared as docs/oracle.md describes."""
        env = dict(os.environ)
        env.update(isolated_env(pathlib.Path(home)))
        proc = subprocess.Popen([str(APP), str(iso)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + wait
        while True:
            try:
                return cls.connect(port=port, proc=proc)
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    proc.kill()
                    raise PpssppError(f"PPSSPP did not open the debugger on port {port}")
                time.sleep(0.25)

    def request(self, event, **params):
        self.n += 1
        ticket = str(self.n)
        self.ws.send_text(json.dumps({"event": event, "ticket": ticket, **params}))
        while True:
            msg = json.loads(self.ws.recv_text())
            if msg.get("ticket") == ticket:
                if msg.get("event") == "error":
                    raise PpssppError(f"{event}: {msg.get('message')}")
                return msg
            self.events.append(msg)

    def next_event(self, name=None):
        """The next queued or incoming event (of type `name`, if given)."""
        while True:
            for i, m in enumerate(self.events):
                if name is None or m.get("event") == name:
                    del self.events[i]
                    return m
            self.events.append(json.loads(self.ws.recv_text()))

    def read_memory(self, addr, size, chunk=1 << 20):
        out = bytearray()
        for a in range(addr, addr + size, chunk):
            n = min(chunk, addr + size - a)
            out += base64.b64decode(self.request("memory.read", address=a, size=n)["base64"])
        return bytes(out)

    def close(self):
        try:
            self.ws.close()
        except (OSError, WebSocketError):
            pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def isolated_env(home):
    """Environment that keeps PPSSPP's config under `home` (docs/oracle.md)."""
    return {"HOME": str(home)}
```

Replace the body of `isolated_env` (and, if Task 1 chose a command-line flag instead, the `Popen` argument list) with exactly what `docs/oracle.md` records. Rename `base64`/`address`/`size` if Task 1 found other field names.

- [ ] **Step 5: Run the tests, then try it on the real emulator**

Run: `python3 -m unittest discover -s tests/tools -v` → `OK`.

Then, by hand:

```bash
python3 -c "
import sys; sys.path.insert(0, 'tools')
from oracle.ppsspp import Ppsspp
import os
p = Ppsspp.launch(os.environ['KHBBS_ISO'], 'build/oracle/ppsspp-home')
print(p.request('version')); print(p.request('game.status')); p.close()"
```

Expected: the version reply names 1.20.4, `game.status` names ULUS10505.

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/ppsspp.py tests/tools/fakeppsspp.py tests/tools/test_ppsspp.py
git commit -m "oracle: PPSSPP session (isolated launch, tickets, event queue, memory reads)"
```

---

### Task 4: Capture PPSSPP's firmware calls as a `khbbs`-format trace

**Files:** Create `tools/oracle/imports.py`, `tools/oracle/capture_hle.py`, `tests/tools/test_capture_hle.py`.

The output is one JSON object per call, in the same shape `runtime/trace.c` writes, plus a `thread` field (PPSSPP knows the current thread; `khbbs` will once P4 has threads):

```json
{"seq":0,"nid":"0x35669D4C","name":null,"ra":"0x08A8BD7C","args":["0x06020010",...8],"ret":"0x00000000","ret_hi":"0x00000000","thread":"user_main"}
```

Unknown fields are `null`. `trace_diff.py` (Task 5) only compares the fields both sides have.

- [ ] **Step 1: Import stubs**

`tools/oracle/imports.py` reads the EBOOT's import table the way `tools/import_report.py` does (reuse its parsing functions by importing it; do not copy them) and returns `{stub_addr: (nid, name_or_None)}`. Test it in `test_capture_hle.py` on a synthetic ELF built with `tests/tools/elfgen.py`, with a `.rodata.sceModuleInfo`, one `.lib.stub` entry, a two-NID `.rodata.sceNid` table and an 16-byte `.sceStub.text`. If `import_report.py`'s functions need the module-info layout spelled out, put the synthetic builder in `elfgen.py` as `make_module_elf(imports)` so later tests can use it too.

- [ ] **Step 2: Write the failing parser test**

Using the rewritten sample lines from `docs/oracle.md` (Task 1, Step 4), write `tests/tools/test_capture_hle.py::test_parse_lines`: feed the lines (as `log` event dicts, or as breakpoint-log lines, whichever Task 1 chose) through `capture_hle.parse(events, stubs, nid_names)` and assert the produced records' `seq`, `nid`, `name`, `args` (padded to 8 with `null`), `ret` and `thread`. Include one call with fewer than 8 arguments, one unknown function name, one 64-bit return (if the chosen source shows `v1`), and one log line that is not a firmware call (must be skipped).

- [ ] **Step 3: Implement `parse` and the capture loop**

`capture_hle.py` has:
- `parse(events, stubs, nid_names) -> iterator of dicts`, pure and tested;
- `capture(p: Ppsspp, n: int, out_path)`: sets up the chosen source (log levels through the request Task 1 found, or one log breakpoint per stub with `logFormat` naming the stub address and `a0`–`t3`), resumes the emulator, reads events until `n` calls are recorded, pauses the emulator (`cpu.stepping` request), and writes JSON lines;
- a CLI: `python3 -m oracle.capture_hle --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF -n 200 -o build/oracle/ppsspp.jsonl` (run from `tools/`, or with `PYTHONPATH=tools`).

Name resolution: the stub's NID from `imports.py`, its name from `tools/nid_names.txt` through the SHA-1 check `import_report.py` uses, else `null` (as `khbbs` writes it).

- [ ] **Step 4: Run tests and a real capture**

Run: `python3 -m unittest discover -s tests/tools -v` → `OK`. Then:

```bash
PYTHONPATH=tools python3 -m oracle.capture_hle --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF -n 50 -o build/oracle/ppsspp.jsonl
head -5 build/oracle/ppsspp.jsonl
```

Expected: the first records are `0x35669D4C` (a0 `0x06020010`), `sceKernelSetCompilerVersion` (a0 `0x00030306`), `sceKernelCreateThread`, `sceKernelStartThread`, the same order `docs/bringup.md` records for `khbbs`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/imports.py tools/oracle/capture_hle.py tests/tools/test_capture_hle.py tests/tools/elfgen.py
git commit -m "oracle: capture PPSSPP's firmware calls in khbbs's trace format"
```

---

### Task 5: `trace_diff.py`

**Files:** Create `tools/oracle/trace_diff.py`, `tests/tools/test_trace_diff.py`.

Rules:
- Records are compared in order, per thread when both sides carry `thread`, else as one stream.
- Compared fields: `nid`, and each `args[i]` and `ret` that are non-`null` on both sides. `seq`, `ra` and `name` are reported, not compared (`ra` differs once overlays or HLE stubs differ, and is noise at first). `--compare-ra` turns `ra` on.
- Values listed with `--ignore-arg NID:i` (for example a thread UID, which PPSSPP and `khbbs` allocate differently) are skipped, and `--map-uids` learns a UID mapping from returns (`sceKernelCreate*` returns a UID on each side) and applies it to later arguments.
- Output: the number of matching records, then the first divergence with three records of context on each side, both records shown in full and the differing fields named. Exit 0 if the first `-n` records match, 1 otherwise, 2 on bad input.

- [ ] **Step 1: Write the failing test**

`tests/tools/test_trace_diff.py`, with traces built in the test as lists of dicts written to temp files:

```python
"""trace_diff.py on synthetic traces."""
import json, pathlib, subprocess, sys, tempfile, unittest

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m unittest discover -s tests/tools -p 'test_trace_diff.py' -v` → FAIL (no tool).

- [ ] **Step 3: Implement**

`tools/oracle/trace_diff.py`: `load(path)` → list of dicts (error and exit 2 on a line that is not JSON); `streams(recs)` → `{thread: [recs]}` when every record has a `thread`, else `{None: recs}`; `compare(a, b, ignore, uid_map) -> list of differing field names` (applying the UID map to `b` values, learning from `ret` of records whose name or NID is a `sceKernelCreate*`/`sceKernelAllocPartitionMemory`-style UID returner when `--map-uids` is set: simply learn whenever both returns are non-zero, non-null and differ, and the same NID returns UIDs consistently); `main()` walks each stream pairwise up to `-n` records, stops at the first difference, prints as specified above. Keep it under 200 lines.

- [ ] **Step 4: Run the tests and compare a real pair**

Run: `python3 -m unittest discover -s tests/tools -v` → `OK`.

```bash
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --trace build/oracle/khbbs.jsonl --budget 50000000
python3 tools/oracle/trace_diff.py build/oracle/khbbs.jsonl build/oracle/ppsspp.jsonl --map-uids
```

Expected: a report. Record where the first divergence is in your report; it is information, not a failure.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/trace_diff.py tests/tools/test_trace_diff.py
git commit -m "oracle: trace_diff.py reports the first divergent firmware call"
```

---

### Task 6: Memory snapshots on both sides, and `mem_diff.py`

The event is "just before the K-th call to firmware function NAME" (`NAME:K`, K from 1), because it means the same thing in both emulators even though their timing differs.

**Files:**
- Create: `runtime/include/khbbs/snapshot.h`, `runtime/snapshot.c`; Modify: `runtime/CMakeLists.txt`, `runtime/trace.c` (call the snapshot hook), `hosts/pc/main.c`
- Create: `tests/runtime/test_snapshot.c`; Modify: `tests/runtime/CMakeLists.txt`
- Create: `tools/oracle/snapshot.py`, `tools/oracle/mem_diff.py`, `tests/tools/test_mem_diff.py`

- [ ] **Step 1: Our side, test first**

`khbbs --snapshot-at sceKernelCreateThread:1 --snapshot build/oracle/khbbs-mem.bin` writes guest RAM `[0x08000000, 0x0A000000)` as raw bytes plus `build/oracle/khbbs-mem.bin.json` (`{"base":"0x08000000","size":33554432,"event":"sceKernelCreateThread:1","side":"khbbs"}`) at the moment the HLE trace hook sees that call's entry. Note that `runtime/trace.c`'s hook fires **after** the call (it carries the return value). Add an entry-time hook if psprecomp's `psp_hle_set_trace` has none: a second, optional `psp_hle_set_pre_call(fn)` in the fork (test first in the fork, commit there, bump the pin as in P3 Task 16). Write `tests/runtime/test_snapshot.c` to arm the snapshot, call the hook twice with the matching name, and check the file appears once, at the first match, with the sidecar.

- [ ] **Step 2: PPSSPP side**

`tools/oracle/snapshot.py --iso … --elf … --at sceKernelCreateThread:1 -o build/oracle/ppsspp-mem.bin`: put an execution breakpoint on that function's import stub (from `imports.py`), resume, count `cpu.stepping` events at that address until the K-th, read the same range with `Ppsspp.read_memory`, and write the same sidecar with `"side":"ppsspp"`.

- [ ] **Step 3: `mem_diff.py`, test first**

`python3 tools/oracle/mem_diff.py A.bin B.bin [--elf game/EBOOT.dec.ELF] [--ignore 0x09F3D7D0-0x0A000000] [--gap 16]` compares the two dumps (sidecars must agree on base and size), merges differences closer than `--gap` bytes into ranges, prints each range with its size and the ELF section it falls in (`.data`, `.bss`, …, or `user partition` / `kernel partition` outside the image), and a summary of differing bytes per section. Exit 0 if no difference outside `--ignore`, 1 otherwise. Test it on synthetic 4 KB dumps with three planted differences (two merged by the gap, one separate, one inside an ignored range) and a synthetic ELF from `elfgen.py`.

- [ ] **Step 4: Run tests, try it, commit**

Run: `python3 -m unittest discover -s tests/tools -v`, `cmake --build build/pc && ctest --test-dir build/pc --output-on-failure`, then both snapshots at `sceKernelCreateThread:1` and `mem_diff.py` over them. Record the result.

```bash
tools/check_no_assets.sh
git add runtime/include/khbbs/snapshot.h runtime/snapshot.c runtime/CMakeLists.txt runtime/trace.c hosts/pc/main.c \
        tests/runtime/test_snapshot.c tests/runtime/CMakeLists.txt tools/oracle/snapshot.py tools/oracle/mem_diff.py \
        tests/tools/test_mem_diff.py
git commit -m "oracle: memory snapshots at NAME:K on both sides; mem_diff.py"
```

---

### Task 7: Frames: PNG, PPSSPP screenshots, `img_diff.py`

`khbbs` draws nothing until P6. This task builds the PPSSPP side and the diff, tested on synthetic images, so P6 only has to write its framebuffer as a PNG.

**Files:** Create `tools/oracle/png.py`, `tools/oracle/frame.py`, `tools/oracle/img_diff.py`, `tests/tools/test_png.py`, `tests/tools/test_img_diff.py`.

- [ ] **Step 1: `png.py`, test first**

`read(path) -> (width, height, rows)` where rows are `bytes` of RGBA8; `write(path, width, height, rows)`. Supports what PPSSPP's screenshots and our backends write: 8-bit RGB and RGBA, non-interlaced, all five filter types on read, filter 0 on write, CRC checked. `test_png.py`: write a 3×2 RGBA image, read it back identical; read a hand-built RGB PNG whose rows use filters 1–4 (build it in the test with `zlib` and `struct`, computing the filtered bytes from known pixels) and check the pixels; a corrupted CRC raises `ValueError`.

- [ ] **Step 2: `img_diff.py`, test first**

`python3 tools/oracle/img_diff.py a.png b.png [--mask out.png] [--psnr-min 40] [--tolerance 2]` prints the PSNR over RGB, the count and bounding box of pixels whose any channel differs by more than `--tolerance`, writes a mask (differing pixels red over a dimmed copy of `a`), and exits 0 if PSNR ≥ `--psnr-min` (or the images are identical), 1 otherwise, 2 on a size mismatch. Test with synthetic 16×16 images: identical (PSNR infinite, exit 0), one pixel changed by 1 under tolerance 2 (no mismatches), a 4×4 block changed (bounding box reported, exit 1 at `--psnr-min 60`), and different sizes (exit 2).

- [ ] **Step 3: `frame.py`**

`python3 tools/oracle/frame.py --iso … --frame N -o build/oracle/ppsspp-frame-N.png`: run PPSSPP, wait for frame N (the counter Task 1 found: a `gpu.stats`/vblank count, or `N` × 1/60 s of emulated time; record which in `docs/oracle.md`), pause, request the screenshot of the displayed framebuffer at native resolution (480×272) and save it with `png.py` if PPSSPP returns raw pixels, or write the PNG bytes directly if it returns a PNG.

- [ ] **Step 4: Run the tests, grab a real frame, commit**

Run: `python3 -m unittest discover -s tests/tools -v` → `OK`; `frame.py --frame 600` and look at the image (the boot logos). Record the frame number and what it shows.

```bash
tools/check_no_assets.sh
git add tools/oracle/png.py tools/oracle/frame.py tools/oracle/img_diff.py tests/tools/test_png.py tests/tools/test_img_diff.py
git commit -m "oracle: PNG I/O, PPSSPP frame capture, img_diff.py"
```

---

### Task 8: `oracle.py first-divergence` (the P2 gate)

**Files:** Create `tools/oracle/oracle.py`; Modify `docs/oracle.md` (a "Running it" section).

- [ ] **Step 1: The command**

```bash
PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF -n 200
```

runs `khbbs` (with the alarm wrapper and `--budget 50000000`, trace to `build/oracle/khbbs.jsonl`), captures `n` calls from PPSSPP (`build/oracle/ppsspp.jsonl`), runs `trace_diff.py --map-uids -n N` on them, and prints its report. With `--snapshot-at NAME:K` it also takes both snapshots and runs `mem_diff.py`. Exit status: `trace_diff`'s.

Write a test with both sides stubbed (`subprocess` calls replaced through a small injectable `runner` argument) that checks the commands it builds and that it propagates the diff's exit status.

- [ ] **Step 2: Run it for real**

Expected: the first N firmware calls are compared and the first divergence (or "N of N records match") is reported. That is P2's done condition. Paste the report into `docs/bringup.md` as a new entry "P2, first oracle comparison".

- [ ] **Step 3: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/oracle.py tests/tools/test_oracle.py docs/oracle.md docs/bringup.md
git commit -m "oracle: first-divergence runs both sides and reports where they disagree"
```

---

### Task 9: Roadmap and PR

- [ ] **Step 1:** Link P2's row in `docs/plan.md` to this plan. Rewrite `docs/next-session.md` for the session after.
- [ ] **Step 2:** Push `p2-oracle-tooling` and open a PR into `main` whose body lists the tools, the gate's result, how to run it, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
