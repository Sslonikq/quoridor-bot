"""Fixed-depth regression snapshot (phase 5).

Scores and moves of Searcher(0).search(max_depth=d) on fixed positions. The values were
recorded after the phase-5 speed-ups; report 0029 verified (A/B on 59 positions, d2-d6)
that they equal the pre-optimization search, so any change here means the search now
computes something different. If a deliberate change of eval/move generation alters them,
regenerate the table and say so in the report.

Complements test_search_speed.py (which checks equivalence to reference implementations):
this one pins concrete numbers, so a change that moves both sides together is caught too.
"""

import pytest

from quoridor.notation import format_move
from quoridor.search import Searcher
from tests.search.test_search_speed import positions

PICK = [1, 2, 3, 5, 6, 7, 8]  # indices into the |score| < 1000 positions (no duplicates)

# (score, move) for depth 2, 3, 4, 5
GOLDEN = [
    [(-25, "d2"), (75, "d2"), (-25, "d2"), (75, "d2")],
    [(-225, "f7h"), (50, "d1"), (-50, "d1"), (50, "d1")],
    [(-250, "e7h"), (-125, "e2"), (-300, "e2"), (-200, "e2")],
    [(25, "g2"), (175, "d9h"), (-25, "g2"), (75, "g2")],
    [(100, "c9h"), (275, "c9v"), (25, "c9v"), (400, "c9v")],
    [(200, "e3"), (325, "e3"), (200, "e3"), (450, "a5h")],
    [(100, "b9v"), (475, "a5h"), (375, "a5h"), (600, "a5h")],
]
DEPTHS = (2, 3, 4, 5)


@pytest.fixture(scope="module")
def quiet_positions():
    ps = positions([200, 201, 202], every=8, max_plies=60, wall_bias=0.5)
    quiet = [s for s in ps if abs(Searcher(0).search(s, max_depth=4).score) < 1000]
    return [quiet[i] for i in PICK]


@pytest.mark.parametrize("k", range(len(GOLDEN)))
def test_fixed_depth_scores_and_moves_are_pinned(quiet_positions, k):
    s = quiet_positions[k]
    for d, (score, move) in zip(DEPTHS, GOLDEN[k], strict=True):
        r = Searcher(0).search(s, max_depth=d)
        assert (r.score, format_move(r.move)) == (score, move), (k, d)
        assert r.depth == d


def test_fixed_depth_is_repeatable_and_cache_free(quiet_positions):
    # A reused Searcher (caches reset per search) gives the same score as a fresh one.
    # Moves may differ among equal scores: the seeded tie-break RNG advances between
    # searches of one Searcher, so only fresh Searcher(0) instances are move-comparable.
    sr = Searcher(0)
    for s in quiet_positions[:4]:
        first = sr.search(s, max_depth=4)
        again = sr.search(s, max_depth=4)
        fresh_a = Searcher(0).search(s, max_depth=4)
        fresh_b = Searcher(0).search(s, max_depth=4)
        assert first.score == again.score == fresh_a.score
        assert (fresh_a.score, fresh_a.move) == (fresh_b.score, fresh_b.move)
