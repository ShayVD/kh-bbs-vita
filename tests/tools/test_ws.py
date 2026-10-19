"""ws.py against an in-process RFC 6455 server. No network beyond localhost."""
import base64, hashlib, pathlib, socket, struct, sys, threading, time, unittest

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

    def __init__(self, script, accept_key_ok=True, protocol="debugger.ppsspp.org",
                 status=b"HTTP/1.1 101 Switching Protocols"):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script, self.accept_key_ok, self.protocol = script, accept_key_ok, protocol
        self.status = status
        self.request = b""

    def run(self):
        conn, _ = self.sock.accept()
        while b"\r\n\r\n" not in self.request:
            self.request += conn.recv(4096)
        key = [l.split(b":", 1)[1].strip() for l in self.request.split(b"\r\n")
               if l.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()) if self.accept_key_ok else b"bogus"
        conn.sendall(self.status + b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
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
        ws.close()
        srv.join(5)

    def test_ppsspp_status_line(self):
        """PPSSPP answers `HTTP/1.1 101 OK`, not `Switching Protocols`."""
        srv = Server(lambda conn: conn.sendall(frame(0x1, b"ok")), status=b"HTTP/1.1 101 OK")
        srv.start()
        ws = WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        self.assertEqual(ws.recv_text(), "ok")
        ws.close()
        srv.join(5)

    def test_many_fragments_with_pings(self):
        """A multi-MB message in thousands of small frames, pings in between."""
        size, step = 8 << 20, 1000
        payload = bytes(range(32, 127)) * (size // 95 + 1)
        payload = payload[:size]
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

        srv = Server(script)
        srv.start()
        ws = WebSocket.connect("127.0.0.1", srv.port, "/debugger")
        start = time.monotonic()
        text = ws.recv_text()
        elapsed = time.monotonic() - start
        self.assertEqual(len(text), size)
        self.assertEqual(text.encode(), payload)
        self.assertLess(elapsed, 20.0)                           # quadratic buffering takes far longer
        ws.close()
        srv.join(5)
        self.assertEqual(pongs[0], (0xA, b"p"))
        self.assertEqual(len(pongs), size // step // 1000)


if __name__ == "__main__":
    unittest.main()
