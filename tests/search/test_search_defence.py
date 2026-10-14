"""Defence against a win in 1 (review 0023, major 1).

A wall that stops the opponent's immediate win must be searched even when its gain
(opponent path growth minus own path growth) is <= 0, and the search must not report
a false "proven" loss while such a wall exists.
"""

import functools
import random
from dataclasses import replace

import pytest

from quoridor.bots import SearchBot
from quoridor.eval import WIN
from quoridor.notation import parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move, apply_move_unchecked, is_terminal, legal_moves, pawn_moves
from quoridor.search import Searcher, is_win_score
from quoridor.state import BLACK, WallMove, goal_row
from tests.helpers import mk
from tests.playouts import random_game


def wins_now(state) -> bool:
    """The side to move can step (or jump) onto its goal row."""
    return any(m.row == goal_row(state.turn) for m in pawn_moves(state))


def threatened(state) -> bool:
    """The opponent of the side to move could win with its next pawn move."""
    return wins_now(replace(state, turn=1 - state.turn))


def saving_moves(state):
    return [m for m in legal_moves(state) if not wins_now(apply_move_unchecked(state, m))]


# ---------- the position from review 0023 ----------

REVIEW = dict(white="e8", black="e9", turn=BLACK, walls_left=(5, 5))
SAVES = {parse_move("d9h"), parse_move("e9h")}


def test_review_position_only_walls_with_zero_gain_save():
    s = mk(**REVIEW)
    assert threatened(s)
    assert set(saving_moves(s)) == SAVES
    for w in SAVES:  # both also lengthen black's own path: gain 0, so they were pruned
        child = apply_move(s, w)
        d_op = shortest_path_length(child, 0) - shortest_path_length(s, 0)
        d_me = shortest_path_length(child, 1) - shortest_path_length(s, 1)
        assert d_op - d_me <= 0


def test_review_position_candidate_walls_contain_the_saves():
    s = mk(**REVIEW)
    cands = {w for _, w, _ in Searcher(0).candidate_walls(s)}
    assert cands == SAVES


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_review_position_saving_wall_is_played(depth):
    s = mk(**REVIEW)
    r = Searcher(0).search(s, max_depth=depth)
    assert r.move in SAVES, r
    assert not wins_now(apply_move(s, r.move))
    assert not (is_win_score(r.score) and r.score < 0), r
    assert r.depth == depth  # no false mate score stopping the deepening


def test_review_position_saving_wall_with_time_limit_and_bot():
    s = mk(**REVIEW)
    assert Searcher(0).search(s, time_limit=0.2).move in SAVES
    assert SearchBot(0, depth=3).choose_move(s) in SAVES


def test_mirrored_review_position_for_white():
    # white e1 at its home row, black e2 threatens d1 / f1 by the diagonal: d2h / e2h save.
    s = mk(white="e1", black="e2", turn=0, walls_left=(5, 5))
    saves = set(saving_moves(s))
    assert saves == {parse_move("d2h"), parse_move("e2h")}
    for depth in (1, 2, 3):
        assert Searcher(0).search(s, max_depth=depth).move in saves


def test_jump_threat_from_two_rows_white_e7_black_e8():
    # Black to move; white (goal row 9) at e7 threatens to jump over e8 onto e9.
    # Walls on the edges e7-e8 (d8h, e8h) or e8-e9 (d9h, e9h) stop it.
    s = mk(white="e7", black="e8", turn=BLACK, walls_left=(5, 5))
    assert threatened(s)
    saves = set(saving_moves(s))
    walls = {m for m in saves if isinstance(m, WallMove)}
    assert walls == {parse_move(x) for x in ("d8h", "e8h", "d9h", "e9h")}
    cands = {w for _, w, _ in Searcher(0, wall_limit=1).candidate_walls(s)}
    assert cands == walls
    for depth in (1, 2, 3):
        r = Searcher(0).search(s, max_depth=depth)
        assert r.move in saves, r
        assert not (is_win_score(r.score) and r.score < 0), r


def test_threat_walls_are_legal_and_all_stop_the_win():
    s = mk(**REVIEW)
    for _, w, child in Searcher(0, wall_limit=1).candidate_walls(s):
        assert isinstance(w, WallMove)
        assert child == apply_move(s, w)
        assert not wins_now(child)


def test_no_walls_left_nothing_to_defend_with():
    s = mk(white="e8", black="e9", turn=BLACK, walls_left=(5, 0))
    assert Searcher(0).candidate_walls(s) == []
    r = Searcher(0).search(s, max_depth=3)
    assert is_win_score(r.score) and r.score < 0  # a real loss: no save exists
    assert saving_moves(s) == []


# ---------- regression: random positions with a threat of a win in 1 ----------


def threat_positions(n_seeds: int, rng_seed: int = 0, per_state: int = 6):
    """Positions where the opponent threatens a win in 1 and at least one saving move exists.

    The opponent is k in {1, 2} rows before its goal; with k = 2 our pawn stands between
    it and the goal row (a direct-jump threat). Walls come from random games.
    """
    rng = random.Random(rng_seed)
    out = []
    for seed in range(n_seeds):
        states = [s for s in random_game(seed, max_plies=60, wall_bias=0.5) if not is_terminal(s)]
        for s in states[::6]:
            for _ in range(per_state):
                me, op = s.turn, 1 - s.turn
                step = -1 if goal_row(op) == 8 else 1
                k = rng.choice((1, 2))
                oc = (rng.randrange(9), goal_row(op) + k * step)
                if k == 2:
                    mc = (oc[0], goal_row(op) + step)
                else:
                    mc = (rng.randrange(9), rng.randrange(9))
                if mc == oc or mc[1] == goal_row(me):
                    continue
                pawns = [None, None]
                pawns[me], pawns[op] = mc, oc
                t = replace(s, pawns=tuple(pawns), walls_left=(3, 3))
                if shortest_path_length(t, 0) is None or shortest_path_length(t, 1) is None:
                    continue
                if wins_now(t) or not threatened(t) or not saving_moves(t):
                    continue
                out.append(t)
    return out


@functools.cache
def fast_threat_positions():
    # 12 seeds: ~490 positions; v1 before the fix (0021) lost at once in 9 of them.
    return tuple(threat_positions(12))


def test_saving_walls_are_exactly_the_walls_that_stop_the_win():
    positions = fast_threat_positions()
    assert len(positions) > 400
    for t in positions:
        brute = {m for m in saving_moves(t) if isinstance(m, WallMove)}
        got = {w for _, w, _ in Searcher(0, wall_limit=1).candidate_walls(t)}
        assert got == brute, t


@pytest.mark.parametrize("depth", [1, 2])
def test_never_allows_a_win_in_one_when_a_save_exists(depth):
    positions = fast_threat_positions()
    for t in positions:
        r = Searcher(0).search(t, max_depth=depth)
        assert not wins_now(apply_move(t, r.move)), (t, r)
        assert not (is_win_score(r.score) and r.score <= -(WIN - 2)), (t, r)


@pytest.mark.slow
def test_never_allows_a_win_in_one_when_a_save_exists_depth3_more_positions():
    positions = threat_positions(30, rng_seed=1)
    assert len(positions) > 700
    for t in positions:
        r = Searcher(0).search(t, max_depth=3)
        assert not wins_now(apply_move(t, r.move)), (t, r)
        assert not (is_win_score(r.score) and r.score <= -(WIN - 2)), (t, r)
