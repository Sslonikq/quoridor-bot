"""Phase 5: pawn_moves_for, wall_fits and the fast wall-legality shortcut."""

from dataclasses import replace

import pytest

from quoridor.paths import has_path_to_goal
from quoridor.rules import (
    _with_wall,
    is_wall_legal,
    pawn_moves,
    pawn_moves_for,
    wall_fits,
)
from quoridor.state import BLACK, WALL_GRID, WHITE, GameState, H, V, WallMove
from tests.oracle import ref_wall_legal
from tests.playouts import random_game

ALL_WALLS = [WallMove(x, y, o) for o in (H, V) for x in range(WALL_GRID) for y in range(WALL_GRID)]


def naive_wall_legal(state: GameState, w: WallMove) -> bool:
    """Previous implementation: full BFS for both players after every fitting wall."""
    if not wall_fits(state, w):
        return False
    nxt = _with_wall(state, w)
    return has_path_to_goal(nxt, WHITE) and has_path_to_goal(nxt, BLACK)


def test_pawn_moves_for_matches_replace_turn():
    n = 0
    for seed in range(12):
        for s in random_game(seed, max_plies=80):
            for p in (WHITE, BLACK):
                assert pawn_moves_for(s, p) == pawn_moves(replace(s, turn=p))
                n += 1
            assert pawn_moves(s) == pawn_moves_for(s, s.turn)
    assert n > 500


def test_pawn_moves_for_ignores_turn_initial():
    s = GameState.initial()
    assert {m.cell for m in pawn_moves_for(s, BLACK)} == {(3, 8), (5, 8), (4, 7)}


@pytest.mark.parametrize("seed", range(10))
def test_fast_wall_legal_equals_naive_and_oracle(seed):
    for s in random_game(seed, max_plies=70, wall_bias=0.85):
        for w in ALL_WALLS:
            fast = is_wall_legal(s, w)
            assert fast == naive_wall_legal(s, w), (s, w)
            assert fast == ref_wall_legal(s, w), (s, w)


def test_wall_fits_is_superset_of_legal():
    for s in random_game(3, max_plies=60, wall_bias=0.9):
        for w in ALL_WALLS:
            if is_wall_legal(s, w):
                assert wall_fits(s, w)


def test_wall_fits_rejects_bad_walls():
    s = GameState.initial()
    assert not wall_fits(s, WallMove(8, 0, H))
    assert not wall_fits(s, WallMove(0, 0, "x"))
    assert not wall_fits(replace(s, walls_left=(0, 10)), WallMove(0, 0, H))
    s2 = _with_wall(s, WallMove(3, 3, H))
    assert not wall_fits(s2, WallMove(3, 3, V))  # cross
    assert not wall_fits(s2, WallMove(4, 3, H))  # partial overlap
    assert wall_fits(s2, WallMove(5, 3, H))


def test_shortcut_still_rejects_closing_wall():
    # row of h-walls leaving one gap at the left; closing the gap is illegal
    s = GameState.initial()
    for x in (1, 3, 5, 7):
        s = _with_wall(s, WallMove(x, 3, H))
    assert not is_wall_legal(s, WallMove(0, 3, H))
