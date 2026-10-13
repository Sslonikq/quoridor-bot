"""Base-level tests for eval / search / timeman / SearchBot (phase 4)."""

import pytest

from quoridor.bots import BOTS, SearchBot, make_bot
from quoridor.eval import RACE, STEP, WALL, WIN, evaluate, evaluate_dists
from quoridor.paths import shortest_path_length
from quoridor.rules import (
    apply_move,
    is_legal_move,
    is_terminal,
    is_wall_legal,
    pawn_moves,
)
from quoridor.search import Searcher, is_win_score, opponent_winning_moves
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from quoridor.timeman import GAME_TIME, MAX_TIME, RESERVE, move_budget
from tests.helpers import mk
from tests.playouts import random_game

# ---------- eval ----------


def test_eval_initial_is_zero_for_both_sides():
    s = GameState.initial()
    assert evaluate(s) == 0
    assert evaluate(mk(turn=BLACK)) == 0


def test_eval_is_from_side_to_move():
    s = mk(white="e5", black="e9")  # white 4 steps, black 8
    assert evaluate(s) == 4 * STEP
    assert evaluate(mk(white="e5", black="e9", turn=BLACK)) == -4 * STEP


def test_eval_walls_and_races():
    assert evaluate_dists(5, 5, 3, 1) == 2 * WALL  # both have walls: no race bonus
    assert evaluate_dists(5, 5, 3, 0) == RACE + 3 * WALL  # tie, opponent out of walls
    assert evaluate_dists(5, 4, 0, 3) == -STEP - RACE - 3 * WALL
    assert evaluate_dists(4, 4, 0, 0) == RACE  # side to move wins an equal pure race


# ---------- search: wins and losses ----------


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_takes_win_in_one(depth):
    s = mk(white="a8", black="e9")
    r = Searcher(0).search(s, max_depth=depth)
    assert r.move == PawnMove(0, 8)
    assert is_win_score(r.score) and r.score > 0
    s = mk(white="e1", black="a2", turn=BLACK)
    assert Searcher(0).search(s, max_depth=depth).move == PawnMove(0, 0)


def test_win_by_jump_in_one():
    # white e7, black e8 in front: the straight jump lands on e9.
    s = mk(white="e7", black="e8")
    assert Searcher(0).search(s, max_depth=2).move == PawnMove(4, 8)


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_blocks_immediate_loss(depth):
    # black on e2 wins next move unless white blocks; white is far from its goal.
    s = mk(white="a3", black="e2", turn=WHITE)
    m = Searcher(0).search(s, max_depth=depth).move
    assert isinstance(m, WallMove)
    after = apply_move(s, m)
    assert all(p.row != 0 for p in pawn_moves(after))


def test_without_walls_loss_is_seen():
    # black on e2, white has no walls and needs 4 steps: lost, the score says so.
    s = mk(white="e5", black="e2", walls_left=(0, 10))
    r = Searcher(0).search(s, max_depth=2)
    assert r.score <= -(WIN - 10)
    assert is_legal_move(s, r.move)


def test_terminal_position_raises():
    s = mk(white="e9", black="a5", turn=BLACK)
    with pytest.raises(ValueError):
        Searcher(0).search(s, max_depth=2)


# ---------- candidate walls ----------


def test_candidate_walls_are_legal_and_gain():
    for seed in range(3):
        for i, s in enumerate(random_game(seed, max_plies=40)):
            if is_terminal(s) or i % 6:
                continue
            me = s.turn
            sr = Searcher(0, wall_limit=50)
            d_me, d_op = shortest_path_length(s, me), shortest_path_length(s, 1 - me)
            for gain, w, child in sr.candidate_walls(s):
                assert is_wall_legal(s, w)
                assert child == apply_move(s, w)
                g = (shortest_path_length(child, 1 - me) - d_op) - (
                    shortest_path_length(child, me) - d_me
                )
                # under a threat of a win in 1 saving walls are kept whatever their gain
                assert g == gain
                assert gain > 0 or opponent_winning_moves(s)


def test_wall_limit_respected():
    s = mk(white="e5", black="e6", turn=WHITE)
    assert len(Searcher(0, wall_limit=2).candidate_walls(s)) <= 2


# ---------- bot ----------


def test_bot_moves_always_legal_on_random_positions():
    bot = SearchBot(3, depth=2)
    checked = 0
    for seed in range(3):
        for i, s in enumerate(random_game(seed, max_plies=60)):
            if is_terminal(s) or i % 7:
                continue
            assert is_legal_move(s, bot.choose_move(s))
            checked += 1
    assert checked > 10


def test_fixed_depth_is_deterministic_per_seed():
    def play(seed):
        bots = (SearchBot(seed, depth=2), SearchBot(seed + 1, depth=2))
        s, log = GameState.initial(), []
        while not is_terminal(s) and len(log) < 40:
            m = bots[s.turn].choose_move(s)
            log.append(m)
            s = apply_move(s, m)
        return log

    assert play(5) == play(5)


def test_time_limit_is_respected():
    s = mk(white="e4", black="e6", h=[(2, 5), (5, 2)], v=[(6, 6)])
    bot = SearchBot(0, move_time=0.3)
    m = bot.choose_move(s)
    assert is_legal_move(s, m)
    assert bot.last is not None and bot.last.depth >= 1
    assert bot.last.elapsed < 0.3 + 0.5  # depth 1 + a clock check interval of slack


@pytest.mark.parametrize("game_time", [2.0, 4.0])
def test_bot_on_game_clock_stays_within_it(game_time):
    # Review 0023, minor 3: SearchBot(game_time=...) keeps its own clock via timeman.
    from quoridor.arena import play_game
    from quoridor.bots import GreedyBot

    bot = SearchBot(0, game_time=game_time)
    res = play_game(bot, GreedyBot(1), WHITE)  # checked apply_move: illegal move raises
    assert res.winner is not None
    assert bot.moves == res.moves_a > 0
    assert bot.used < game_time
    assert abs(bot.used - res.time_a) < 0.05
    assert bot.last is not None and bot.last.depth >= 1


def test_fixed_depth_result_is_never_partial():
    s = mk(white="e4", black="e6", h=[(2, 5), (5, 2)], v=[(6, 6)])
    r = Searcher(0).search(s, max_depth=2)
    assert r.depth == 2 and not r.partial


def test_bot_needs_a_limit():
    with pytest.raises(ValueError):
        SearchBot(0)


def test_registry_has_v1():
    assert {"v1", "v1-clock"} <= set(BOTS)
    bot = make_bot("v1", 0)
    s = mk(white="a8", black="e9")
    assert bot.choose_move(s) == PawnMove(0, 8)


def test_v1_beats_greedy_smoke():
    from quoridor.arena import run_match

    m = run_match(2, "v1", "greedy", seed=0)
    assert m.total.wins == 2


# ---------- timeman ----------


def test_budget_bounds():
    for played in range(0, 80, 3):
        for remaining in (600.0, 300.0, 100.0, 45.0, 31.0, 10.0, 0.5):
            b = move_budget(remaining, played)
            assert 0 <= b <= MAX_TIME
            assert b < remaining
            if remaining > RESERVE:
                assert b <= (remaining - RESERVE) / 3 + 1e-9


def test_budget_shape():
    assert move_budget(GAME_TIME, 10) > move_budget(GAME_TIME, 0)  # middlegame > opening
    assert move_budget(100.0, 10) < move_budget(GAME_TIME, 10)  # less time, less spent
    assert move_budget(20.0, 10) <= 0.05  # inside the reserve: almost nothing
    assert move_budget(0.0, 10) == 0.0


def test_full_game_fits_the_clock():
    # Spending exactly the budget every move never exhausts a 10-minute clock.
    remaining = GAME_TIME
    for played in range(150):
        remaining -= move_budget(remaining, played)
    assert remaining > RESERVE * 0.5
