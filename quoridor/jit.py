"""Optional numba acceleration (phase 9, step 6): one switch for the whole package.

ENABLED is True when numba imports and the environment variable QUORIDOR_NO_NUMBA is not
set to a non-empty value other than "0". Then quoridor.bitpaths, quoridor.threat and the
search use the compiled kernels of quoridor.kernels; otherwise the pure-Python code (the
reference implementation, always kept) runs. Results are identical either way: the
kernels are exact ports, compared with the Python code in tests/paths/test_jit.py.

njit: numba.njit when ENABLED, else a no-op decorator (with or without arguments), so a
module can be decorated without importing numba itself. Compiled kernels are cached on
disk (cache=True): numba writes them to __pycache__ next to the module (or to
NUMBA_CACHE_DIR if set); __pycache__/ is in .gitignore. The first run after an install or
a change of quoridor/kernels.py compiles them once (some seconds).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

DISABLE_ENV = "QUORIDOR_NO_NUMBA"


def _disabled_by_env() -> bool:
    return os.environ.get(DISABLE_ENV, "") not in ("", "0")


def _identity(*args: Any, **kwargs: Any) -> Any:
    """Stand-in for numba.njit: @njit, @njit(...) and @njit("sig", ...) all return f."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def deco(f: Callable) -> Callable:
        return f

    return deco


ENABLED = False
REASON = ""  # why numba is off ("" when it is on)
njit: Callable[..., Any] = _identity

if _disabled_by_env():
    REASON = f"{DISABLE_ENV} is set"
else:
    try:
        from numba import njit as _numba_njit
    except Exception as e:  # ImportError, or a broken install (llvmlite DLL, numpy ABI)
        REASON = f"numba unavailable: {type(e).__name__}: {e}"
    else:
        njit = _numba_njit
        ENABLED = True

_kernels: Any = None


def kernels() -> Any:
    """quoridor.kernels when ENABLED (imported, i.e. loaded from the cache or compiled,
    on the first call), else None. If the kernels fail to load, numba is switched off
    for the whole process (ENABLED = False, REASON says why) with a warning: the
    pure-Python code then runs, with the same results."""
    global _kernels, ENABLED, REASON
    if not ENABLED:
        return None
    if _kernels is None:
        try:
            from quoridor import kernels as k
        except Exception as e:  # a numba / llvmlite / numpy problem at compile time
            import warnings

            ENABLED = False
            REASON = f"quoridor.kernels failed to load: {type(e).__name__}: {e}"
            warnings.warn(REASON + "; using pure Python", RuntimeWarning, stacklevel=2)
            return None
        _kernels = k
    return _kernels
