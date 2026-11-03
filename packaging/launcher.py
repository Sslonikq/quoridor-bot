"""Entry point of the frozen app (PyInstaller): QuoridorAnalyzer.exe.

Before anything imports numba, the kernel cache is moved to ~/.quoridor/numba-cache (the
unpacked bundle is temporary), then the usual analyzer main() runs. Offline, 127.0.0.1 only.
"""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    cache = Path.home() / ".quoridor" / "numba-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(cache))

from quoridor.analyzer.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
