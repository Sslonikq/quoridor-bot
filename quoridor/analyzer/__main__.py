"""python -m quoridor.analyzer [--port N] [--no-browser] [--browser] [--config PATH]

Starts the analyzer. With pywebview (pip install -e .[app]) it opens a native window; without
it (or with --browser) the page opens in the default browser. Closing the window stops the
server.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from quoridor.analyzer import window
from quoridor.analyzer.server import AnalyzerServer
from quoridor.analyzer.settings import SettingsStore

NO_WEBVIEW_WARNING = (
    "Предупреждение: pywebview не установлен, открываю в браузере (окно: pip install -e .[app])."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quoridor.analyzer", description=__doc__)
    ap.add_argument("--port", type=int, default=0, help="0 = random free port (default)")
    ap.add_argument("--no-browser", action="store_true", help="only serve, open nothing")
    ap.add_argument("--browser", action="store_true", help="plain browser even if pywebview exists")
    ap.add_argument("--config", help="settings file (default ~/.quoridor/analyzer.json)")
    args = ap.parse_args(argv)
    server = AnalyzerServer(args.port)
    print(f"Анализатор: {server.url}  (Ctrl+C: выход)", flush=True)
    want_window = not (args.no_browser or args.browser)
    wv = window.load_webview() if want_window else None
    mode = window.choose_mode(want_window, wv)
    nowin = "browser" if args.browser else "missing"
    if want_window and wv is None:
        print(NO_WEBVIEW_WARNING, file=sys.stderr, flush=True)
    thread = threading.Thread(target=server.serve_forever, name="analyzer-server", daemon=True)
    thread.start()
    try:
        if mode == "window":
            store = SettingsStore(args.config)
            store.load()
            try:
                window.run_native(server.url, store, wv)
                return 0  # the window was closed; finally stops the server
            except Exception as e:  # GUI backend missing or broken: fall back to the browser
                nowin = "error"
                print(
                    f"Предупреждение: окно не открылось ({e}); открываю в браузере.",
                    file=sys.stderr,
                    flush=True,
                )
        if not args.no_browser:
            webbrowser.open(f"{server.url}?nowin={nowin}")
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
