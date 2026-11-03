"""Regression for the 📌 hang (report 0109): pywebview 6.x WinForms sets TopMost in the
calling thread; from a js_api thread that deadlocked the UI ("Not responding")."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quoridor.analyzer import window


def _install_fake_winforms(monkeypatch, form):
    system = types.ModuleType("System")
    system.Type = object
    system.Func = type("Func", (), {"__class_getitem__": classmethod(lambda cls, t: lambda f: f)})
    pkg = types.ModuleType("webview.platforms.winforms")
    pkg.BrowserView = types.SimpleNamespace(instances={"uid1": form} if form else {})
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", pkg)


def test_on_top_is_marshalled_to_ui_thread_on_winforms(monkeypatch):
    form = MagicMock()
    form.Invoke.side_effect = lambda fn: fn()
    _install_fake_winforms(monkeypatch, form)
    win = MagicMock(uid="uid1")
    window.set_window_on_top(win, True)
    form.Invoke.assert_called_once()
    assert form.TopMost is True
    # the unsafe pywebview property setter (cross-thread TopMost) must not be used
    assert not any(c[0] == "on_top" for c in win.mock_calls)


def test_on_top_falls_back_to_property_without_winforms(monkeypatch):
    _install_fake_winforms(monkeypatch, None)  # no form for this uid
    win = MagicMock(uid="other")
    window.set_window_on_top(win, True)
    assert win.on_top is True


def test_invoke_failure_warns_and_still_saves(monkeypatch, tmp_path, capsys):
    from quoridor.analyzer.settings import SettingsStore

    form = MagicMock()
    form.Invoke.side_effect = RuntimeError("form disposed")
    _install_fake_winforms(monkeypatch, form)
    store = SettingsStore(tmp_path / "a.json")
    api = window.WindowApi(store)
    api.attach(MagicMock(uid="uid1"))
    saved = MagicMock()
    monkeypatch.setattr(api, "save_soon", saved)
    assert api.set_on_top(True) is True  # the exception does not escape
    form.Invoke.assert_called_once()
    saved.assert_called_once_with()
    assert store.get()["on_top"] is True
    err = capsys.readouterr().err
    assert "Предупреждение" in err and "form disposed" in err


def test_on_top_falls_back_when_winforms_module_not_loaded(monkeypatch):
    system = types.ModuleType("System")
    system.Type = object
    system.Func = type("Func", (), {"__class_getitem__": classmethod(lambda cls, t: lambda f: f)})
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.delitem(sys.modules, "webview.platforms.winforms", raising=False)
    # an import attempt would find a loadable module: it must not be tried
    real_import = __import__

    tried = []  # recorded, not asserted: the code under test swallows exceptions

    def guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("webview.platforms.winforms") or (
            name == "webview.platforms" and "winforms" in (fromlist or ())
        ):
            tried.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guard)
    win = MagicMock(uid="uid1")
    window.set_window_on_top(win, True)
    assert win.on_top is True
    assert tried == []
    assert "webview.platforms.winforms" not in sys.modules


def test_on_state_handlers_are_plain_partials():
    import functools

    api = window.WindowApi(MagicMock())
    f = functools.partial(api.on_state, "minimized")
    f()
    assert "minimized" in api._abnormal


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="WinForms backend")
def test_real_window_pin_and_resize_do_not_hang(tmp_path):
    pytest.importorskip("webview")
    probe = Path(__file__).with_name("real_window_probe.py")
    r = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "cfg.json")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = r.stdout
    assert "RESULT pin_on true" in out, out + r.stderr
    assert "RESULT pin_off false" in out
    assert "RESULT compact True" in out and "RESULT full True" in out
    assert "RESULT responsive 2" in out and "RESULT exited" in out


class _FakeEvent:
    """pywebview Event: `+=` subscribes; firing trims arguments to the handler signature."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, f):
        self.handlers.append(f)
        return self

    def fire(self, *args):
        import inspect

        for f in self.handlers:
            n = len(inspect.signature(f).parameters)
            f(*args[:n])


def _state_webview(with_state_events=True):
    names = ["moved", "resized", "closed"]
    if with_state_events:
        names += ["minimized", "maximized", "restored"]
    ev = types.SimpleNamespace(**{n: _FakeEvent() for n in names})
    win = MagicMock()
    win.events = ev
    wv = MagicMock()
    wv.create_window.return_value = win
    return wv, ev


def _run(tmp_path, wv):
    from quoridor.analyzer.settings import SettingsStore

    return window.run_native("http://127.0.0.1:1/", SettingsStore(tmp_path / "a.json"), wv, [])


def test_run_native_subscribes_state_events(tmp_path):
    wv, ev = _state_webview()
    api = _run(tmp_path, wv)
    for n in ("minimized", "maximized", "restored"):
        assert len(getattr(ev, n).handlers) == 1
    ev.minimized.fire()
    assert api._abnormal == {"minimized"}
    ev.maximized.fire()
    assert api._abnormal == {"minimized", "maximized"}


@pytest.mark.parametrize("args", [(), ("extra",)])
def test_state_handlers_bind_their_own_name(tmp_path, args):
    """Each handler carries its own event name (not the last loop value), with or without
    an event argument passed by the GUI (pywebview trims it to the signature)."""
    wv, ev = _state_webview()
    api = _run(tmp_path, wv)
    ev.maximized.fire(*args)
    assert api._abnormal == {"maximized"}
    ev.minimized.fire(*args)
    assert api._abnormal == {"maximized", "minimized"}


def test_restored_clears_minimized_and_maximized(tmp_path):
    wv, ev = _state_webview()
    api = _run(tmp_path, wv)
    ev.minimized.fire()
    ev.maximized.fire()
    ev.restored.fire()
    assert api._abnormal == set()
    api.on_resized(900, 700)  # geometry is saved again
    assert api._store.get()["full_size"] == [900, 700]


def test_missing_state_events_do_not_break_run_native(tmp_path):
    wv, _ = _state_webview(with_state_events=False)
    api = _run(tmp_path, wv)
    assert api._abnormal == set()
