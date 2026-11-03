"""Window settings of the analyzer: position, size, compact mode, always-on-top.

Stored in ~/.quoridor/analyzer.json (override: the `path` argument or the environment variable
QUORIDOR_ANALYZER_CONFIG). A missing or broken file means the defaults; every field is
validated one by one, so one bad value does not spoil the others. Writes are atomic
(temporary file + os.replace). Pure standard library, no GUI imports.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

ENV_PATH = "QUORIDOR_ANALYZER_CONFIG"
COMPACT_WIDTH = 380
DEFAULTS: dict = {
    "x": None,
    "y": None,
    "full_size": [1180, 900],
    "compact_size": [COMPACT_WIDTH, 760],
    "compact": False,
    "on_top": False,
}
MIN_SIZE = (320, 400)
MAX_SIZE = (10000, 10000)
MAX_COORD = 100000


def default_path() -> Path:
    env = os.environ.get(ENV_PATH)
    return Path(env) if env else Path.home() / ".quoridor" / "analyzer.json"


def _int(v: object) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or v != v:
        return None
    if abs(v) > MAX_COORD:
        return None
    return int(v)


def _size(v: object, default: list[int]) -> list[int]:
    if isinstance(v, list | tuple) and len(v) == 2:
        w, h = _int(v[0]), _int(v[1])
        if w is not None and h is not None:
            return [max(MIN_SIZE[0], min(MAX_SIZE[0], w)), max(MIN_SIZE[1], min(MAX_SIZE[1], h))]
    return list(default)


def sanitize(raw: object) -> dict:
    """Any JSON value -> a complete valid settings dict."""
    d = raw if isinstance(raw, dict) else {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    x, y = _int(d.get("x")), _int(d.get("y"))
    if x is not None and y is not None:
        out["x"], out["y"] = x, y
    out["full_size"] = _size(d.get("full_size"), DEFAULTS["full_size"])
    out["compact_size"] = _size(d.get("compact_size"), DEFAULTS["compact_size"])
    for key in ("compact", "on_top"):
        if isinstance(d.get(key), bool):
            out[key] = d[key]
    return out


class SettingsStore:
    """In-memory settings + load/save. Thread-safe (window events come from GUI threads)."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()
        self._lock = threading.Lock()
        self._data = sanitize(None)

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        with self._lock:
            self._data = sanitize(raw)
            return json.loads(json.dumps(self._data))

    def get(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def update(self, **changes: object) -> dict:
        """Merge changes (invalid values fall back to the previous ones); no disk write."""
        with self._lock:
            merged = json.loads(json.dumps(self._data))
            for k, v in changes.items():
                if k in DEFAULTS:
                    merged[k] = v
            fresh = sanitize(merged)
            # a rejected field keeps its old value instead of resetting to the default
            for k in ("compact", "on_top"):
                if not isinstance(merged[k], bool):
                    fresh[k] = self._data[k]
            # an invalid x/y pair keeps the previous position (None, None is an explicit reset)
            xy = (merged["x"], merged["y"])
            if xy != (None, None) and (_int(xy[0]) is None or _int(xy[1]) is None):
                fresh["x"], fresh["y"] = self._data["x"], self._data["y"]
            self._data = fresh
            return json.loads(json.dumps(self._data))

    def save(self) -> bool:
        """Atomic write; False if the disk is not writable (never raises)."""
        data = self.get()
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".analyzer-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except OSError:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False
