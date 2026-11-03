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
