"""Phase 9, step 6: a timed-out iteration must not replace the completed one by a move that
was searched before the previous best.

Found with numba (report 0077): v4 at 3 s on game06 after 8. ... (analyzer hint f6)
answered h6 -615 (partial depth 10) instead of f6 -185 (depth 9). Root moves whose
fail-low bound equals the best score are ordered by the seeded tie-break, so the next
iteration can start with such a move instead of the previous best. The partial result was
"the best move so far of the interrupted iteration", assumed to have beaten the previous
best: here it had not been compared with it at all. Now the partial result counts only
once the previous best has been searched again at the new depth. Fixed-depth searches
never time out, so they are unchanged.
"""

from __future__ import annotations

import pytest

from quoridor.bots import V4_SETTINGS
from quoridor.rules import apply_move_unchecked
from quoridor.search import Searcher, SearchTimeout
from tests.search.test_search_speed import positions

SAMPLE = dict(seeds=[61, 62, 63, 64], every=3, max_plies=60, wall_bias=0.6)


def _root_order(s, kw, it):
    """Root children of iteration `it` in search order (fixed depth `it`)."""
    order = []
    orig = Searcher._negamax
    sr = Searcher(0, **kw)

    def spy(state, d, alpha, beta, ply, key=0):
        if ply == 1 and d + 1 == it:
            order.append(state)
        return orig(sr, state, d, alpha, beta, ply, key)

    sr._negamax = spy  # type: ignore[method-assign]
    sr.search(s, max_depth=it)
    return order


def _cases(limit=3):
    """(position, settings, iteration, completed result of iteration - 1) where the
    iteration does not start with the previous iteration's best move."""
    out = []
    p = SAMPLE
    for s in positions(p["seeds"], every=p["every"], max_plies=p["max_plies"],
                       wall_bias=p["wall_bias"]):  # fmt: skip
        for kw in ({}, V4_SETTINGS):
            for it in (3, 4):
                prev = Searcher(0, **kw).search(s, max_depth=it - 1)
                order = _root_order(s, kw, it)
                if order and order[0] != apply_move_unchecked(s, prev.move):
                    out.append((s, kw, it, prev))
                    break
        if len(out) >= limit:
            break
    return out


def _search_with_timeout(monkeypatch, s, kw, it, after=None, after_child=None):
    """search(time_limit) that times out at a root call of iteration `it`: the call after
    `after` root calls, or the first call after the search of `after_child` returned
    (deterministic, no clock)."""
    orig = Searcher._negamax
    calls = {"n": 0, "seen": False}

    def fake(self, state, d, alpha, beta, ply, key=0):
        if ply == 1 and d + 1 == it:
            calls["n"] += 1
            if after is not None and calls["n"] > after:
                raise SearchTimeout
            if calls["seen"] and state != after_child:  # (not its PVS re-search)
                raise SearchTimeout
            v = orig(self, state, d, alpha, beta, ply, key)
            calls["seen"] = state == after_child
            return v
        return orig(self, state, d, alpha, beta, ply, key)

    monkeypatch.setattr(Searcher, "_negamax", fake)
    try:
        return Searcher(0, **kw).search(s, time_limit=1e6)
    finally:
        monkeypatch.setattr(Searcher, "_negamax", orig)


@pytest.mark.slow
def test_timeout_before_the_previous_best_keeps_the_completed_iteration(monkeypatch):
    cases = _cases()
    assert len(cases) >= 3
    for s, kw, it, prev in cases:
        # the first root move of iteration `it` (not the previous best) is fully searched
        r = _search_with_timeout(monkeypatch, s, kw, it, after=1)
        assert (r.move, r.score, r.depth, r.partial) == (prev.move, prev.score, it - 1, False)


@pytest.mark.slow
def test_timeout_after_the_previous_best_uses_the_partial_iteration(monkeypatch):
    """Once the previous best is searched again at the new depth, the best move so far
    of the interrupted iteration is used, as before (an exact score, not above the full
    iteration's)."""
    for s, kw, it, prev in _cases():
        full = Searcher(0, **kw).search(s, max_depth=it)
        child = apply_move_unchecked(s, prev.move)
        r = _search_with_timeout(monkeypatch, s, kw, it, after_child=child)
        if r.depth == it:  # the previous best was the last root move: nothing to cut
            continue
        assert r.depth == it - 1 and r.partial
        assert r.score <= full.score
