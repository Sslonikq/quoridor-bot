"""apply_move_unchecked/_with_wall build GameState directly; must equal the replace() version."""

from dataclasses import replace

import pytest

from quoridor.rules import _with_wall, apply_move_unchecked, legal_moves
from quoridor.state import GameState, H, PawnMove, WallMove
from tests.playouts import random_game


def ref_with_wall(state: GameState, w: WallMove) -> GameState:
    if w.orient == H:
        return replace(state, h_walls=state.h_walls | {(w.x, w.y)})
    return replace(state, v_walls=state.v_walls | {(w.x, w.y)})


def ref_apply(state: GameState, move) -> GameState:
    p = state.turn
    if isinstance(move, PawnMove):
        pawns = list(state.pawns)
        pawns[p] = move.cell
        return replace(state, pawns=(pawns[0], pawns[1]), turn=1 - p)
    nxt = ref_with_wall(state, move)
    left = list(state.walls_left)
    left[p] -= 1
    return replace(nxt, walls_left=(left[0], left[1]), turn=1 - p)


@pytest.mark.parametrize("seed", range(8))
def test_apply_equals_replace_version(seed):
    n = 0
    for s in random_game(seed, max_plies=90, wall_bias=0.6):
        for m in legal_moves(s):
            got = apply_move_unchecked(s, m)
            assert got == ref_apply(s, m)
            assert type(got.pawns) is tuple and type(got.walls_left) is tuple
            assert isinstance(got.h_walls, frozenset) and isinstance(got.v_walls, frozenset)
            if isinstance(m, WallMove):
                assert _with_wall(s, m) == ref_with_wall(s, m)
            n += 1
    assert n > 500
