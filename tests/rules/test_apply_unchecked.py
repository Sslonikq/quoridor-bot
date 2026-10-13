import pytest

from quoridor.rules import apply_move, apply_move_unchecked, legal_moves
from tests.playouts import random_game


@pytest.mark.slow
def test_unchecked_equals_checked_on_legal_moves():
    n = 0
    for seed in range(3):
        for s in random_game(seed, max_plies=40, wall_bias=0.5):
            for m in legal_moves(s)[:25]:
                assert apply_move_unchecked(s, m) == apply_move(s, m)
                n += 1
    assert n > 100
