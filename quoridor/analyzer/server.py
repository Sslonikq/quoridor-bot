"""Local JSON API + static files. Binds 127.0.0.1 only, a random free port by default.

  POST /api/state    {"moves": [...]}              -> position, legal moves, winner
  POST /api/analyze  {"moves": [...], "time": 2.0} -> top-3 moves of v3, evaluation

Errors: HTTP 400 {"ok": false, "error": "...", "bad_move": N} (N = 1-based number of the
illegal move, if the error is about one). A newer /api/analyze cancels the older one: the
older request answers {"ok": true, "cancelled": true}.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from quoridor.analyzer import engine

HOST = "127.0.0.1"
STATIC = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024
_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}


class AnalyzerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__((HOST, port), Handler)
        self._job_lock = threading.Lock()
        self._job: threading.Event | None = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}/"

    def start_job(self) -> threading.Event:
        """Register a new analysis; the previous one (if any) is cancelled."""
        ev = threading.Event()
        with self._job_lock:
            if self._job is not None:
                self._job.set()
            self._job = ev
        return ev


class Handler(BaseHTTPRequestHandler):
    server: AnalyzerServer
    protocol_version = "HTTP/1.1"
    timeout = 10  # a client that stalls mid-request does not hold a thread forever

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the client vanished between requests

    # ---- helpers ----

    def _host_ok(self) -> bool:
        # Defence against DNS rebinding: only our own address as Host / Origin.
        port = self.server.server_address[1]
        allowed = {f"{HOST}:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in allowed}

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _drain(self) -> None:
        """Discard a small unread body so that closing does not reset the connection (RST)
        before the client has read our error response."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= 16 * MAX_BODY:
            self.rfile.read(length)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _error(self, code: int, msg: str, bad_move: int | None = None) -> None:
        self._json(code, {"ok": False, "error": msg, "bad_move": bad_move})

    # ---- routes ----

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the tab was closed while the page was loading

    def _get(self) -> None:
        if not self._host_ok():
            return self._error(403, "bad host")
        entry = _FILES.get(self.path.split("?", 1)[0])
        if entry is None:
            return self._error(404, "not found")
        name, ctype = entry
        self._send(200, (STATIC / name).read_bytes(), ctype)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the client gave up (a newer request replaced this one)

    def _post(self) -> None:
        # The body is not read on these early exits: close the connection so the rest of
        # it is not parsed as the next request.
        if not self._host_ok():
            self.close_connection = True
            self._drain()
            return self._error(403, "bad host")
        route = self.path.split("?", 1)[0]
        if route not in ("/api/state", "/api/analyze"):
            self.close_connection = True
            self._drain()
            return self._error(404, "not found")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            # Discard a moderately large body so that closing does not reset the connection
            # before the client has read the 413.
            self._drain()
            return self._error(413, "bad body size")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, RecursionError):  # deeply nested JSON raises RecursionError
            return self._error(400, "body must be a JSON object")
        moves = body.get("moves", [])
        try:
            if route == "/api/state":
                pos = engine.describe(engine.replay(moves), moves)
                return self._json(200, {"ok": True, "position": pos})
            seconds = body.get("time", engine.DEFAULT_TIME)
            if isinstance(seconds, bool) or not isinstance(seconds, int | float):
                return self._error(400, "time must be a number")
            engine.replay(moves)  # reject bad input before cancelling the running job
            cancel = self.server.start_job()
            res = engine.analyze(moves, float(seconds), cancel.is_set)
            if res["cancelled"]:
                return self._json(200, {"ok": True, "cancelled": True})
            return self._json(200, {"ok": True, **res})
        except engine.MoveListError as e:
            return self._error(400, str(e), e.number)
