import dataclasses
import random

import pytest

from quoridor.rules import apply_move, is_legal_move, is_terminal, legal_moves, winner
from quoridor.state import GameState, PawnMove, WallMove
from tests.helpers import mk


def test_apply_pawn_move_switches_turn():
    s = GameState.initial()
    n = apply_move(s, PawnMove(4, 1))
    assert n.pawns == ((4, 1), (4, 8))
    assert n.turn == 1
    assert s.pawns == ((4, 0), (4, 8))


def test_illegal_move_raises():
    with pytest.raises(ValueError):
        apply_move(GameState.initial(), PawnMove(4, 2))


def test_state_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        GameState.initial().turn = 1  # type: ignore[misc]


def test_white_wins_on_row_9():
    s = mk(white="e8", black="a5")
    n = apply_move(s, PawnMove(4, 8))
    assert winner(n) == 0 and is_terminal(n)
    assert legal_moves(n) == []


def test_black_wins_on_row_1():
    s = mk(white="a5", black="e2", turn=1)
    n = apply_move(s, PawnMove(4, 0))
    assert winner(n) == 1


def test_no_winner_initially():
    assert winner(GameState.initial()) is None


def test_jump_onto_goal_row_wins():
    s = mk(white="e7", black="e8")
    n = apply_move(s, PawnMove(4, 8))
    assert winner(n) == 0


def test_is_legal_move_wall_and_pawn():
    s = GameState.initial()
    assert is_legal_move(s, PawnMove(4, 1))
    assert not is_legal_move(s, PawnMove(5, 1))
    assert is_legal_move(s, WallMove(0, 0, "h"))


def test_random_playouts_stay_consistent():
    rng = random.Random(1)
    for _ in range(15):
        s = GameState.initial()
        for _ply in range(300):
            if is_terminal(s):
                break
            moves = legal_moves(s)
            assert moves
            s = apply_move(s, rng.choice(moves))
            assert s.walls_left[0] >= 0 and s.walls_left[1] >= 0
            assert len(s.h_walls) + len(s.v_walls) == 20 - sum(s.walls_left)
