"""A local server that hands the interface its live view of Jarvis.

Built on the standard library alone: a threading HTTP server, server-sent
events for the stream from Jarvis to the display, and ordinary POSTs for the
other direction. No framework, no websocket dependency, nothing to install.

It listens on the loopback interface only, and every request must carry a
token minted at start-up -- a browser tab from some other site must not be
able to read the transcript or put words in Jarvis' mouth.
"""

from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from jarvis.events import Event, EventBus, EventKind

WEB_ROOT = Path(__file__).parent / "web"

# Dropped rather than queued without limit: a display that cannot keep up
# should skip frames, not grow a backlog.
QUEUE_LIMIT = 256

# Sent when nothing else is happening, so proxies and browsers keep the
# connection open.
HEARTBEAT_SECONDS = 20.0


class Broadcaster:
    """Fans events out to every connected display."""

    def __init__(self) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        # Replayed to a display that connects mid-conversation, so a reload
        # does not show an empty screen.
        self._recent: list[dict[str, Any]] = []

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        with self._lock:
            for message in self._recent[-60:]:
                try:
                    channel.put_nowait(message)
                except queue.Full:
                    break
            self._clients.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self._clients:
                self._clients.remove(channel)

    def publish(self, message: dict[str, Any]) -> None:
        with self._lock:
            # Levels are a live signal; replaying old ones would animate the
            # display with sound that is long gone.
            if message.get("kind") != EventKind.LEVEL.value:
                self._recent.append(message)
                del self._recent[:-200]
            clients = list(self._clients)

        for channel in clients:
            try:
                channel.put_nowait(message)
            except queue.Full:
                pass  # this display is behind; let it skip

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._clients)


class UiServer:
    """Serves the interface and bridges it to the event bus."""

    def __init__(
        self,
        bus: EventBus,
        on_message: Callable[[str], None] | None = None,
        on_answer: Callable[[str, bool], None] | None = None,
        snapshot: Callable[[], dict[str, Any]] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        self.bus = bus
        # Called with text the user typed into the interface.
        self.on_message = on_message
        # Called with (question, answer) when a confirmation is answered.
        self.on_answer = on_answer
        # Returns the state a freshly opened display needs.
        self.snapshot = snapshot or (lambda: {})
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.broadcaster = Broadcaster()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        bus.subscribe(self._on_event)

    # -- bridging ------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        self.broadcaster.publish(
            {
                "kind": event.kind.value,
                "text": event.text,
                "data": _plain(event.data),
            }
        )

    # -- lifecycle -----------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def start(self) -> str:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="jarvis-ui", daemon=True
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _plain(data: dict[str, Any]) -> dict[str, Any]:
    """Make event data JSON-safe without losing the useful parts."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        elif isinstance(value, dict):
            out[key] = {str(k): str(v) for k, v in value.items()}
        else:
            out[key] = str(value)
    return out


def _make_handler(server: "UiServer"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Jarvis"

        def log_message(self, *args) -> None:
            pass  # the console belongs to Jarvis, not to request logs

        # -- helpers ---------------------------------------------------------

        def _authorised(self) -> bool:
            """Token in the query or the header; nothing else gets in."""
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("token") or [""])[0] or self.headers.get("X-Jarvis-Token", "")
            return secrets.compare_digest(supplied, server.token)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        # -- routes ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import urlparse

            path = urlparse(self.path).path

            if path == "/events":
                if not self._authorised():
                    self._json({"error": "forbidden"}, 403)
                    return
                self._stream()
                return

            if path == "/api/state":
                if not self._authorised():
                    self._json({"error": "forbidden"}, 403)
                    return
                self._json(server.snapshot())
                return

            # Static files. The page itself needs no token -- it cannot do
            # anything without one, and the token arrives in its query string.
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB_ROOT / name).resolve()
            if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
                self._json({"error": "not found"}, 404)
                return
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), kind)

        def do_POST(self) -> None:  # noqa: N802
            from urllib.parse import urlparse

            if not self._authorised():
                self._json({"error": "forbidden"}, 403)
                return

            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return

            if path == "/api/say":
                text = str(payload.get("text", "")).strip()
                if not text:
                    self._json({"error": "empty"}, 400)
                    return
                if server.on_message is not None:
                    threading.Thread(
                        target=server.on_message, args=(text,), daemon=True
                    ).start()
                self._json({"ok": True})
                return

            if path == "/api/answer":
                if server.on_answer is not None:
                    server.on_answer(
                        str(payload.get("question", "")), bool(payload.get("yes"))
                    )
                self._json({"ok": True})
                return

            self._json({"error": "not found"}, 404)

        # -- server-sent events ----------------------------------------------

        def _stream(self) -> None:
            channel = server.broadcaster.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            try:
                while True:
                    try:
                        message = channel.get(timeout=HEARTBEAT_SECONDS)
                        payload = f"data: {json.dumps(message)}\n\n"
                    except queue.Empty:
                        payload = ": keep-alive\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the display went away
            finally:
                server.broadcaster.unsubscribe(channel)

    return Handler
