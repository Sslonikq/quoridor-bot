"""Window icon path (sources / frozen bundle / missing) and favicon (report 0118)."""

import http.client
import re
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quoridor.analyzer import window
from quoridor.analyzer.server import HOST, AnalyzerServer
from quoridor.analyzer.settings import SettingsStore

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "quoridor/analyzer/static"


def test_icon_path_in_sources():
    p = window.icon_path()
    assert p is not None and Path(p) == ROOT / "assets" / "icon.ico"


def test_icon_path_prefers_frozen_bundle(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "icon.ico").write_bytes(b"x")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert Path(window.icon_path()) == tmp_path / "assets" / "icon.ico"


def test_icon_path_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert window.icon_path() is None


def _run(monkeypatch, tmp_path, icon):
    monkeypatch.setattr(window, "icon_path", lambda: icon)
    wv = MagicMock()
    window.run_native("http://127.0.0.1:1/", SettingsStore(tmp_path / "s.json"), wv, screens=[])
    return wv


def test_start_gets_icon(monkeypatch, tmp_path):
    wv = _run(monkeypatch, tmp_path, "C:/x/icon.ico")
    wv.start.assert_called_once_with(icon="C:/x/icon.ico")


def test_start_without_icon_file(monkeypatch, tmp_path):
    wv = _run(monkeypatch, tmp_path, None)
    wv.start.assert_called_once_with()


def test_spec_bundles_icon():
    spec = (ROOT / "packaging/quoridor_analyzer.spec").read_text(encoding="utf-8")
    assert re.search(r'"assets",\s*"icon\.ico"\)\s*,\s*"assets"', spec)


def test_favicon_linked_and_no_external_links():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/png" href="favicon.png">' in html
    assert not re.search(r'(?:href|src)="(?:https?:)?//', html)
    assert (STATIC / "favicon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_favicon_served_as_png():
    srv = AnalyzerServer(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection(HOST, srv.server_address[1], timeout=10)
        c.request("GET", "/favicon.png")
        r = c.getresponse()
        body = r.read()
        c.close()
        assert r.status == 200 and r.getheader("Content-Type") == "image/png"
        assert body == (STATIC / "favicon.png").read_bytes()
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("name", ["icon.ico", "favicon_64.png"])
def test_assets_exist(name):
    assert (ROOT / "assets" / name).is_file()
