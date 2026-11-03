"""EndgameSolver.solve: should_stop counts as a budget (review 0095 nit 3, report 0099).

Without time_limit and max_nodes but with should_stop the MAX_REPEATS cap does not apply:
the depth is repeated until should_stop() cancels; without should_stop the cap stays."""

from __future__ import annotations

import pytest

from quoridor import endgame as eg
from quoridor import jit
from quoridor.endgame import EndgameSolver, SolverCancelled
from quoridor.state import WHITE, GameState

needs_jit = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")
CORES = ["py", pytest.param("jit", marks=needs_jit)]
CAP = 5  # MAX_REPEATS for the test: a chunk of 1 node never finishes depth 1


def _state() -> GameState:
    return GameState(((4, 1), (4, 7)), frozenset(), frozenset(), (2, 2), WHITE)


@pytest.mark.parametrize("core", CORES)
def test_should_stop_lifts_the_repeat_cap(core, monkeypatch):
    monkeypatch.setattr(eg, "MAX_REPEATS", CAP)
    checks = 0

    def stop() -> bool:
        nonlocal checks
        checks += 1
        return checks > 4 * CAP

    with pytest.raises(SolverCancelled):
        EndgameSolver(tt_bits=10, core=core).solve(_state(), chunk=1, should_stop=stop)
    assert checks == 4 * CAP + 1  # went past CAP calls, stopped only by should_stop


@pytest.mark.parametrize("core", CORES)
def test_without_should_stop_the_cap_stays(core, monkeypatch):
    monkeypatch.setattr(eg, "MAX_REPEATS", CAP)
    res = EndgameSolver(tt_bits=10, core=core).solve(_state(), chunk=1)
    assert not res.proven and res.move is None
    assert res.nodes <= CAP * (res.depth + 1)
