"""Phase 6, v2: transposition table (Zobrist keys) must not change what the search computes.

* child_key (incremental) equals zobrist (from scratch) for every legal move;
* A/B: at a fixed depth Searcher(tt_bits > 0) returns the same score as Searcher() on
  hundreds of positions (random games with few/many walls, win-in-1 threats, mates).
  The move may differ only between equally good moves: then the TT move scores exactly
  the root value in a plain (no TT, full window) search;
* a tiny table (constant index collisions and replacements) gives the same scores;
* the table belongs to the Searcher: allocated per search, dropped on return.
"""

import functools

import pytest

from quoridor.bots import BOTS, V2_DEPTH, make_bot
from quoridor.rules import (
    apply_move_unchecked,
    is_legal_move,
    is_terminal,
    is_wall_legal,
    legal_moves,
    pawn_moves,
    wall_fits,
)
from quoridor.search import (
    INF,
    V2_TT_BITS,
    Searcher,
    _from_tt,
    _to_tt,
    child_key,
    is_win_score,
    zobrist,
)
from quoridor.state import WALL_GRID, GameState, H, V, WallMove, goal_row
from tests.playouts import random_game
from tests.search.test_search_defence import fast_threat_positions


def game_positions(seeds, every, wall_bias, max_plies=80):
    out = []
    for seed in seeds:
        for i, s in enumerate(random_game(seed, max_plies=max_plies, wall_bias=wall_bias)):
            if not is_terminal(s) and i % every == 0:
                out.append(s)
    return out


@functools.cache
def ab_positions() -> tuple[GameState, ...]:
    ps = (
        game_positions(range(100, 106), 4, 0.3)
        + game_positions(range(110, 116), 4, 0.6)
        + game_positions(range(120, 126), 4, 0.9)
        + list(fast_threat_positions()[::5])
    )
    return tuple(ps)


def plain_value(state, move, depth) -> int:
    """Root-move value without TT, full window (the reference for 'equally good')."""
    sr = Searcher(0)
    return -sr._negamax(apply_move_unchecked(state, move), depth - 1, -INF, INF, 1)


def check_same(states, depths, tt_bits):
    moves_differ = 0
    mates = 0
    for s in states:
        for d in depths:
            ref = Searcher(0).search(s, max_depth=d)
            got = Searcher(0, tt_bits=tt_bits).search(s, max_depth=d)
            assert got.score == ref.score, (s, d)
            assert got.depth == ref.depth, (s, d)
            mates += is_win_score(ref.score)
            if got.move != ref.move:
                moves_differ += 1
                assert is_legal_move(s, got.move)
                assert plain_value(s, got.move, ref.depth) == ref.score, (s, d)
    return moves_differ, mates


# ---------- Zobrist ----------


def test_child_key_equals_full_zobrist_for_every_legal_move():
    n = 0
    for s in game_positions(range(130, 134), 3, 0.6):
        k = zobrist(s)
        for m in legal_moves(s):
            assert child_key(k, s, m) == zobrist(apply_move_unchecked(s, m))
            n += 1
    assert n > 5000


def test_zobrist_tells_apart_turn_and_wall_stock():
    s = GameState.initial()
    keys = {
        zobrist(s),
        zobrist(GameState(s.pawns, s.h_walls, s.v_walls, s.walls_left, 1)),
        zobrist(GameState(s.pawns, s.h_walls, s.v_walls, (9, 10), 0)),
        zobrist(GameState(s.pawns, s.h_walls, s.v_walls, (10, 9), 0)),
        zobrist(GameState(s.pawns, frozenset({(0, 0)}), s.v_walls, s.walls_left, 0)),
        zobrist(GameState(s.pawns, s.h_walls, frozenset({(0, 0)}), s.walls_left, 0)),
    }
    assert len(keys) == 6


@pytest.mark.parametrize("v", [0, 175, -5025, 999_990, -999_985])
def test_mate_scores_are_stored_relative_to_the_node(v):
    for ply in (1, 4, 9):
        assert _from_tt(_to_tt(v, ply), ply) == v
    # a win in 2 plies from a node is the same entry whatever the node's ply
    assert _to_tt(1_000_000 - 3 - 2, 3) == _to_tt(1_000_000 - 5 - 2, 5)


# ---------- A/B against the search without TT ----------


def test_ab_position_set_is_large_and_varied():
    ps = ab_positions()
    assert len(ps) >= 300
    assert sum(1 for s in ps if len(s.h_walls) + len(s.v_walls) >= 10) >= 50


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_tt_scores_equal_no_tt_scores(depth):
    _, mates = check_same(ab_positions(), [depth], V2_TT_BITS)
    if depth >= 2:
        assert mates > 0  # mate scores (stored relative to the node) are exercised


def test_tt_scores_equal_no_tt_scores_depth4():
    check_same(ab_positions()[::3], [4], V2_TT_BITS)


def test_tiny_table_with_constant_replacement_gives_the_same_scores():
    check_same(ab_positions()[::7], [2, 3, 4], 3)


@pytest.mark.slow
@pytest.mark.parametrize("depth", [4, 5])
def test_tt_scores_equal_no_tt_scores_deep(depth):
    check_same(ab_positions()[:: 1 if depth == 4 else 2], [depth], V2_TT_BITS)


def test_tt_saves_work_at_depth_5():
    ps = ab_positions()[::10]
    plain = sum(Searcher(0).search(s, max_depth=5).nodes for s in ps)
    tt = sum(Searcher(0, tt_bits=V2_TT_BITS).search(s, max_depth=5).nodes for s in ps)
    assert tt < plain


# ---------- key collision: a foreign TT move ----------


def _cutting_wall_position():
    """A position (walls left, no win in 1) and a wall that fits but cuts someone off."""
    for seed in range(200, 260):
        for s in random_game(seed, max_plies=80, wall_bias=0.9):
            if is_terminal(s) or s.walls_left[s.turn] == 0:
                continue
            if any(m.row == goal_row(s.turn) for m in pawn_moves(s)):
                continue
            for x in range(WALL_GRID):
                for y in range(WALL_GRID):
                    for o in (H, V):
                        w = WallMove(x, y, o)
                        if wall_fits(s, w) and not is_wall_legal(s, w):
                            return s, w
    raise AssertionError("no position with a fitting but cutting wall")


def test_tt_wall_from_a_key_collision_is_checked_for_paths():
    """Review 0040, nit 6: a TT wall that fits but cuts a player off is never searched."""
    s, bad = _cutting_wall_position()
    sr = Searcher(0, tt_bits=V2_TT_BITS)
    key = zobrist(s)
    # A colliding entry: same full key, other depth (no value cutoff), foreign best move.
    sr._tt = {key & sr._tt_mask: (key, 99, 0, 0, bad)}
    seen = []
    orig = sr._negamax

    def spy(state, depth, alpha, beta, ply, k=0):
        seen.append(state)
        return orig(state, depth, alpha, beta, ply, k)

    sr._negamax = spy
    sr._negamax(s, 2, -INF, INF, 0, key)
    assert len(seen) > 1
    o = bad.orient
    assert all((bad.x, bad.y) not in (c.h_walls if o == H else c.v_walls) for c in seen)


# ---------- ownership ----------


def test_table_is_per_search_and_dropped_on_return():
    s = ab_positions()[5]
    sr = Searcher(0, tt_bits=10)
    a = sr.search(s, max_depth=4)
    assert sr._tt is None
    b = sr.search(s, max_depth=4)
    assert sr._tt is None
    assert a.score == b.score
    # Nothing is carried over: the next search costs exactly as much as on a fresh searcher
    # with the same tie-break RNG state (node counts depend on the root shuffle).
    sr2 = Searcher(0, tt_bits=10)
    sr2._rng.setstate(sr._rng.getstate())
    assert sr.search(s, max_depth=4).nodes == sr2.search(s, max_depth=4).nodes


def test_no_table_without_tt_bits():
    sr = Searcher(0)
    sr.search(ab_positions()[5], max_depth=3)
    assert sr.tt_bits == 0 and sr._tt is None


def test_huge_wall_stock_disables_the_table_instead_of_failing():
    s = GameState.initial()
    s = GameState(s.pawns, s.h_walls, s.v_walls, (500, 500), 0)
    r = Searcher(0, tt_bits=10).search(s, max_depth=2)
    assert r.score == Searcher(0).search(s, max_depth=2).score


# ---------- registry ----------


def test_registry_has_v2_bots():
    assert {"v2", "v2-clock", "v1-0.2s", "v2-0.2s"} <= set(BOTS)
    bot = make_bot("v2", 0)
    assert bot.depth == V2_DEPTH
    assert bot._searcher.tt_bits == V2_TT_BITS
    assert make_bot("v2-clock", 0)._searcher.tt_bits == V2_TT_BITS
    assert make_bot("v1", 0)._searcher.tt_bits == 0


def test_v2_is_deterministic_at_fixed_depth():
    ps = ab_positions()[::40]
    a = [make_bot("v2", 7).choose_move(s) for s in ps]
    b = [make_bot("v2", 7).choose_move(s) for s in ps]
    assert a == b
