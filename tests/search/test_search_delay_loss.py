"""Phase 8, step 1 (review 0057, minor 3 and nit): _delay_loss stays inside the move's
budget (skipped after the deadline, node cap at a fixed depth) and every candidate that
is not worse than the searched loss gets its exact score."""

import pytest

from quoridor import search
from quoridor.rules import legal_moves
from quoridor.search import Searcher, is_win_score
from tests.search.test_search_edge import sample_positions
from tests.search.test_search_root import LOST, V3


class Spy(Searcher):
    """Records whether _delay_loss ran and the node counts around it."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.delay_calls = 0
        self.main_nodes = None

    def _delay_loss_limited(self, *a, **kw):
        self.main_nodes = self.nodes
        return super()._delay_loss_limited(*a, **kw)

    def _delay_loss(self, *a, **kw):
        self.delay_calls += 1
        return super()._delay_loss(*a, **kw)


def test_delay_loss_is_skipped_after_the_deadline():
    # Depth 1 always completes (it already proves the loss); the deadline is gone then.
    sr = Spy(0, **V3)
    r = sr.search(LOST, time_limit=1e-9)
    assert sr.delay_calls == 0 and r.depth == 1
    assert is_win_score(r.score) and r.score < 0 and r.move in legal_moves(LOST)
    assert sr._node_limit == search._NO_NODE_LIMIT and sr._deadline is None


def test_delay_loss_runs_inside_the_time_budget():
    sr = Spy(0, **V3)
    r = sr.search(LOST, time_limit=5.0)
    assert sr.delay_calls == 1
    assert r.move == Searcher(0, **V3).search(LOST, max_depth=r.depth).move


def test_fixed_depth_delay_loss_has_a_node_cap(monkeypatch):
    monkeypatch.setattr(search, "_DELAY_MIN_NODES", 0)
    monkeypatch.setattr(search, "_DELAY_NODE_SHARE", 0.0)
    monkeypatch.setattr(search, "_CHECK_EVERY", 1)
    sr = Spy(0, **V3)
    r = sr.search(LOST, max_depth=3)
    assert sr.delay_calls == 1
    assert r.nodes <= sr.main_nodes + 1  # the first extra node hits the cap
    assert is_win_score(r.score) and r.score < 0 and r.move in legal_moves(LOST)
    # deterministic (nodes, not the clock) and nothing left switched on
    again = Spy(0, **V3).search(LOST, max_depth=3)
    assert (again.move, again.score, again.nodes) == (r.move, r.score, r.nodes)
    assert sr._node_limit == search._NO_NODE_LIMIT and sr._deadline is None


def test_default_node_cap_does_not_change_the_choice_on_samples(monkeypatch):
    """With the default cap the choice equals an uncapped _delay_loss (the cap is a guard:
    in real lost positions _delay_loss costs well below the main search)."""
    lost = []
    for s in sample_positions(range(40, 52), every=3, max_plies=120, wall_bias=0.5):
        r = Searcher(0, **V3).search(s, max_depth=3)
        if is_win_score(r.score) and r.score < 0:
            lost.append((s, r))
    assert len(lost) >= 3
    monkeypatch.setattr(search, "_DELAY_MIN_NODES", 10**12)
    for s, r in lost:
        u = Searcher(0, **V3).search(s, max_depth=3)
        assert (u.move, u.score) == (r.move, r.score)


def test_delay_loss_rescores_a_null_window_tie_with_the_full_window():
    """Null-window nit: the null window around best_score returns v == best_score as a
    fail-soft lower bound for a move Y that is in fact better. Before the fix Y kept
    v == best_score (and could lose the tie-break to the worse X). Pairs (X, Y) where
    this happens are found first; then _delay_loss over root [X, Y] (best = X, no
    root-move bounds: exact_root=False) must pick the true maximum with its exact score."""
    depth = 3
    pairs = 0
    for s in sample_positions(range(30, 40), every=5, max_plies=80):
        probe = Searcher(0, **V3)
        probe._reset(s)
        root = probe.ordered_moves(s)
        keys = probe._keys(s, root)
        exact = {
            r.move: r.score
            for r in Searcher(0, **V3)
            .search_root(s, max_depth=depth, moves=[m for m, _ in root])
            .scores
        }
        x = min(range(len(root)), key=lambda i: exact[root[i][0]])
        b = exact[root[x][0]]
        ys = [
            i
            for i, (m, c) in enumerate(root)
            if exact[m] > b and -probe._negamax(c, depth - 1, -b, -(b - 1), 1, keys[i]) == b
        ]
        probe._free()
        for y in ys[:2]:
            sub = [root[x], root[y]]
            sr = Searcher(0, **V3)
            sr._reset(s)
            sub_keys = sr._keys(s, sub)
            extra = [w for w, _ in sr._lengthening_walls(s) if w not in {root[x][0], root[y][0]}]
            ex = Searcher(0, **V3).search_root(
                s, max_depth=depth, moves=[root[x][0], root[y][0], *extra]
            )
            all_exact = {r.move: r.score for r in ex.scores}
            move, v = sr._delay_loss(
                s, sub, sub_keys, {0: 0, 1: 1}, depth, root[x][0], b, exact_root=False
            )
            sr._free()
            assert v == all_exact[move] == max(all_exact.values()), (s, root[y][0], move, v)
            pairs += 1
    assert pairs >= 3


@pytest.mark.parametrize("depth", [2, 3])
def test_delay_loss_scores_are_exact_for_candidates_not_worse(depth):
    """Null-window nit: a candidate with v >= best_score gets its exact score, so the
    choice is the true maximum of all candidates (here with an artificial best_score =
    the exact score of the worst root move, and no root-move bounds: exact_root=False)."""
    checked = 0
    for s in sample_positions(range(30, 36), every=5, max_plies=80):
        sr = Searcher(0, **V3)
        sr._reset(s)
        root = sr.ordered_moves(s)
        if len(root) < 3:
            continue
        keys = sr._keys(s, root)
        prio = {i: i for i in range(len(root))}
        extra = [w for w, _ in sr._lengthening_walls(s) if w not in {m for m, _ in root}]
        cands = [m for m, _ in root] + extra
        ex = Searcher(0, **V3).search_root(s, max_depth=depth, moves=cands)
        exact = {r.move: r.score for r in ex.scores}
        mid = min((m for m, _ in root), key=lambda m: exact[m])
        move, v = sr._delay_loss(s, root, keys, prio, depth, mid, exact[mid], exact_root=False)
        sr._free()
        assert v == exact[move] == max(exact.values()), (s, move)
        checked += 1
    assert checked >= 5
