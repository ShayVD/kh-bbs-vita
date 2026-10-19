"""ws.py against an in-process RFC 6455 server. No network beyond localhost."""
import base64, hashlib, pathlib, socket, struct, sys, threading, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from oracle.ws import WebSocket, WebSocketError, _xor_mask  # noqa: E402

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
    return b0 & 0x0F, _xor_mask(data, mask)


class Server(threading.Thread):
    """Accepts one client, checks the handshake, then runs `script(conn)`.

    `status=None` closes without replying; `extra` is sent in the same write as
    the handshake reply. An exception in the server is kept in `self.error`:
    call `join_checked(testcase)` so it fails the test.
    """

    def __init__(self, script, accept_key_ok=True, protocol="debugger.ppsspp.org",
                 status=b"HTTP/1.1 101 Switching Protocols", extra=b""):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script, self.accept_key_ok, self.protocol = script, accept_key_ok, protocol
        self.status, self.extra = status, extra
        self.request = b""
        self.error = None

    def run(self):
        conn = None
        try:
            conn, _ = self.sock.accept()
            while b"\r\n\r\n" not in self.request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                self.request += chunk
            key = [line.split(b":", 1)[1].strip() for line in self.request.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest()) if self.accept_key_ok else b"bogus"
            if self.status is not None:
                conn.sendall(self.status + b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Accept: " + accept + b"\r\nSec-WebSocket-Protocol: " +
                             self.protocol.encode() + b"\r\n\r\n" + self.extra)
            self.script(conn)
        except BaseException as e:  # noqa: BLE001 - reported through join_checked
            self.error = e
        finally:
            if conn:
                conn.close()
            self.sock.close()

    def join_checked(self, testcase, timeout=5):
        self.join(timeout)
        testcase.assertFalse(self.is_alive(), "server thread still running")
        testcase.assertIsNone(self.error, f"server failed: {self.error!r}")


class WsTest(unittest.TestCase):
    def serve(self, script, **kw):
        srv = Server(script, **kw)
        self.addCleanup(srv.sock.close)
        srv.start()
        return srv

    def connect(self, srv, path="/debugger", **kw):
        ws = WebSocket.connect("127.0.0.1", srv.port, path, **kw)
        self.addCleanup(ws.close)
        return ws

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

        srv = self.serve(script)
        ws = self.connect(srv, protocol="debugger.ppsspp.org")
        ws.send_text('{"event":"version"}')
        self.assertEqual(ws.recv_text(), 'echo:{"event":"version"}')
        self.assertEqual(ws.recv_text(), "fragment")
        self.assertEqual(len(ws.recv_text()), 70000)
        ws.close()
        srv.join_checked(self)
        self.assertEqual(got[0], (0x1, b'{"event":"version"}'))
        self.assertEqual(got[1], (0xA, b"hi"))
        self.assertIn(b"GET /debugger HTTP/1.1", srv.request)
        self.assertIn(b"Sec-WebSocket-Protocol: debugger.ppsspp.org", srv.request)

    def test_client_send_lengths(self):
        sizes = [0, 125, 126, 65535, 65536]

        def script(conn):
            for _ in sizes:
                op, data = read_client_frame(conn)
                ok = op == 0x1 and data == b"y" * len(data)
                conn.sendall(frame(0x1, f"{len(data)}:{ok}".encode()))
            read_client_frame(conn)                                # close

        srv = self.serve(script)
        ws = self.connect(srv)
        for n in sizes:
            ws.send_text("y" * n)
            self.assertEqual(ws.recv_text(), f"{n}:True")
        ws.close()
        srv.join_checked(self)

    def test_bad_accept_refused(self):
        srv = self.serve(lambda conn: None, accept_key_ok=False)
        with self.assertRaises(WebSocketError):
            WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        srv.join_checked(self)

    def test_refused_status(self):
        srv = self.serve(lambda conn: None, status=b"HTTP/1.1 400 Bad Request")
        with self.assertRaises(WebSocketError):
            WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        srv.join_checked(self)

    def test_server_closes_before_replying(self):
        """A retry loop on OSError must see this: PPSSPP drops early connections while starting."""
        srv = self.serve(lambda conn: None, status=None)
        with self.assertRaises(OSError):
            WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        srv.join_checked(self)

    def test_close_from_server(self):
        srv = self.serve(lambda conn: conn.sendall(frame(0x8, b"")))
        ws = self.connect(srv, "/")
        with self.assertRaises(WebSocketError):
            ws.recv_text()
        srv.join_checked(self)

    def test_ppsspp_status_line(self):
        """PPSSPP answers `HTTP/1.1 101 OK`, not `Switching Protocols`."""
        srv = self.serve(lambda conn: conn.sendall(frame(0x1, b"ok")), status=b"HTTP/1.1 101 OK")
        ws = self.connect(srv)
        self.assertEqual(ws.recv_text(), "ok")
        srv.join_checked(self)

    def test_first_frame_in_the_handshake_write(self):
        srv = self.serve(lambda conn: None, extra=frame(0x1, b"early"))
        ws = self.connect(srv)
        self.assertEqual(ws.recv_text(), "early")
        srv.join_checked(self)

    def test_utf8_split_across_fragments(self):
        text = "café ☃!"
        raw = text.encode()
        cut = raw.index(b"\xc3") + 1                              # between the two bytes of the e-acute
        srv = self.serve(lambda conn: conn.sendall(frame(0x1, raw[:cut], fin=False) + frame(0x0, raw[cut:])))
        ws = self.connect(srv)
        self.assertEqual(ws.recv_text(), text)
        srv.join_checked(self)

    def test_masked_server_frame_is_tolerated(self):
        mask = b"\x01\x02\x03\x04"
        srv = self.serve(lambda conn: conn.sendall(bytes([0x81, 0x80 | 5]) + mask + _xor_mask(b"hello", mask)))
        ws = self.connect(srv)
        self.assertEqual(ws.recv_text(), "hello")
        srv.join_checked(self)

    def test_many_fragments_with_pings(self):
        """A multi-MB message in thousands of small frames, pings in between."""
        size, step = 8 << 20, 1000
        payload = (bytes(range(32, 127)) * (size // 95 + 1))[:size]
        pongs = []

        def script(conn):
            out = []
            for i, off in enumerate(range(0, size, step)):
                last = off + step >= size
                out.append(frame(0x1 if off == 0 else 0x0, payload[off:off + step], fin=last))
                if i % 1000 == 999:
                    out.append(frame(0x9, b"p"))                  # control frame mid-message
            conn.sendall(b"".join(out))
            for _ in range(size // step // 1000):
                pongs.append(read_client_frame(conn))

        srv = self.serve(script)
        ws = self.connect(srv)
        text = ws.recv_text()
        self.assertEqual(len(text), size)
        self.assertEqual(text.encode(), payload)
        srv.join_checked(self)
        self.assertEqual(pongs[0], (0xA, b"p"))
        self.assertEqual(len(pongs), size // step // 1000)

    def test_timeout_mid_frame_breaks_the_connection(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def script(conn):
            conn.sendall(frame(0x1, b"hello")[:4])                # header and two payload bytes
            release.wait(5)

        srv = self.serve(script)
        ws = self.connect(srv, timeout=0.3)
        with self.assertRaises((TimeoutError, WebSocketError)):
            ws.recv_text()
        with self.assertRaises(WebSocketError):
            ws.recv_text()
        with self.assertRaises(WebSocketError):
            ws.send_text("late")
        release.set()
        srv.join_checked(self)

    def test_protocol_errors(self):
        cases = {
            "binary": frame(0x2, b"x"),
            "bad utf-8": frame(0x1, b"\xff"),
            "orphan continuation": frame(0x0, b"x"),
            "data frame inside a message": frame(0x1, b"a", fin=False) + frame(0x1, b"b"),
            "long ping": frame(0x9, b"p" * 126),
            "fragmented ping": frame(0x9, b"p", fin=False),
            "unknown opcode": frame(0x3, b"x"),
        }
        for name, wire in cases.items():
            with self.subTest(name):
                srv = self.serve(lambda conn, wire=wire: conn.sendall(wire))
                ws = self.connect(srv)
                with self.assertRaises(WebSocketError):
                    ws.recv_text()
                with self.assertRaises(WebSocketError):
                    ws.recv_text()                                 # and it stays unusable
                srv.join_checked(self)


if __name__ == "__main__":
    unittest.main()
