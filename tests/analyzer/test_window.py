"""Smoke tests of the window layer (phase 12): settings file and a mocked pywebview."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from quoridor.analyzer import settings, window
from quoridor.analyzer.settings import SettingsStore


def test_missing_or_broken_file_gives_defaults(tmp_path):
    p = tmp_path / "sub" / "a.json"
    assert SettingsStore(p).load() == settings.sanitize(None)
    p.parent.mkdir()
    p.write_text("{not json", encoding="utf-8")
    assert SettingsStore(p).load()["compact"] is False


def test_roundtrip_creates_directory_and_is_atomic(tmp_path):
    p = tmp_path / "new" / "a.json"
    st = SettingsStore(p)
    st.update(x=10, y=20, compact=True, on_top=True, compact_size=[380, 700])
    assert st.save()
    assert [f.name for f in p.parent.iterdir()] == ["a.json"]  # no leftover temp files
    got = SettingsStore(p).load()
    assert (got["x"], got["y"], got["compact"], got["on_top"]) == (10, 20, True, True)
    assert got["compact_size"] == [380, 700]


def test_bad_values_are_ignored_field_by_field(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps({"x": "a", "y": 1, "full_size": [1, 20000], "compact": "yes", "on_top": True}),
        encoding="utf-8",
    )
    got = SettingsStore(p).load()
    assert got["x"] is None and got["compact"] is False and got["on_top"] is True
    assert got["full_size"] == [settings.MIN_SIZE[0], settings.MAX_SIZE[1]]


def test_env_path(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.ENV_PATH, str(tmp_path / "e.json"))
    assert SettingsStore().path == tmp_path / "e.json"


def test_choose_mode_and_missing_webview(monkeypatch):
    assert window.choose_mode(True, None) == "browser"
    assert window.choose_mode(False, object()) == "browser"  # type: ignore[arg-type]
    assert window.choose_mode(True, object()) == "window"  # type: ignore[arg-type]
    monkeypatch.setattr(window.importlib, "import_module", MagicMock(side_effect=ImportError))
    assert window.load_webview() is None


def test_clamp_drops_offscreen_position():
    s = {"x": 5000, "y": 10}
    assert window.clamp_to_screens(s, [(0, 0, 1920, 1080)])["x"] is None
    assert window.clamp_to_screens({"x": 100, "y": 10}, [(0, 0, 1920, 1080)])["x"] == 100


def _fake_webview(tmp_path):
    ev = SimpleNamespace(moved=MagicMock(), resized=MagicMock(), closed=MagicMock())
    for e in (ev.moved, ev.resized, ev.closed):
        e.__iadd__ = lambda self, f: self
    win = MagicMock()
    win.events = ev
    wv = MagicMock()
    wv.create_window.return_value = win
    return wv, win


def test_run_native_saves_on_close_and_api_resizes(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    st.update(on_top=True)
    wv, win = _fake_webview(tmp_path)
    api = window.run_native("http://127.0.0.1:1/", st, wv, screens=[])
    kw = wv.create_window.call_args.kwargs
    assert kw["on_top"] is True and kw["js_api"] is api and kw["width"] == 1180
    wv.start.assert_called_once()
    assert (tmp_path / "a.json").exists()  # saved when the GUI loop ended
    api.set_compact(True)
    win.resize.assert_called_with(*st.get()["compact_size"])
    api.on_resized(400, 650)  # remembered for the compact mode, not the full one
    assert st.get()["compact_size"] == [400, 650] and st.get()["full_size"] == [1180, 900]
    api.set_on_top(False)
    assert win.on_top is False
    api.flush()
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["on_top"] is False


def test_minimized_maximized_and_tiny_geometry_is_not_saved(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    api = window.WindowApi(st)
    api.on_moved(100, 50)
    api.on_resized(900, 700)
    api.on_state("minimized")
    api.on_moved(-32000, -32000)
    api.on_resized(160, 28)
    api.on_state("restored")
    api.on_resized(100, 100)  # implausible even in the normal state
    got = st.get()
    assert (got["x"], got["y"], got["full_size"]) == (100, 50, [900, 700])
    api.on_state("maximized")
    api.on_resized(1920, 1080)
    assert st.get()["full_size"] == [900, 700]
    api.flush()


# --- phase 12 minors (report 0113) ---


def test_offscreen_minimized_coordinates_are_ignored_in_normal_state(tmp_path):
    """-32000 (Windows 'minimized' position) is dropped even without any state event."""
    st = SettingsStore(tmp_path / "a.json")
    api = window.WindowApi(st)
    api.on_moved(100, 50)
    api.on_moved(-32000, -32000)
    api.on_moved(-32000, 10)
    api.on_moved(10, -32000)
    assert (st.get()["x"], st.get()["y"]) == (100, 50)
    api.flush()


def test_fresh_api_offscreen_position_keeps_default(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    before = (st.get()["x"], st.get()["y"])
    api = window.WindowApi(st)
    api.on_moved(-32000, -32000)
    assert (st.get()["x"], st.get()["y"]) == before
    api.flush()


def test_second_monitor_on_the_left_is_saved_at_boundary(tmp_path):
    st = SettingsStore(tmp_path / "a.json")
    api = window.WindowApi(st)
    api.on_moved(-29999, 0)
    assert (st.get()["x"], st.get()["y"]) == (-29999, 0)
    api.on_moved(-1920, 0)
    assert (st.get()["x"], st.get()["y"]) == (-1920, 0)
    api.on_moved(-30000, 0)  # the filter is inclusive: -30000 is dropped
    assert st.get()["x"] == -1920
    api.flush()
