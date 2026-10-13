import time

import pytest

from quoridor.bots import GreedyBot, RandomBot, candidate_walls
from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import apply_move, is_legal_move, is_terminal, pawn_moves, wall_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from tests.helpers import mk
from tests.playouts import random_game

MAX_PLIES = 300


def play(white, black, max_plies=MAX_PLIES):
    """Play a game; every move is checked for legality. Returns (final_state, plies)."""
    s = GameState.initial()
    bots = (white, black)
    plies = 0
    while not is_terminal(s) and plies < max_plies:
        m = bots[s.turn].choose_move(s)
        assert is_legal_move(s, m)
        s = apply_move(s, m)
        plies += 1
    return s, plies


@pytest.mark.slow
def test_moves_always_legal_on_random_positions():
    for seed in range(4):
        for i, s in enumerate(random_game(seed, max_plies=60)):
            if is_terminal(s) or i % 5:
                continue
            assert is_legal_move(s, RandomBot(seed).choose_move(s))
            assert is_legal_move(s, GreedyBot(seed).choose_move(s))


def test_determinism_same_seed():
    for cls in (RandomBot, GreedyBot):
        a, _ = play(cls(7), cls(7), max_plies=40)
        b, _ = play(cls(7), cls(7), max_plies=40)
        assert a == b


def test_greedy_wins_in_one_move():
    s = mk(white="a8", black="e9")
    assert GreedyBot(0).choose_move(s) == PawnMove(0, 8)
    s = mk(white="e1", black="a2", turn=1)
    assert GreedyBot(0).choose_move(s) == PawnMove(0, 0)


@pytest.mark.slow
def test_greedy_chooses_wall_only_if_strictly_better():
    walls_chosen = 0
    for seed in range(4):
        for i, s in enumerate(random_game(seed, max_plies=60)):
            if is_terminal(s) or i % 4:
                continue
            me = s.turn
            m = GreedyBot(0).choose_move(s)
            if isinstance(m, WallMove):
                walls_chosen += 1
                best_pawn = max(GreedyBot._score(apply_move(s, p), me) for p in pawn_moves(s))
                assert GreedyBot._score(apply_move(s, m), me) > best_pawn
    assert walls_chosen > 0


def _lengthening_walls(s):
    me = s.turn
    base = shortest_path_length(s, 1 - me)
    return {w for w in wall_moves(s) if shortest_path_length(apply_move(s, w), 1 - me) > base}


def test_candidate_walls_complete_for_lengthening():
    s = mk(white="e4", black="e6", turn=0)
    cands = set(candidate_walls(s))
    assert _lengthening_walls(s) <= cands
    path = shortest_path(s, BLACK)
    assert path[0] == s.pawns[BLACK] and path[-1][1] == 0


@pytest.mark.parametrize("seed", range(6))
def test_candidate_walls_complete_on_random_positions(seed):
    checked = 0
    for i, s in enumerate(random_game(seed, max_plies=70, wall_bias=0.6)):
        if is_terminal(s) or i % 4:
            continue
        cands = candidate_walls(s)
        assert len(cands) == len(set(cands))
        assert set(cands) <= set(wall_moves(s))  # every candidate is legal
        assert _lengthening_walls(s) <= set(cands), (seed, i)
        checked += 1
    assert checked > 5


@pytest.mark.parametrize("turn", [0, 1])
def test_candidate_walls_empty_when_no_walls_left(turn):
    walls_left = (0, 10) if turn == 0 else (10, 0)
    s = mk(white="e4", black="e6", turn=turn, walls_left=walls_left)
    assert candidate_walls(s) == []
    # the opponent still has walls, so the generator is keyed to the side to move
    s2 = mk(white="e4", black="e6", turn=turn, walls_left=(10, 10))
    assert candidate_walls(s2)


@pytest.mark.slow
def test_greedy_beats_random():
    wins = 0
    games = 20
    for i in range(games):
        g, rnd = GreedyBot(i), RandomBot(1000 + i)
        if i % 2 == 0:
            final, _ = play(g, rnd)
            wins += winner(final) == WHITE
        else:
            final, _ = play(rnd, g)
            wins += winner(final) == BLACK
    assert wins >= 18, wins


def test_bot_vs_bot_terminates():
    final, plies = play(GreedyBot(1), RandomBot(2))
    assert is_terminal(final) and plies < MAX_PLIES


def test_greedy_time_per_move():
    s = GameState.initial()
    t = time.perf_counter()
    GreedyBot(0).choose_move(s)
    assert time.perf_counter() - t < 1.0
