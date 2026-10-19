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


def _xor_mask(data, mask):
    """XOR `data` with the 4-byte `mask`, four bytes at a time through int arithmetic."""
    n = len(data)
    pad = -n % 4
    m32 = int.from_bytes(mask * ((n + pad) // 4), "little")
    return (int.from_bytes(data + b"\0" * pad, "little") ^ m32).to_bytes(n + pad, "little")[:n]


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.pos = 0  # start of the unread bytes in buf

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
        # PPSSPP answers "HTTP/1.1 101 OK", so only the code is checked.
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
        # Drop the consumed prefix once per read, not once per frame: a 32 MB
        # message arrives as many small frames and per-frame deletes go quadratic.
        if self.pos:
            del self.buf[:self.pos]
            self.pos = 0
        self.buf += chunk

    def _read_until(self, marker):
        while (i := self.buf.find(marker, self.pos)) < 0:
            self._fill()
        head = bytes(self.buf[self.pos:i])
        self.pos = i + len(marker)
        return head

    def _read_exact(self, n):
        while len(self.buf) - self.pos < n:
            self._fill()
        out = bytes(self.buf[self.pos:self.pos + n])
        self.pos += n
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
                data = _xor_mask(data, mask)
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
