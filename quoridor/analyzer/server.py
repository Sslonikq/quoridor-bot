"""Local JSON API + static files. Binds 127.0.0.1 only, a random free port by default.

  POST /api/state    {"moves": [...]}              -> position, legal moves, winner
  POST /api/analyze  {"moves": [...], "time": 3.0, "bot": "mcts"} -> top-3 moves, evaluation,
                     depth ("time" is optional: engine.DEFAULT_TIME; "bot" is "v4" | "v5" |
                     "mcts", default engine.DEFAULT_BOT; anything else is a 400)
  POST /api/evaluate {"moves": [...], "move": "e3h", "time": 3.0}
                     -> score of that one move in the position after "moves" (engine.evaluate_move)
  POST /api/progress {}                            -> {"busy", "depth", "sims"}: live depth
                     (completed iterations; MCTS: length of the main line) of the running
                     analysis, 0 before the first one; sims = MCTS simulations so far (else 0)
  POST /api/cancel   {}                            -> cancels the running analysis / evaluation
  POST /api/parse    {"text": "d1 e8 ..."}         -> {"moves": [...]}: a pasted game (see
                     engine.parse_game_text), replayed and validated
  POST /api/review   {"moves": [...], "time": 1.0} -> {"id", "total"}: starts a background
                     review of the whole game (a newer one cancels the older one)
  POST /api/review/status {"id", "since": 0}       -> {"done", "total", "finished",
                     "cancelled", "error", "results": [steps since index `since`], "summary"}
  POST /api/review/cancel {}                       -> cancels the running review

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
REVIEW_MAX_TIME = 10.0  # per position; the review has its own, smaller limit than a hint
_TIME_ERROR = f"time must be a number from 0 to {engine.MAX_TIME:g}"


def _valid_time(seconds: object) -> bool:
    """A finite non-bool number in (0, engine.MAX_TIME]; small values are raised to MIN_TIME."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return False
    return 0 < seconds <= engine.MAX_TIME  # False for NaN; inf > MAX_TIME


ROUTES = (
    "/api/state",
    "/api/analyze",
    "/api/evaluate",
    "/api/cancel",
    "/api/progress",
    "/api/parse",
    "/api/review",
    "/api/review/status",
    "/api/review/cancel",
)
_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}


class ReviewJob:
    """A game review running in its own thread; the client polls it."""

    def __init__(self, job_id: int, moves: list[str], seconds: float) -> None:
        self.id = job_id
        self.moves = moves
        self.total = len(moves) + 1  # positions 0..len(moves)
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.steps: list[dict] = []
        self.finished = False
        self.error: str | None = None
        self.thread = threading.Thread(target=self._run, args=(seconds,), daemon=True)

    def _run(self, seconds: float) -> None:
        try:
            for step in engine.review_game(self.moves, seconds, self.cancel.is_set):
                with self.lock:
                    self.steps.append(step)
        except Exception as e:  # noqa: BLE001 - reported to the client, never kills the server
            with self.lock:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                self.finished = True

    def status(self, since: int) -> dict:
        with self.lock:
            steps = list(self.steps)
            finished = self.finished
            error = self.error
        cancelled = self.cancel.is_set() and len(steps) < self.total
        return {
            "ok": True,
            "id": self.id,
            "total": self.total,
            "done": len(steps),
            "finished": finished,
            "cancelled": cancelled,
            "error": error,
            "results": steps[since:],
            "summary": engine.summarize(steps),
        }


class AnalyzerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__((HOST, port), Handler)
        self._job_lock = threading.Lock()
        self._job: threading.Event | None = None
        self._depth = 0  # live depth of the current analysis (see on_depth)
        self._sims = 0  # MCTS simulations of the current analysis
        self._review: ReviewJob | None = None
        self._review_seq = 0

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
            self._depth = 0
            self._sims = 0
        return ev

    def on_depth(self, ev: threading.Event, depth: int, sims: int = 0) -> None:
        """Progress of the analysis `ev`; ignored once a newer one has replaced it."""
        with self._job_lock:
            if self._job is ev:
                self._depth = depth
                self._sims = sims

    def progress(self) -> dict:
        with self._job_lock:
            return {"busy": self._job is not None, "depth": self._depth, "sims": self._sims}

    def start_review(self, moves: list[str], seconds: float) -> ReviewJob:
        """Start a review; the previous review and the running analysis are cancelled."""
        self.cancel_job()
        with self._job_lock:
            if self._review is not None:
                self._review.cancel.set()
            self._review_seq += 1
            job = self._review = ReviewJob(self._review_seq, moves, seconds)
        job.thread.start()
        return job

    def get_review(self, job_id: object) -> ReviewJob | None:
        with self._job_lock:
            r = self._review
        if isinstance(job_id, bool) or not isinstance(job_id, int):
            return None
        return r if r is not None and r.id == job_id else None

    def cancel_review(self) -> None:
        with self._job_lock:
            if self._review is not None:
                self._review.cancel.set()

    def server_close(self) -> None:
        self.cancel_review()
        super().server_close()

    def cancel_job(self) -> None:
        """Cancel the running analysis (if any); its request answers "cancelled"."""
        with self._job_lock:
            if self._job is not None:
                self._job.set()
                self._job = None


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
        if route not in ROUTES:
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
        if route == "/api/cancel":
            self.server.cancel_job()
            return self._json(200, {"ok": True})
        if route == "/api/progress":
            return self._json(200, {"ok": True, **self.server.progress()})
        if route == "/api/review/cancel":
            self.server.cancel_review()
            return self._json(200, {"ok": True})
        moves = body.get("moves", [])
        try:
            if route == "/api/parse":
                text_moves = engine.parse_game_text(body.get("text"))
                return self._json(200, {"ok": True, "moves": text_moves})
            if route == "/api/review/status":
                since = body.get("since", 0)
                if isinstance(since, bool) or not isinstance(since, int) or since < 0:
                    return self._error(400, "since must be a non-negative integer")
                job = self.server.get_review(body.get("id"))
                if job is None:
                    return self._error(404, "unknown review")
                return self._json(200, job.status(since))
            if route == "/api/review":
                seconds = body.get("time", engine.REVIEW_TIME)
                if not _valid_time(seconds):
                    return self._error(400, _TIME_ERROR)
                engine.replay(moves)
                if not moves:
                    return self._error(400, "nothing to review")
                job = self.server.start_review(moves, min(float(seconds), REVIEW_MAX_TIME))
                return self._json(200, {"ok": True, "id": job.id, "total": job.total})
            if route == "/api/state":
                pos = engine.describe(engine.replay(moves), moves)
                return self._json(200, {"ok": True, "position": pos})
            seconds = body.get("time", engine.DEFAULT_TIME)
            if not _valid_time(seconds):
                return self._error(400, _TIME_ERROR)
            engine.replay(moves)  # reject bad input before cancelling the running job
            if route == "/api/evaluate":
                move = body.get("move")
                if not isinstance(move, str):
                    raise engine.MoveListError("move must be a string", len(moves) + 1)
                engine.replay([*moves, move])  # the move must be legal after `moves`
                cancel = self.server.start_job()
                res = engine.evaluate_move(moves, move, float(seconds), cancel.is_set)
                return self._json(200, {"ok": True, **res})
            bot = body.get("bot", engine.DEFAULT_BOT)
            if not isinstance(bot, str) or bot not in engine.BOTS:
                return self._error(400, f"bot must be one of: {', '.join(engine.BOTS)}")
            cancel = self.server.start_job()
            res = engine.analyze(
                moves,
                float(seconds),
                cancel.is_set,
                lambda d, n=0: self.server.on_depth(cancel, d, n),
                bot,
            )
            if res["cancelled"]:
                return self._json(200, {"ok": True, "cancelled": True})
            return self._json(200, {"ok": True, **res})
        except engine.MoveListError as e:
            return self._error(400, str(e), e.number)
