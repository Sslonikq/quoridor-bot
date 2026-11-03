"""Phase 12, extended tests: settings file, fallback to the browser, window lifecycle,
WindowApi on mocks, loopback-only server, no external URLs in static/."""

from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quoridor.analyzer import __main__ as app_main
from quoridor.analyzer import settings, window
from quoridor.analyzer.server import HOST, STATIC, AnalyzerServer
from quoridor.analyzer.settings import DEFAULTS, SettingsStore

# ---------------------------------------------------------------- settings


@pytest.mark.parametrize("content", ["", "   ", "[]", "42", "null", '"str"', "{", "\x00\x01"])
def test_non_object_or_garbage_json_gives_defaults(tmp_path, content):
    p = tmp_path / "a.json"
    p.write_text(content, encoding="utf-8")
    assert SettingsStore(p).load() == settings.sanitize(None) == DEFAULTS


def test_undecodable_bytes_give_defaults(tmp_path):
    p = tmp_path / "a.json"
    p.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert SettingsStore(p).load() == DEFAULTS


def test_path_is_a_directory_gives_defaults(tmp_path):
    assert SettingsStore(tmp_path).load() == DEFAULTS


def test_partially_valid_json_keeps_good_fields(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "x": 30,
                "y": 40,
                "full_size": "big",
                "compact_size": [500, 600],
                "compact": True,
                "on_top": 1,
                "unknown": [1, 2],
            }
        ),
        encoding="utf-8",
    )
    got = SettingsStore(p).load()
    assert (got["x"], got["y"]) == (30, 40)
    assert got["full_size"] == DEFAULTS["full_size"]
    assert got["compact_size"] == [500, 600]
    assert got["compact"] is True
    assert got["on_top"] is False  # 1 is not a bool
    assert "unknown" not in got


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        "10",
        [1],
        {"a": 1},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**9,
        -(10**9),
    ],
)
def test_bad_coordinate_types_drop_position(bad):
    got = settings.sanitize({"x": bad, "y": 5})
    assert got["x"] is None and got["y"] is None
    got = settings.sanitize({"x": 5, "y": bad})
    assert got["x"] is None and got["y"] is None


def test_negative_and_float_coordinates():
    got = settings.sanitize({"x": -1500.7, "y": -20})  # second monitor on the left
    assert (got["x"], got["y"]) == (-1500, -20)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "380x700",
        [],
        [500],
        [500, 600, 700],
        [True, 600],
        ["a", "b"],
        [float("nan"), 600],
        [float("inf"), 600],
        {"w": 1},
    ],
)
def test_bad_size_falls_back_to_default(bad):
    got = settings.sanitize({"full_size": bad, "compact_size": bad})
    assert got["full_size"] == DEFAULTS["full_size"]
    assert got["compact_size"] == DEFAULTS["compact_size"]


def test_size_is_clamped_and_negative_becomes_minimum():
    got = settings.sanitize({"full_size": [-5, -5], "compact_size": [10**6 - 1, 99999]})
    assert got["full_size"] == list(settings.MIN_SIZE)
    # beyond MAX_COORD the whole pair is rejected; within it, clamped to MAX_SIZE
    assert got["compact_size"] == DEFAULTS["compact_size"]
    got = settings.sanitize({"full_size": [50000, 20000]})
    assert got["full_size"] == list(settings.MAX_SIZE)


def test_sanitize_does_not_alias_defaults():
    a = settings.sanitize(None)
    a["full_size"][0] = 1
    assert DEFAULTS["full_size"][0] == 1180
    assert settings.sanitize(None)["full_size"][0] == 1180


def test_x_without_y_is_dropped():
    got = settings.sanitize({"x": 10})
    assert got["x"] is None and got["y"] is None


def test_update_rejected_bool_keeps_previous_value(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(compact=True, on_top=True)
    st.update(compact="no", on_top=0)
    assert st.get()["compact"] is True and st.get()["on_top"] is True
    st.update(unknown_key=5)
    assert "unknown_key" not in st.get()


def test_get_returns_copy(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.get()["full_size"][0] = 1
    assert st.get()["full_size"][0] == 1180


def test_explicit_path_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.ENV_PATH, str(tmp_path / "env.json"))
    assert SettingsStore(tmp_path / "own.json").path == tmp_path / "own.json"


def test_default_path_without_env_is_home_quoridor(monkeypatch, tmp_path):
    monkeypatch.delenv(settings.ENV_PATH, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert settings.default_path() == tmp_path / ".quoridor" / "analyzer.json"
    monkeypatch.setenv(settings.ENV_PATH, "")  # empty variable counts as unset
    assert settings.default_path() == tmp_path / ".quoridor" / "analyzer.json"


def test_env_file_is_really_used_for_load_and_save(monkeypatch, tmp_path):
    p = tmp_path / "e" / "cfg.json"
    monkeypatch.setenv(settings.ENV_PATH, str(p))
    st = SettingsStore()
    st.update(on_top=True)
    assert st.save()
    assert SettingsStore().load()["on_top"] is True
    assert json.loads(p.read_text(encoding="utf-8"))["on_top"] is True


def test_failed_replace_keeps_old_file_and_leaves_no_temp(monkeypatch, tmp_path):
    p = tmp_path / "a.json"
    st = SettingsStore(p)
    st.update(on_top=True)
    assert st.save()
    before = p.read_text(encoding="utf-8")
    st.update(on_top=False, x=1, y=2)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(settings.os, "replace", boom)
    assert st.save() is False  # never raises
    assert p.read_text(encoding="utf-8") == before  # the old file is intact
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]  # temp file removed


def test_failed_json_dump_does_not_corrupt_file(monkeypatch, tmp_path):
    p = tmp_path / "a.json"
    st = SettingsStore(p)
    assert st.save()
    before = p.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("write failed")

    monkeypatch.setattr(settings.json, "dump", boom)
    assert st.save() is False
    assert p.read_text(encoding="utf-8") == before
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]


def test_unwritable_location_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert SettingsStore(blocker / "sub" / "a.json").save() is False  # parent is a file


def test_saved_file_is_valid_json_with_all_keys(tmp_path):
    p = tmp_path / "a.json"
    st = SettingsStore(p)
    st.update(x=1, y=2)
    st.save()
    assert set(json.loads(p.read_text(encoding="utf-8"))) == set(DEFAULTS)


def test_separate_sizes_for_compact_and_full(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(full_size=[1300, 950], compact_size=[400, 800])
    st.save()
    got = SettingsStore(tmp_path / "a.json").load()
    assert got["full_size"] == [1300, 950] and got["compact_size"] == [400, 800]
    assert got["compact_size"][0] != got["full_size"][0]
    assert DEFAULTS["compact_size"][0] == settings.COMPACT_WIDTH == 380


# ---------------------------------------------------------------- screens


SCREENS = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]


@pytest.mark.parametrize(
    ("x", "y", "kept"),
    [
        (0, 0, True),
        (1000, 500, True),
        (2000, 100, True),  # second monitor
        (-30, 0, True),  # slightly off the edge is still reachable
        (3300, 0, False),  # right of every screen
        (-1920, 0, False),  # monitor unplugged
        (100, 5000, False),
        (100, -500, False),
    ],
)
def test_clamp_to_screens_table(x, y, kept):
    got = window.clamp_to_screens({"x": x, "y": y, "on_top": True}, SCREENS)
    assert (got["x"] is not None) is kept
    assert got["on_top"] is True  # other fields untouched


def test_clamp_does_not_mutate_and_trusts_without_screens_or_position():
    s = {"x": 9999, "y": 9999}
    assert window.clamp_to_screens(s, [(0, 0, 100, 100)]) == {"x": None, "y": None}
    assert s == {"x": 9999, "y": 9999}
    assert window.clamp_to_screens(s, []) is s
    n = {"x": None, "y": None}
    assert window.clamp_to_screens(n, SCREENS) is n


def _fake_webview(screens=None):
    ev = SimpleNamespace(moved=MagicMock(), resized=MagicMock(), closed=MagicMock())
    for e in (ev.moved, ev.resized, ev.closed):
        e.__iadd__ = lambda self, f: self
    win = MagicMock()
    win.events = ev
    wv = MagicMock()
    wv.create_window.return_value = win
    if screens is not None:
        wv.screens = [SimpleNamespace(x=a, y=b, width=c, height=d) for a, b, c, d in screens]
    return wv, win


def test_run_native_discards_offscreen_position(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(x=5000, y=5000)
    wv, _ = _fake_webview(screens=[(0, 0, 1920, 1080)])
    window.run_native("http://127.0.0.1:1/", st, wv)  # screens come from the module
    kw = wv.create_window.call_args.kwargs
    assert kw["x"] is None and kw["y"] is None
    assert st.get()["x"] == 5000  # the stored value itself is not rewritten


def test_run_native_keeps_onscreen_position_and_compact_size(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(x=100, y=50, compact=True, compact_size=[390, 710])
    wv, _ = _fake_webview(screens=[(0, 0, 1920, 1080)])
    window.run_native("http://127.0.0.1:1/", st, wv)
    kw = wv.create_window.call_args.kwargs
    assert (kw["x"], kw["y"]) == (100, 50)
    assert (kw["width"], kw["height"]) == (390, 710)
    assert kw["min_size"] == settings.MIN_SIZE


def test_run_native_screens_error_is_tolerated(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(x=5000, y=5000)
    wv, _ = _fake_webview()
    type(wv).screens = property(lambda self: (_ for _ in ()).throw(RuntimeError("no backend")))
    window.run_native("http://127.0.0.1:1/", st, wv)
    assert wv.create_window.call_args.kwargs["x"] == 5000  # no screen info: trusted


def test_run_native_subscribes_events_and_flushes_on_start_error(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    wv, win = _fake_webview(screens=[])
    wv.start.side_effect = RuntimeError("backend died")
    with pytest.raises(RuntimeError):
        window.run_native("http://127.0.0.1:1/", st, wv, screens=[])
    assert (tmp_path / "a.json").exists()  # finally: settings were still saved


def test_run_native_create_window_error_propagates(tmp_path):
    wv, _ = _fake_webview()
    wv.create_window.side_effect = RuntimeError("no GUI")
    with pytest.raises(RuntimeError):
        window.run_native("http://127.0.0.1:1/", SettingsStore(tmp_path / "a.json"), wv, screens=[])


# ---------------------------------------------------------------- WindowApi


@pytest.fixture
def api_env(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    api = window.WindowApi(st)
    win = MagicMock()
    api.attach(win)
    yield api, st, win, tmp_path / "a.json"
    api.flush()  # no timer thread left behind


def test_get_settings_marks_native(api_env):
    api, st, _, _ = api_env
    got = api.get_settings()
    assert got["native"] is True and got["compact"] is False


def test_pin_sets_on_top_and_stores_it(api_env):
    api, st, win, _ = api_env
    assert api.set_on_top(True) is True
    assert win.on_top is True and st.get()["on_top"] is True
    assert api.set_on_top(0) is False  # any value is coerced to bool
    assert win.on_top is False and st.get()["on_top"] is False


def test_mode_switch_resizes_to_remembered_size_of_that_mode(api_env):
    api, st, win, _ = api_env
    st.update(full_size=[1300, 950], compact_size=[400, 800])
    got = api.set_compact(True)
    win.resize.assert_called_with(400, 800)
    assert got["compact"] is True and st.get()["compact"] is True
    api.set_compact(False)
    win.resize.assert_called_with(1300, 950)
    assert st.get()["compact"] is False
    assert win.resize.call_count == 2


def test_resize_event_updates_only_current_mode(api_env):
    api, st, _, _ = api_env
    api.on_resized(1000, 700)
    assert st.get()["full_size"] == [1000, 700]
    assert st.get()["compact_size"] == DEFAULTS["compact_size"]
    api.set_compact(True)
    api.on_resized(360, 650)
    assert st.get()["compact_size"] == [360, 650] and st.get()["full_size"] == [1000, 700]


def test_moved_event_stored(api_env):
    api, st, _, _ = api_env
    api.on_moved(120, 80)
    assert (st.get()["x"], st.get()["y"]) == (120, 80)


def test_moved_garbage_keeps_previous_position(api_env):
    api, st, _, _ = api_env
    api.on_moved(120, 80)
    api.on_moved(float("nan"), 5)
    assert (st.get()["x"], st.get()["y"]) == (120, 80)


def test_api_without_window_does_not_fail(tmp_path):
    api = window.WindowApi(SettingsStore(tmp_path / "a.json"))
    assert api.set_on_top(True) is True
    assert api.set_compact(True)["compact"] is True
    api.flush()


def test_set_compact_resize_error_is_caught_after_state_is_stored(api_env):
    # An exception in window.resize is caught: ok=False, the mode is already stored.
    api, st, win, _ = api_env
    win.resize.side_effect = RuntimeError("closed window")
    assert api.set_compact(True)["ok"] is False
    assert st.get()["compact"] is True


def test_save_is_delayed_and_debounced(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "SAVE_DELAY", 0.15)
    st = SettingsStore(tmp_path / "a.json")
    st.save = MagicMock(wraps=st.save)  # type: ignore[method-assign]
    api = window.WindowApi(st)
    for i in range(5):
        api.on_moved(i, i)
    assert st.save.call_count == 0  # not written synchronously
    assert not (tmp_path / "a.json").exists()
    deadline = time.monotonic() + 3
    while st.save.call_count == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)  # no second save from the 4 cancelled timers
    assert st.save.call_count == 1
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["x"] == 4


def test_flush_saves_immediately_and_cancels_timer(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "SAVE_DELAY", 0.2)
    st = SettingsStore(tmp_path / "a.json")
    st.save = MagicMock(wraps=st.save)  # type: ignore[method-assign]
    api = window.WindowApi(st)
    api.set_on_top(True)
    api.flush()
    assert st.save.call_count == 1
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["on_top"] is True
    time.sleep(0.5)
    assert st.save.call_count == 1  # the pending timer was cancelled
    api.flush()  # idempotent
    assert st.save.call_count == 2


def test_closed_event_is_wired_to_flush(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    wv, win = _fake_webview(screens=[])
    registered = []
    win.events.closed.__iadd__ = lambda self, f: registered.append(f) or self
    api = window.run_native("http://127.0.0.1:1/", st, wv, screens=[])
    assert api.flush in registered or any(
        getattr(f, "__func__", None) is window.WindowApi.flush for f in registered
    )


# ---------------------------------------------------------------- main(): window / browser


@pytest.fixture
def spy_server(monkeypatch):
    """Real AnalyzerServer (port 0, loopback) whose shutdown/server_close calls are recorded."""
    made: list = []

    class Spy(AnalyzerServer):
        def __init__(self, port=0):
            super().__init__(port)
            self.calls: list[str] = []
            self.port = self.server_address[1]
            made.append(self)

        def shutdown(self):
            self.calls.append("shutdown")
            super().shutdown()

        def server_close(self):
            self.calls.append("server_close")
            super().server_close()

    monkeypatch.setattr(app_main, "AnalyzerServer", Spy)
    return made


def _port_closed(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((HOST, port)) != 0


def _port_open(port: int) -> bool:
    return not _port_closed(port)


def _open_browser(monkeypatch):
    """Replace webbrowser.open; it raises KeyboardInterrupt to leave the wait loop."""
    m = MagicMock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(app_main.webbrowser, "open", m)
    return m


def test_closing_window_stops_server_and_frees_port(monkeypatch, spy_server, tmp_path):
    wv = object()
    seen = {}

    def fake_run(url, store, mod):
        seen["url"], seen["mod"] = url, mod
        seen["port"] = spy_server[0].port
        seen["open_while_running"] = _port_open(seen["port"])
        return MagicMock()  # the window is "closed": return

    monkeypatch.setattr(app_main.window, "load_webview", lambda: wv)
    monkeypatch.setattr(app_main.window, "run_native", fake_run)
    browser = _open_browser(monkeypatch)
    assert app_main.main(["--config", str(tmp_path / "c.json")]) == 0
    assert seen["mod"] is wv and seen["url"].startswith(f"http://{HOST}:")
    assert seen["open_while_running"] is True  # the server serves while the window is open
    assert spy_server[0].calls == ["shutdown", "server_close"]
    assert _port_closed(seen["port"])
    browser.assert_not_called()  # no browser when the window worked


def test_window_uses_config_path_from_flag(monkeypatch, spy_server, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"on_top": True}), encoding="utf-8")
    got = {}
    monkeypatch.setattr(app_main.window, "load_webview", lambda: object())
    monkeypatch.setattr(
        app_main.window, "run_native", lambda url, store, mod: got.update(store=store)
    )
    app_main.main(["--config", str(cfg)])
    assert got["store"].path == cfg and got["store"].get()["on_top"] is True


def test_no_pywebview_warns_and_opens_browser_with_nowin(monkeypatch, spy_server, capsys, tmp_path):
    monkeypatch.setattr(app_main.window, "load_webview", lambda: None)
    run = MagicMock()
    monkeypatch.setattr(app_main.window, "run_native", run)
    browser = _open_browser(monkeypatch)
    assert app_main.main([]) == 0
    run.assert_not_called()
    err = capsys.readouterr().err
    assert "pywebview" in err
    (url,) = browser.call_args.args
    assert url == f"http://{HOST}:{spy_server[0].port}/?nowin=missing"
    assert spy_server[0].calls == ["shutdown", "server_close"]
    assert _port_closed(spy_server[0].port)


def test_window_start_error_falls_back_to_browser(monkeypatch, spy_server, capsys):
    monkeypatch.setattr(app_main.window, "load_webview", lambda: object())

    def boom(url, store, mod):
        raise RuntimeError("WebView2 missing")

    monkeypatch.setattr(app_main.window, "run_native", boom)
    browser = _open_browser(monkeypatch)
    assert app_main.main(["--config", "unused.json"]) == 0
    err = capsys.readouterr().err
    assert "WebView2 missing" in err and "браузер" in err
    assert browser.call_args.args[0].endswith("/?nowin=error")
    assert spy_server[0].calls == ["shutdown", "server_close"]
    assert _port_closed(spy_server[0].port)


def test_browser_flag_skips_pywebview_without_warning(monkeypatch, spy_server, capsys):
    loader = MagicMock(return_value=object())
    monkeypatch.setattr(app_main.window, "load_webview", loader)
    run = MagicMock()
    monkeypatch.setattr(app_main.window, "run_native", run)
    browser = _open_browser(monkeypatch)
    app_main.main(["--browser"])
    run.assert_not_called()
    assert "pywebview" not in capsys.readouterr().err  # an explicit choice: no warning
    assert browser.call_args.args[0].endswith("?nowin=browser")
    assert _port_closed(spy_server[0].port)


def test_no_browser_flag_opens_nothing(monkeypatch, spy_server):
    run = MagicMock()
    monkeypatch.setattr(app_main.window, "run_native", run)
    browser = MagicMock()
    monkeypatch.setattr(app_main.webbrowser, "open", browser)
    ticks = {"n": 0}
    real_join = app_main.threading.Thread.join

    def join(self, timeout=None):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt  # Ctrl+C after a moment
        return real_join(self, 0.01)

    monkeypatch.setattr(app_main.threading.Thread, "join", join)
    app_main.main(["--no-browser"])
    run.assert_not_called()
    browser.assert_not_called()
    assert _port_closed(spy_server[0].port)


def test_server_listens_on_loopback_only():
    srv = AnalyzerServer(0)
    try:
        host, port = srv.server_address[:2]
        assert host == "127.0.0.1" and HOST == "127.0.0.1"
        assert srv.url == f"http://127.0.0.1:{port}/"
        assert srv.socket.getsockname()[0] == "127.0.0.1"
    finally:
        srv.server_close()


# ---------------------------------------------------------------- static/: no external URLs


STATIC_FILES = ["index.html", "app.css", "app.js"]
# the SVG namespace is an identifier, not a network request
ALLOWED_URLS = {"http://www.w3.org/2000/svg"}


@pytest.mark.parametrize("name", STATIC_FILES)
def test_static_has_no_external_urls(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    found = []
    for m in re.finditer(r"https?://[^\s\"'`)<>]+|(?<![:\w])//[A-Za-z0-9.\-]+\.[a-z]{2,}", text):
        url = m.group(0)
        if url not in ALLOWED_URLS:
            found.append(url)
    assert not found, f"{name}: external URLs {found}"


@pytest.mark.parametrize("name", STATIC_FILES)
def test_static_has_no_remote_resource_loading(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    assert "@import" not in text
    assert not re.search(r"url\(\s*[\"']?\s*(?:https?:)?//", text)
    assert not re.search(r"@font-face", text), "no web fonts"
    for tag in re.findall(r"<(?:script|link|img|iframe|source|embed|object)\b[^>]*>", text):
        for attr in re.findall(r"(?:src|href|data)\s*=\s*[\"']([^\"']*)[\"']", tag):
            assert not re.match(r"(?:[a-z]+:)?//", attr, re.I), f"{name}: {tag}"
    assert not re.search(r"\bfetch\(\s*[\"'`]https?:", text)
    assert not re.search(r"XMLHttpRequest|WebSocket\(|EventSource\(|sendBeacon", text)


def test_static_dir_contains_only_local_files():
    names = sorted(p.name for p in STATIC.iterdir())
    assert set(STATIC_FILES) <= set(names)
    for p in STATIC.iterdir():
        assert p.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico", ".json"}, p.name


def test_fetches_in_app_js_are_relative():
    text = (STATIC / "app.js").read_text(encoding="utf-8")
    for m in re.finditer(r"fetch\(\s*([^,)]+)", text):
        arg = m.group(1).strip()
        assert not re.match(r"[\"'`]\s*(?:https?:)?//", arg), arg
