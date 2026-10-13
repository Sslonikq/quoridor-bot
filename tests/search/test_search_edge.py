"""Phase 4, test-engineer: edge cases, alpha-beta vs plain negamax, time limit, loops."""

import random
import time

import pytest

from quoridor.arena import game_seed, play_game, run_match
from quoridor.bots import DEFAULT_MAX_MOVES, GreedyBot, SearchBot, make_bot
from quoridor.eval import WIN
from quoridor.notation import parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import (
    apply_move,
    apply_move_unchecked,
    is_legal_move,
    is_terminal,
    legal_moves,
    pawn_moves,
    winner,
)
from quoridor.search import Searcher, is_win_score
from quoridor.state import BLACK, WHITE, H, PawnMove, WallMove, goal_row
from tests.helpers import mk
from tests.playouts import random_game


def walls(*names):
    """('d9v', 'a4h') -> (h_walls, v_walls) as internal (x, y) sets, via notation.py."""
    h, v = [], []
    for n in names:
        m = parse_move(n)
        assert isinstance(m, WallMove)
        (h if m.orient == H else v).append((m.x, m.y))
    return h, v


def sample_positions(seeds, every, max_plies=70, wall_bias=0.6):
    out = []
    for seed in seeds:
        for i, s in enumerate(random_game(seed, max_plies=max_plies, wall_bias=wall_bias)):
            if not is_terminal(s) and i % every == 0:
                out.append(s)
    return out


# ---------- jumps / diagonals inside the search ----------


def test_diagonal_win_at_the_edge_of_the_board():
    # white a8, black a9 right in front: no straight jump (edge), the diagonal b9 wins.
    s = mk(white="a8", black="a9")
    r = Searcher(0).search(s, max_depth=2)
    assert r.move == PawnMove(1, 8)
    assert r.score == WIN - 1
    # mirrored for black: black i2, white i1 below, diagonal h1 wins.
    s = mk(white="i1", black="i2", turn=BLACK)
    assert Searcher(0).search(s, max_depth=2).move == PawnMove(7, 0)


def test_diagonal_win_picks_the_open_side():
    # white e8, black e9: both d9 and f9 win, unless a wall closes one of them.
    s = mk(white="e8", black="e9")
    assert Searcher(0).search(s, max_depth=1).move in (PawnMove(3, 8), PawnMove(5, 8))
    h, v = walls("e9v")  # between e and f: closes f9 (and f8) for the diagonal
    s = mk(white="e8", black="e9", h=h, v=v)
    assert Searcher(0).search(s, max_depth=3).move == PawnMove(3, 8)
    h, v = walls("d9v")  # between d and e: closes d9
    s = mk(white="e8", black="e9", h=h, v=v)
    assert Searcher(0).search(s, max_depth=3).move == PawnMove(5, 8)


def test_straight_jump_blocked_by_wall_behind_opponent_is_not_chosen():
    h, v = walls("e9h")  # wall between rows 9 and 8 over e/f: blocks the straight jump over e8
    s = mk(white="e7", black="e8", h=h, v=v)
    r = Searcher(0).search(s, max_depth=3)
    assert is_legal_move(s, r.move)
    assert not (isinstance(r.move, PawnMove) and r.move.row == 8 and r.move.col == 4)


def test_search_never_returns_a_jump_that_is_not_legal():
    # Pawns face to face in the middle of many walls: every returned move must be legal.
    h, v = walls("e5h", "c4h", "f3v", "d6v")
    for white, black in (("e5", "e6"), ("e4", "e5"), ("d5", "e5")):
        for turn in (WHITE, BLACK):
            s = mk(white=white, black=black, h=h, v=v, turn=turn)
            if not is_terminal(s):
                for depth in (1, 2, 3):
                    assert is_legal_move(s, Searcher(1).search(s, max_depth=depth).move)


# ---------- walls stock ----------


def test_no_walls_for_either_side_only_pawn_moves():
    s = mk(white="a3", black="i7", walls_left=(0, 0))
    sr = Searcher(0)
    assert sr.candidate_walls(s) == []
    for depth in (1, 2, 3):
        m = sr.search(s, max_depth=depth).move
        assert isinstance(m, PawnMove)
    assert Searcher(0).search(s, max_depth=3).move == PawnMove(0, 3)  # a4: straight ahead


def test_side_without_walls_never_places_one_even_when_the_other_has_walls():
    for seed in range(3):
        for s in sample_positions([seed], 5):
            me = s.turn
            s2 = s.__class__(
                pawns=s.pawns,
                h_walls=s.h_walls,
                v_walls=s.v_walls,
                walls_left=tuple(0 if i == me else n for i, n in enumerate(s.walls_left)),
                turn=me,
            )
            r = Searcher(seed).search(s2, max_depth=2)
            assert isinstance(r.move, PawnMove)


def test_walls_run_out_generator_stops_offering_walls():
    s = mk(white="e5", black="e6", walls_left=(0, 3))
    assert Searcher(0).candidate_walls(s) == []


def test_single_legal_move_is_returned_at_any_limit():
    # white a1, a wall over a1/b1 leaves only b1, and white has no walls.
    h, v = walls("a2h")
    s = mk(white="a1", black="e9", h=h, v=v, walls_left=(0, 10))
    assert legal_moves(s) == [PawnMove(1, 0)]
    assert Searcher(0).search(s, max_depth=4).move == PawnMove(1, 0)
    assert Searcher(0).search(s, time_limit=0.05).move == PawnMove(1, 0)
    assert SearchBot(0, depth=3).choose_move(s) == PawnMove(1, 0)


# ---------- forced lines ----------


def test_forced_win_in_two_own_moves():
    # black has no walls and is far away: e7 -> e8 -> e9 cannot be stopped.
    s = mk(white="e7", black="a5", turn=WHITE, walls_left=(10, 0))
    r = Searcher(0).search(s, max_depth=3)
    assert r.move == PawnMove(4, 7)
    assert is_win_score(r.score) and r.score > 0
    # the same position is also found by deeper searches (and stops early)
    assert Searcher(0).search(s, max_depth=6).score == r.score


def test_forced_loss_in_two_is_seen_as_a_loss():
    s = mk(white="e5", black="e3", turn=BLACK, walls_left=(0, 0))
    # black needs 2 steps (e2, e1), white 4 and cannot do anything: black wins.
    r = Searcher(0).search(s, max_depth=3)
    assert r.move == PawnMove(4, 1)
    assert is_win_score(r.score) and r.score > 0
    s = mk(white="e5", black="e3", turn=WHITE, walls_left=(0, 0))
    r = Searcher(0).search(s, max_depth=4)  # white to move, no walls: lost
    assert is_win_score(r.score) and r.score < 0
    assert is_legal_move(s, r.move)


def test_must_wall_to_stop_a_win_in_two():
    # black e3 wins in two steps, white is 6 steps away: only walls help.
    s = mk(white="a3", black="e3", turn=WHITE, walls_left=(10, 0))
    r = Searcher(0).search(s, max_depth=3)
    assert isinstance(r.move, WallMove)
    assert not is_win_score(r.score) or r.score > 0
    after = apply_move(s, r.move)
    assert shortest_path_length(after, BLACK) > 2


def test_faster_win_preferred_over_slower():
    # white wins in 1 (a8 -> a9); the score is the fastest possible mate.
    s = mk(white="a8", black="e5", walls_left=(10, 0))
    r = Searcher(0).search(s, max_depth=5)
    assert r.move == PawnMove(0, 8) and r.score == WIN - 1


# ---------- alpha-beta == plain negamax ----------


def plain_negamax(sr: Searcher, state, depth: int, ply: int) -> int:
    """Reference: same move set and terminal rules, no pruning at all."""
    if winner(state) is not None:
        return -(WIN - ply)
    pawns = pawn_moves(state)
    if any(m.row == goal_row(state.turn) for m in pawns):
        return WIN - ply - 1
    if depth <= 0:
        return sr.evaluate(state)
    children = sr.ordered_moves(state, pawns)
    if not children:
        return sr.evaluate(state)
    return max(-plain_negamax(sr, c, depth - 1, ply + 1) for _, c in children)


def reference_root(sr: Searcher, state, depth: int) -> int:
    pawns = pawn_moves(state)
    if any(m.row == goal_row(state.turn) for m in pawns):
        return WIN - 1
    root = sr.ordered_moves(state, pawns)
    if not root:
        root = [(m, apply_move_unchecked(state, m)) for m in legal_moves(state)]
    return max(-plain_negamax(sr, c, depth - 1, 1) for _, c in root)


def check_matches_reference(state, depth, seed=0):
    r = Searcher(seed).search(state, max_depth=depth)
    ref = reference_root(Searcher(seed), state, r.depth)
    assert r.score == ref, (state, r)
    # the chosen move must be one of the best ones: its own reference score equals the best
    child = apply_move_unchecked(state, r.move)
    assert -plain_negamax(Searcher(seed), child, r.depth - 1, 1) == ref
    assert is_legal_move(state, r.move)


@pytest.mark.parametrize("depth", [1, 2])
def test_alpha_beta_equals_plain_negamax_shallow(depth):
    positions = sample_positions(range(6), every=4, max_plies=60)
    assert len(positions) > 30
    for s in positions:
        check_matches_reference(s, depth)


@pytest.mark.slow
def test_alpha_beta_equals_plain_negamax_depth3():
    positions = sample_positions(range(10, 14), every=7, max_plies=60)
    assert len(positions) > 10
    for s in positions:
        check_matches_reference(s, 3, seed=2)


def test_alpha_beta_equals_plain_negamax_with_few_walls_and_low_wall_limit():
    rng = random.Random(4)
    for s in sample_positions(range(20, 24), every=5, max_plies=70):
        wl = (rng.randint(0, 2), rng.randint(0, 2))
        s2 = s.__class__(
            pawns=s.pawns, h_walls=s.h_walls, v_walls=s.v_walls, walls_left=wl, turn=s.turn
        )
        r = Searcher(0, wall_limit=3).search(s2, max_depth=2)
        assert r.score == reference_root(Searcher(0, wall_limit=3), s2, r.depth)


# ---------- legality and time limits ----------


def test_always_legal_move_on_many_random_positions_with_all_limit_kinds():
    positions = sample_positions(range(30, 36), every=3, max_plies=90, wall_bias=0.8)
    assert len(positions) > 60
    depth_bot = SearchBot(1, depth=2)
    time_bot = SearchBot(2, move_time=0.02)
    for s in positions:
        assert is_legal_move(s, depth_bot.choose_move(s))
        assert is_legal_move(s, time_bot.choose_move(s))


def test_search_does_not_change_the_state_and_is_repeatable_after_reuse():
    s = mk(white="e4", black="e6", h=[(2, 5)], v=[(6, 6)])
    sr = Searcher(0)
    a = sr.search(s, max_depth=3)
    b = sr.search(s, max_depth=3)  # RNG advanced: move may differ among ties, score may not
    assert a.score == b.score
    assert s == mk(white="e4", black="e6", h=[(2, 5)], v=[(6, 6)])


@pytest.mark.parametrize("limit", [0.05, 0.15, 0.4])
def test_time_limit_is_hard_enough(limit):
    positions = sample_positions([40, 41], every=9, max_plies=80)
    for s in positions:
        t0 = time.perf_counter()
        r = Searcher(0).search(s, time_limit=limit)
        spent = time.perf_counter() - t0
        assert is_legal_move(s, r.move)
        assert r.depth >= 1
        assert spent < limit + 0.3, (limit, spent)


def test_tiny_time_limit_still_gives_a_legal_move():
    for s in sample_positions([50, 51], every=6, max_plies=80):
        r = Searcher(0).search(s, time_limit=1e-6)
        assert is_legal_move(s, r.move)
        assert r.depth >= 1


def test_deeper_time_gives_at_least_the_depth_of_shorter_time():
    s = mk(white="e4", black="e6", h=[(2, 5), (5, 2)], v=[(6, 6)])
    d_short = Searcher(0).search(s, time_limit=0.05).depth
    d_long = Searcher(0).search(s, time_limit=0.6).depth
    assert d_long >= d_short >= 1


def test_search_argument_validation():
    s = mk()
    with pytest.raises(ValueError):
        Searcher(0).search(s)  # neither depth nor time


# ---------- regression: seed 1 losses of v1 against greedy (report 0021) ----------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known weakness (0021): horizon effect, v1 spends all walls then shuffles; "
        "games 28 and 58 of `--a v1 --b greedy --seed 1` are lost by v1 as white. "
        "ai-engineer: repetition/non-progress handling (phase 6)"
    ),
)
@pytest.mark.parametrize("index", [28, 58])
def test_seed1_regression_games_are_won_by_v1(index):
    s = game_seed(1, index)
    res = play_game(
        make_bot("v1", 2 * s), make_bot("greedy", 2 * s + 1), WHITE, index=index, seed=s
    )
    assert res.winner == WHITE, " ".join(res.moves)


def test_seed1_regression_games_finish_without_errors_or_move_limit():
    # Whatever the outcome, the game must end by a win, not by the move limit.
    for index in (28, 58):
        s = game_seed(1, index)
        res = play_game(
            make_bot("v1", 2 * s), make_bot("greedy", 2 * s + 1), WHITE, index=index, seed=s
        )
        assert res.winner is not None
        assert res.plies < DEFAULT_MAX_MOVES


# ---------- loops: there are no draws in Quoridor ----------


def assert_no_move_limit_games(m):
    stuck = [(g.index, g.a_color, " ".join(g.moves[-12:])) for g in m.games if g.winner is None]
    assert not stuck, f"games hit the move limit (looping bot): {stuck}"


@pytest.mark.slow
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_v1_vs_greedy_never_hits_the_move_limit(seed):
    m = run_match(30, "v1", "greedy", seed=seed)
    assert_no_move_limit_games(m)
    assert m.total.draws == 0
    assert m.total.wins >= 27  # known: ~98% (0021), not a strength test but a sanity floor


@pytest.mark.slow
def test_v1_vs_random_never_hits_the_move_limit():
    m = run_match(20, "v1", "random", seed=0)
    assert_no_move_limit_games(m)
    assert m.total.wins == 20


@pytest.mark.slow
def test_v1_vs_v1_never_hits_the_move_limit():
    m = run_match(20, "v1", "v1", seed=1)
    assert_no_move_limit_games(m)


def test_greedy_is_not_looping_either_on_a_fast_game():
    res = play_game(GreedyBot(0), GreedyBot(1), WHITE)
    assert res.winner is not None
