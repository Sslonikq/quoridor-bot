"""Property-based and differential tests over reachable positions."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from quoridor.notation import format_move, parse_move
from quoridor.paths import has_path_to_goal
from quoridor.rules import (
    apply_move,
    is_legal_move,
    is_terminal,
    is_wall_legal,
    legal_moves,
    pawn_moves,
    winner,
)
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from tests.oracle import ref_pawn_dests, ref_wall_legal
from tests.playouts import random_game

HSETTINGS = settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def check_invariants(s: GameState) -> None:
    assert has_path_to_goal(s, WHITE)
    assert has_path_to_goal(s, BLACK)
    assert min(s.walls_left) >= 0
    assert len(s.h_walls) + len(s.v_walls) + sum(s.walls_left) == 20
    assert s.pawns[0] != s.pawns[1]
    assert s.turn in (0, 1)


@pytest.mark.slow
@HSETTINGS
@given(seed=st.integers(0, 10**9), bias=st.floats(0.0, 1.0))
def test_hypothesis_invariants_hold_in_every_position(seed, bias):
    for s in random_game(seed, max_plies=40, wall_bias=bias):
        check_invariants(s)


@pytest.mark.slow
@HSETTINGS
@given(seed=st.integers(0, 10**9))
def test_hypothesis_every_generated_move_keeps_position_legal(seed):
    for s in random_game(seed, max_plies=12):
        if is_terminal(s):
            break
        for m in legal_moves(s):
            assert is_legal_move(s, m)
        # applying a sample of moves (all pawn moves + a few walls) stays consistent
        ms = legal_moves(s)
        for m in ms[:: max(1, len(ms) // 12)]:
            check_invariants(apply_move(s, m))


@pytest.mark.slow
@HSETTINGS
@given(seed=st.integers(0, 10**9))
def test_hypothesis_notation_roundtrip_for_all_legal_moves(seed):
    for s in random_game(seed, max_plies=12):
        for m in legal_moves(s):
            assert parse_move(format_move(m)) == m


@pytest.mark.slow
@HSETTINGS
@given(seed=st.integers(0, 10**9))
def test_hypothesis_pawn_moves_match_reference_model(seed):
    for s in random_game(seed, max_plies=40, wall_bias=0.7):
        if is_terminal(s):
            break
        got = [m.cell for m in pawn_moves(s)]
        assert len(got) == len(set(got))
        assert set(got) == ref_pawn_dests(s)


@pytest.mark.slow
@HSETTINGS
@given(seed=st.integers(0, 10**9))
def test_hypothesis_wall_legality_matches_reference_model(seed):
    for s in random_game(seed, max_plies=20, wall_bias=0.8):
        if is_terminal(s):
            break
        for o in "hv":
            for x in range(-1, 9):
                for y in range(-1, 9):
                    w = WallMove(x, y, o)
                    assert is_wall_legal(s, w) == ref_wall_legal(s, w), (s, w)


# --- deterministic seeded variants (do not depend on hypothesis) -------------
@pytest.mark.parametrize("seed", range(12))
def test_seeded_full_games_run_without_errors(seed):
    last = None
    for s in random_game(seed, max_plies=400, wall_bias=0.5):
        check_invariants(s)
        last = s
    assert last is not None
    if is_terminal(last):
        assert winner(last) in (WHITE, BLACK)
        assert legal_moves(last) == []
    else:
        assert legal_moves(last)


def test_all_20_walls_can_be_placed_and_game_continues():
    """Pure wall spam with a seed: wall counters never underflow, 20 is the cap."""
    s = GameState.initial()
    placed = 0
    for x in range(0, 8, 2):  # a lattice of non-overlapping walls, safe w.r.t. paths
        for y in range(1, 8, 2):
            for o in "hv":
                w = WallMove(x, y, o)
                if is_wall_legal(s, w):
                    s = apply_move(s, w)
                    placed += 1
                    check_invariants(s)
    assert placed == len(s.h_walls) + len(s.v_walls)
    assert placed + sum(s.walls_left) == 20
    # once a side runs out the generator stops offering walls for it
    s = GameState(s.pawns, s.h_walls, s.v_walls, (0, 0), s.turn)
    assert all(isinstance(m, PawnMove) for m in legal_moves(s))
