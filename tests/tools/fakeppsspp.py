"""A stand-in for PPSSPP's remote debugger, for tests. Speaks JSON over test_ws.Server."""
import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_ws import Server, frame, read_client_frame  # noqa: E402


class FakePpsspp(Server):
    """Replies to each request through `handlers`: {event: fn(msg) -> reply
    dict or list of dicts}. A list sends several messages (an untagged event,
    then the reply, or nothing at all with []). Unknown events get PPSSPP's
    error reply. Every request received is kept in `received`."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.received = []
        super().__init__(self._serve, status=b"HTTP/1.1 101 OK")

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
                                          "message": "Bad message: unknown event", "level": 2}
            for r in replies if isinstance(replies, list) else [replies]:
                conn.sendall(frame(0x1, json.dumps(r).encode()))
