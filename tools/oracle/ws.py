"""A minimal RFC 6455 WebSocket client, standard library only.

Enough for PPSSPP's remote debugger: one connection, text messages, client
masking, fragmented and large (64-bit length) server frames, ping/pong and
close. No TLS, no extensions.
"""
import base64, hashlib, os, select, socket, struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(ConnectionError):
    """A protocol error or a closed connection. An OSError, so callers that
    retry a connect on OSError also retry a connection dropped mid-handshake."""


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.parts = []                     # frames of a fragmented message so far
        self.broken = False

    @classmethod
    def connect(cls, host, port, path="/", protocol=None, timeout=10.0):
        """Open a connection. `timeout` stays on the socket, so it applies to
        every later send and recv as well."""
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16))
            req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key.decode()}\r\n"
                   f"Sec-WebSocket-Version: 13\r\n")
            if protocol:
                req += f"Sec-WebSocket-Protocol: {protocol}\r\n"
            sock.sendall((req + "\r\n").encode())
            ws = cls(sock)
            head = ws._read_until(b"\r\n\r\n").decode("latin-1").split("\r\n")
            # PPSSPP answers "HTTP/1.1 101 OK", so only the code is checked.
            if not head[0].startswith("HTTP/1.1 101"):
                raise WebSocketError(f"handshake refused: {head[0]}")
            hdrs = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in head[1:] if ":" in line)}
            want = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
            if hdrs.get("sec-websocket-accept") != want:
                raise WebSocketError("bad Sec-WebSocket-Accept")
            return ws
        except BaseException:
            sock.close()
            raise

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
        self.sock.sendall(head + mask + _xor_mask(payload, mask))

    def send_text(self, text):
        if self.broken:
            raise WebSocketError("connection unusable after error")
        self._send(OP_TEXT, text.encode())

    def recv_text(self):
        """The next complete text message. Answers pings; raises on close.

        Any exception, a timeout in the middle of a frame included, leaves the
        stream out of sync. The connection is then unusable: later calls raise
        WebSocketError and the caller must close it.
        """
        if self.broken:
            raise WebSocketError("connection unusable after error")
        try:
            while (text := self._recv_frame()) is None:
                pass
            return text
        except BaseException:
            self.broken = True
            raise

    def poll_text(self, timeout):
        """Wait up to `timeout` seconds for data, then read at most one frame.

        Returns the text message that frame completes, or None: nothing
        arrived, or the frame was a ping (answered), a pong or part of a
        fragmented message. Nothing arriving leaves the connection usable;
        an error inside a frame breaks it, as in recv_text.
        """
        if self.broken:
            raise WebSocketError("connection unusable after error")
        if not self.buf and not select.select([self.sock], [], [], max(timeout, 0.0))[0]:
            return None
        try:
            return self._recv_frame()
        except BaseException:
            self.broken = True
            raise

    def _recv_frame(self):
        """Read one frame; the text message if it completes one, else None."""
        b0, b1 = self._read_exact(2)
        fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
        if n == 126:
            n, = struct.unpack(">H", self._read_exact(2))
        elif n == 127:
            n, = struct.unpack(">Q", self._read_exact(8))
        if opcode & 0x8 and (not fin or n > 125):
            raise WebSocketError("bad control frame")
        mask = self._read_exact(4) if b1 & 0x80 else None
        data = self._read_exact(n)
        if mask:
            data = _xor_mask(data, mask)
        if opcode == OP_PING:
            self._send(OP_PONG, data)
            return None
        if opcode == OP_PONG:
            return None
        if opcode == OP_CLOSE:
            raise WebSocketError("closed by peer")
        if opcode == OP_CONT and not self.parts:
            raise WebSocketError("continuation without a message")
        if opcode == OP_TEXT and self.parts:
            raise WebSocketError("new message inside a fragmented one")
        if opcode not in (OP_CONT, OP_TEXT):
            raise WebSocketError(f"unsupported opcode {opcode:#x}")
        self.parts.append(data)
        if not fin:
            return None
        raw, self.parts = b"".join(self.parts), []
        try:
            return raw.decode()
        except UnicodeDecodeError as e:
            raise WebSocketError(f"message is not UTF-8: {e}") from e

    def close(self):
        try:
            self._send(OP_CLOSE, b"")
        except OSError:
            pass
        finally:
            self.sock.close()


def _xor_mask(data, mask):
    """XOR `data` with the 4-byte `mask`, four bytes at a time through int arithmetic."""
    n = len(data)
    pad = -n % 4
    m32 = int.from_bytes(mask * ((n + pad) // 4), "little")
    return (int.from_bytes(data + b"\0" * pad, "little") ^ m32).to_bytes(n + pad, "little")[:n]
