"""python -m quoridor.analyzer [--port N] [--no-browser]: start the analyzer."""

from __future__ import annotations

import argparse
import webbrowser

from quoridor.analyzer.server import AnalyzerServer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quoridor.analyzer", description=__doc__)
    ap.add_argument("--port", type=int, default=0, help="0 = random free port (default)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)
    server = AnalyzerServer(args.port)
    print(f"Анализатор: {server.url}  (Ctrl+C: выход)", flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
