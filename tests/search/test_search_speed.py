"""Phase 5 speed-ups must not change what the search computes.

* Searcher.dist / candidate_walls equal the phase-4 reference built on quoridor.paths and
  rules.is_wall_legal (bitboard BFS, child masks, wall_fits instead of is_wall_legal).
* Killer ordering only reorders: alpha-beta still equals plain negamax at depths where
  killers are actually used (>= 3), with a small wall_limit to keep the reference cheap.
"""

import pytest

from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import apply_move_unchecked, is_terminal, is_wall_legal
from quoridor.search import Searcher, edge_walls, opponent_winning_moves
from quoridor.state import WALL_GRID, WallMove
from tests.playouts import random_game
from tests.search.test_search_edge import plain_negamax, reference_root


def positions(seeds, every=3, max_plies=90, wall_bias=0.7):
    out = []
    for seed in seeds:
        for i, s in enumerate(random_game(seed, max_plies=max_plies, wall_bias=wall_bias)):
            if not is_terminal(s) and i % every == 0:
                out.append(s)
    return out


def reference_candidate_walls(state, wall_limit):
    """Phase-4 candidate_walls (before the bitboard rewrite), threat branch excluded."""
    me = state.turn
    if state.walls_left[me] <= 0:
        return []
    path = shortest_path(state, 1 - me)
    d_me = shortest_path_length(state, me)
    d_op = len(path) - 1
    seen: set[WallMove] = set()
    scored = []
    for a, b in zip(path, path[1:], strict=False):
        for w in edge_walls(a, b):
            if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                continue
            seen.add(w)
            if (w.x, w.y) in state.h_walls or (w.x, w.y) in state.v_walls:
                continue
            child = apply_move_unchecked(state, w)
            op_after = shortest_path_length(child, 1 - me)
            if op_after is None or op_after <= d_op:
                continue
            me_after = shortest_path_length(child, me)
            if me_after is None:
                continue
            gain = (op_after - d_op) - (me_after - d_me)
            if gain > 0:
                scored.append((gain, w, child))
    scored.sort(key=lambda t: -t[0])
    return [t for t in scored if is_wall_legal(state, t[1])][:wall_limit]


def test_dist_equals_reference_bfs():
    sr = Searcher(0)
    for s in positions(range(4)):
        for p in (0, 1):
            assert sr.dist(s, p) == shortest_path_length(s, p)


@pytest.mark.parametrize("wall_limit", [3, 10])
def test_candidate_walls_equal_phase4_reference(wall_limit):
    ps = [s for s in positions(range(6), wall_bias=0.8) if not opponent_winning_moves(s)]
    assert len(ps) > 60
    for s in ps:
        got = Searcher(0, wall_limit=wall_limit).candidate_walls(s)
        assert got == reference_candidate_walls(s, wall_limit), s


@pytest.mark.parametrize("depth", [3, 4])
def test_killer_ordering_keeps_alpha_beta_exact(depth):
    ps = positions(range(40, 43), every=9, max_plies=60)
    assert len(ps) >= 8
    for s in ps:
        r = Searcher(0, wall_limit=2).search(s, max_depth=depth)
        ref = reference_root(Searcher(0, wall_limit=2), s, r.depth)
        assert r.score == ref, s
        child = apply_move_unchecked(s, r.move)
        assert -plain_negamax(Searcher(0, wall_limit=2), child, r.depth - 1, 1) == ref
