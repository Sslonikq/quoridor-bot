"""Native window (pywebview) around the local analyzer server, or a plain browser.

pywebview is an optional dependency (`pip install -e .[app]`): it is imported lazily and
only inside try/except. Without it, or when the window fails to start, the analyzer opens in
the default browser (a warning goes to the console, and to the page via
?nowin=missing|error|browser).
The GUI module is injected (`webview_module`), so the logic is testable with mocks.
Closing the window returns from run_native; the caller then closes the server it started.
"""

from __future__ import annotations

import functools
import importlib
import sys
import threading
from types import ModuleType
from typing import Any

from quoridor.analyzer.settings import MIN_SIZE, SettingsStore

TITLE = "Анализатор Коридора"
SAVE_DELAY = 0.5  # seconds: move/resize events come in bursts


def load_webview() -> ModuleType | None:
    """The pywebview module, or None if it is not installed / cannot be imported."""
    try:
        return importlib.import_module("webview")
    except Exception:  # ImportError, or a broken GUI backend
        return None


def choose_mode(want_window: bool, webview_module: ModuleType | None) -> str:
    """'window' or 'browser'."""
    return "window" if want_window and webview_module is not None else "browser"


def clamp_to_screens(settings: dict, screens: list[tuple[int, int, int, int]]) -> dict:
    """Drop x/y if the saved top-left corner is outside every screen (monitor unplugged).
    screens: (x, y, width, height). With no screen info the saved value is trusted."""
    x, y = settings.get("x"), settings.get("y")
    if x is None or y is None or not screens:
        return settings
    for sx, sy, sw, sh in screens:
        if sx - 50 <= x < sx + sw - 50 and sy - 10 <= y < sy + sh - 50:
            return settings
    return {**settings, "x": None, "y": None}


def set_window_on_top(window: Any, value: bool) -> None:
    """Set always-on-top from any thread.

    pywebview 6.x (WinForms) sets Form.TopMost directly in the calling thread. From a js_api
    thread that recreates the window handle across threads and deadlocks the UI ("Not
    responding"). So on WinForms the assignment is marshalled to the UI thread with
    Control.Invoke; elsewhere (or if that is not possible) the plain property is used.
    """
    try:
        from System import Func, Type  # type: ignore[import-not-found]
        from webview.platforms.winforms import BrowserView  # type: ignore[import-not-found]

        form = BrowserView.instances.get(window.uid)
    except Exception:  # not WinForms (other OS / backend) or an API change
        form = None
    if form is None:
        window.on_top = value
        return

    def apply() -> None:
        form.TopMost = value

    form.Invoke(Func[Type](apply))


class WindowApi:
    """Exposed to the page as window.pywebview.api (js_api)."""

    def __init__(self, store: SettingsStore) -> None:
        self._store = store
        self._window: Any = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._abnormal: set[str] = set()  # "minimized" / "maximized": geometry is not saved

    def attach(self, window: Any) -> None:
        self._window = window

    # ---- called from JS ----

    def get_settings(self) -> dict:
        return {**self._store.get(), "native": True}

    def set_on_top(self, value: bool) -> bool:
        value = bool(value)
        self._store.update(on_top=value)
        if self._window is not None:
            set_window_on_top(self._window, value)
        self.save_soon()
        return value

    def set_compact(self, value: bool) -> dict:
        """Switch the mode and resize the window to the size remembered for that mode."""
        value = bool(value)
        self._store.update(compact=value)
        s = self._store.get()
        w, h = s["compact_size"] if value else s["full_size"]
        ok = True
        if self._window is not None:
            try:
                self._window.resize(w, h)
            except Exception as e:  # window closing or backend refused; the mode is stored
                ok = False
                print(f"Предупреждение: не удалось изменить размер окна ({e})", file=sys.stderr)
        self.save_soon()
        return {**s, "ok": ok}

    # ---- called from window events ----

    def on_state(self, name: str) -> None:
        """Window state events: minimized / maximized / restored."""
        if name == "restored":
            self._abnormal.clear()
        else:
            self._abnormal.add(name)

    def on_moved(self, x: int, y: int) -> None:
        if self._abnormal or x <= -30000 or y <= -30000:
            return  # minimized (Windows reports -32000) or an implausible position
        self._store.update(x=x, y=y)
        self.save_soon()

    def on_resized(self, width: int, height: int) -> None:
        if self._abnormal or width < MIN_SIZE[0] or height < MIN_SIZE[1]:
            return  # minimized/maximized or an implausible size: keep the saved one
        key = "compact_size" if self._store.get()["compact"] else "full_size"
        self._store.update(**{key: [width, height]})
        self.save_soon()

    def save_soon(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(SAVE_DELAY, self._store.save)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._store.save()


def run_native(
    url: str,
    store: SettingsStore,
    webview_module: ModuleType,
    screens: list[tuple[int, int, int, int]] | None = None,
) -> WindowApi:
    """Create the window and block in the GUI loop until it is closed; settings are saved.
    Any exception from the GUI propagates (the caller falls back to the browser)."""
    settings = store.get()
    if screens is None:
        try:
            screens = [(s.x, s.y, s.width, s.height) for s in webview_module.screens]
        except Exception:
            screens = []
    settings = clamp_to_screens(settings, screens)
    api = WindowApi(store)
    w, h = settings["compact_size"] if settings["compact"] else settings["full_size"]
    window = webview_module.create_window(
        TITLE,
        url,
        js_api=api,
        width=w,
        height=h,
        x=settings["x"],
        y=settings["y"],
        min_size=MIN_SIZE,
        on_top=settings["on_top"],
    )
    api.attach(window)
    window.events.moved += api.on_moved
    window.events.resized += api.on_resized
    window.events.closed += api.flush
    for name in ("minimized", "maximized", "restored"):
        event = getattr(window.events, name, None)
        if event is not None:
            event += functools.partial(api.on_state, name)
    try:
        webview_module.start()
    finally:
        api.flush()
    return api
