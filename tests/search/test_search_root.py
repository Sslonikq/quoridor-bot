"""Phase 8, ai-engineer: search_root / search_top, display_score, the best resistance in
proven-lost positions (report 0053)."""

from pathlib import Path

import pytest

from quoridor.analyzer.engine import replay
from quoridor.bots import V3_WALL_WEIGHT
from quoridor.notation import format_move, parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move_unchecked, legal_moves
from quoridor.search import (
    TEMPO,
    V2_TT_BITS,
    WIN,
    Searcher,
    display_score,
    is_win_score,
)
from quoridor.state import GameState, WallMove
from tests.search.test_search_edge import plain_negamax, sample_positions

V3 = {"tt_bits": V2_TT_BITS, "wall_weight": V3_WALL_WEIGHT}

GAME02 = [
    t
    for line in (Path(__file__).parents[1] / "fixtures/games/game02.txt")
    .read_text("utf-8")
    .splitlines()
    if not line.startswith("#")
    for t in line.split()
]


def replay_fixture(name: str, n: int) -> GameState:
    """The position after the first n moves of tests/fixtures/games/<name>."""
    text = (Path(__file__).parents[1] / "fixtures/games" / name).read_text("utf-8")
    moves = [t for line in text.splitlines() if not line.startswith("#") for t in line.split()]
    return replay(moves[:n])


def positions():
    return sample_positions(range(30, 34), every=6, max_plies=60)


# ---------- search_root ----------


@pytest.mark.parametrize("depth", [1, 2])
def test_search_root_scores_equal_plain_negamax_per_move(depth):
    for s in positions():
        sr = Searcher(0, **V3)
        res = sr.search_root(s, max_depth=depth)
        assert res.depth == depth and all(r.exact for r in res.scores)
        ref = Searcher(0, **V3)
        for r in res.scores:
            child = apply_move_unchecked(s, r.move)
            assert r.score == -plain_negamax(ref, child, depth - 1, 1), (s, r)
        scores = [r.score for r in res.scores]
        assert scores == sorted(scores, reverse=True)
        # the best root score is the value search() computes
        assert scores[0] == Searcher(0, **V3).search(s, max_depth=depth).score


def test_search_root_top_k_exact_for_the_best_k_and_bounds_for_the_rest():
    for s in positions():
        full = Searcher(0, **V3).search_root(s, max_depth=3)
        exact = {r.move: r.score for r in full.scores}
        part = Searcher(0, **V3).search_root(s, max_depth=3, top=2)
        assert {r.move for r in part.scores} == set(exact)
        ex = [r for r in part.scores if r.exact]
        assert len(ex) >= min(2, len(exact))
        for r in part.scores:
            if r.exact:
                assert r.score == exact[r.move]
            else:
                assert exact[r.move] <= r.score  # an upper bound
        best2 = sorted(exact.values(), reverse=True)[:2]
        assert sorted((r.score for r in ex), reverse=True)[:2] == best2


def test_search_root_given_move_outside_the_pruned_set_and_illegal_move():
    s = replay(["e2", "e8"])
    sr = Searcher(0, **V3)
    pruned = {m for m, _ in sr.ordered_moves(s)}
    wall = next(m for m in legal_moves(s) if isinstance(m, WallMove) and m not in pruned)
    res = Searcher(0, **V3).search_root(s, max_depth=3, moves=[wall])
    assert [r.move for r in res.scores] == [wall] and res.scores[0].exact
    with pytest.raises(ValueError):
        Searcher(0, **V3).search_root(s, max_depth=2, moves=[parse_move("e5")])
    with pytest.raises(ValueError):
        Searcher(0, **V3).search_root(s)  # no limits


def test_search_root_and_search_top_free_their_tables():
    s = replay(["e2", "e8"])
    sr = Searcher(0, **V3)
    sr.search_root(s, max_depth=3)
    assert sr._tt is None and sr._root_info is None and not sr._dist_cache
    sr.search_top(s, max_depth=3)
    assert sr._tt is None and sr._root_info is None and not sr._dist_cache


# ---------- search_top ----------


@pytest.mark.parametrize("depth", [2, 3])
def test_search_top_best_move_is_exactly_search(depth):
    for s in positions():
        a = Searcher(5, **V3).search(s, max_depth=depth)
        t = Searcher(5, **V3).search_top(s, max_depth=depth, top_n=3)
        assert (t.best.move, t.best.score, t.best.depth) == (a.move, a.score, a.depth)
        assert len(t.alts) <= 2 and all(r.move != a.move for r in t.alts)
        assert all(r.score <= t.best.display_score for r in t.alts)
        assert len({r.move for r in t.alts}) == len(t.alts)
        assert t.alt_depth <= t.best.depth


def test_search_top_with_a_time_limit_stays_inside_it():
    s = replay(GAME02[:16])
    t = Searcher(0, **V3).search_top(s, time_limit=1.0)
    assert t.elapsed < 1.3 and t.best.depth >= 4 and len(t.alts) == 2


# ---------- display score (odd/even depth swing) ----------


def test_display_score_removes_the_odd_even_swing():
    # game02, white's move 19: raw scores alternate +500 / +400 at odd / even depths
    s = replay(GAME02[:36])
    raw = [Searcher(0, **V3).search(s, max_depth=d).score for d in range(3, 9)]
    shown = [display_score(v, d) for d, v in zip(range(3, 9), raw, strict=True)]
    assert max(raw) - min(raw) == 2 * TEMPO
    assert max(shown) == min(shown)


def test_display_score_keeps_mates():
    assert display_score(WIN - 3, 4) == WIN - 3 and display_score(-(WIN - 7), 7) == -(WIN - 7)


# ---------- proven loss: the longest resistance ----------

# A random-game position (seed 39, ply 81): black to move, no walls left, white wins next
# move whatever black does. Before report 0053 the search played d9 (own path 12).
LOST = GameState(
    pawns=((4, 7), (3, 7)),
    h_walls=frozenset(
        {(0, 1), (3, 4), (3, 1), (5, 4), (6, 6), (0, 6), (4, 5), (1, 0), (6, 3), (1, 3)}
    ),
    v_walls=frozenset(
        {(0, 7), (6, 2), (0, 4), (7, 7), (6, 5), (5, 1), (4, 2), (1, 4), (7, 3), (0, 2)}
    ),
    walls_left=(0, 0),
    turn=1,
)


@pytest.mark.parametrize("kw", [{}, V3])
def test_lost_position_plays_the_longest_resistance(kw):
    r = Searcher(0, **kw).search(LOST, max_depth=3)
    assert is_win_score(r.score) and r.score < 0
    scores = Searcher(0, **kw).search_root(LOST, max_depth=3, moves=legal_moves(LOST))
    best = max(x.score for x in scores.scores)
    assert r.score == best

    def margin(m):
        c = apply_move_unchecked(LOST, m)
        return shortest_path_length(c, 0) - shortest_path_length(c, 1)

    tied = [x.move for x in scores.scores if x.score == best]
    assert margin(r.move) == max(margin(m) for m in tied)
    assert format_move(r.move) == "d7"


def test_lost_choice_with_walls_never_loses_faster():
    """Every proven-lost sample position: the move played loses no faster than any move of
    the pruned root set (checked by search_root at the same depth)."""
    seen = 0
    for s in sample_positions(range(40, 52), every=3, max_plies=120, wall_bias=0.5):
        r = Searcher(0, **V3).search(s, max_depth=3)
        if not (is_win_score(r.score) and r.score < 0):
            continue
        seen += 1
        root = Searcher(0, **V3).search_root(s, max_depth=3)
        assert r.score >= max(x.score for x in root.scores)
        own = Searcher(0, **V3).search_root(s, max_depth=3, moves=[r.move])
        assert own.scores[0].score == r.score
    assert seen >= 3
