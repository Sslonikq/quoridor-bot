"""Nit 8 of review 0040: v3 must beat greedy (50 games, seed 0, measured 50-0)."""

import pytest

from quoridor.arena import run_match


@pytest.mark.slow
def test_v3_beats_greedy_on_both_colours():
    m = run_match(50, "v3", "greedy", seed=0)
    assert m.total.draws == 0
    assert m.total.wins >= 48  # measured 50/0/0; small slack for future eval changes
    assert m.as_white.wins >= 23 and m.as_black.wins >= 23
