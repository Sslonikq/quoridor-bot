"""Phase 6 (0032 minor 1): bounded memo caches of the search.

The caches are a pure memo, so a tiny cache_limit must give exactly the same results as
the default one; the caches never grow past the limit and are freed after search().
"""

import pytest

from quoridor.search import Searcher
from tests.search.test_search_speed import positions


class CheckedSearcher(Searcher):
    """Asserts the cache bounds on every lookup."""

    max_seen = 0

    def dist_m(self, m, cell, player):
        n = len(self._dist_cache)
        assert n <= self.cache_limit
        assert len(self._mask_cache) <= self._small_limit
        assert len(self._path_cache) <= self._small_limit
        self.max_seen = max(self.max_seen, n)
        return super().dist_m(m, cell, player)


@pytest.fixture(scope="module")
def sample():
    return positions(range(50, 54), every=6, max_plies=60, wall_bias=0.6)


@pytest.mark.parametrize("depth", [2, 3, 4])
def test_tiny_cache_gives_the_same_result(sample, depth):
    for s in sample:
        big = Searcher(0).search(s, max_depth=depth)
        small = CheckedSearcher(0, cache_limit=40)
        r = small.search(s, max_depth=depth)
        assert (r.score, r.move, r.nodes) == (big.score, big.move, big.nodes), s


def test_cache_is_really_bounded_and_used(sample):
    sr = CheckedSearcher(0, cache_limit=40)
    for s in sample[:5]:
        sr.search(s, max_depth=4)
    assert sr.max_seen == 40  # the limit was reached (and the cache cleared), never exceeded


def test_caches_are_freed_after_search(sample):
    sr = Searcher(0)
    sr.search(sample[3], max_depth=3)
    assert not sr._dist_cache and not sr._mask_cache and not sr._path_cache


def test_caches_are_freed_after_timeout(sample):
    sr = Searcher(0)
    r = sr.search(sample[3], time_limit=0.05)
    assert r.depth >= 1
    assert not sr._dist_cache and not sr._mask_cache and not sr._path_cache
