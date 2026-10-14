"""Phase 5 perf sanity (slow). No absolute time thresholds: only ratios to naive baselines
measured in the same process, and structural properties (deterministic node counts).
The numbers themselves come from `python -m tests.perf.bench_search`."""

import time

import pytest

from quoridor.search import Searcher
from tests.perf.bench_search import bench_bfs, bench_depth, bench_positions
from tests.search.test_search_edge import plain_negamax

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def positions():
    ps = bench_positions()
    assert len(ps) >= 40
    return ps


def test_bench_positions_are_fixed(positions):
    # The benchmark is only reproducible if the position set never silently changes.
    assert [s.turn for s in positions] == [s.turn for s in bench_positions()]
    assert positions == bench_positions()


def test_nodes_are_deterministic_and_grow_with_depth(positions):
    sub = positions[:15]
    a = [bench_depth(sub, d, repeat=1).nodes for d in (2, 3, 4)]
    b = [bench_depth(sub, d, repeat=1).nodes for d in (2, 3, 4)]
    assert a == b
    assert a[0] < a[1] < a[2]


def test_nps_is_sane(positions):
    st = bench_depth(positions[:20], 3, repeat=2)
    assert st.nodes > 0 and st.nps > 0
    assert st.ms_per_position < 5000  # absurdly loose: catches a hang, not a slowdown


def test_bitboard_bfs_beats_reference_bfs(positions):
    ref, fast = bench_bfs(positions, repeat=3)
    assert ref / fast > 1.5, (ref, fast)  # measured ~4.5x


def test_alpha_beta_beats_plain_negamax(positions):
    sub = positions[:20]

    def run_ab():
        t0 = time.perf_counter()
        for s in sub:
            Searcher(0, wall_limit=2).search(s, max_depth=3)
        return time.perf_counter() - t0

    def run_plain():
        t0 = time.perf_counter()
        for s in sub:
            sr = Searcher(0, wall_limit=2)
            for _, child in sr.ordered_moves(s):
                plain_negamax(sr, child, 2, 1)
        return time.perf_counter() - t0

    # Robust to machine load: ratio of per-side minima over interleaved rounds, with up to
    # 4 extra rounds if the ratio is still below the threshold (a load spike hits one side).
    ab, plain = float("inf"), float("inf")
    for _ in range(7):
        ab = min(ab, run_ab())
        plain = min(plain, run_plain())
        if plain / ab > 1.3:
            break
    assert plain / ab > 1.3, (plain, ab)  # measured ~2.2x (iterative deepening costs some)


def test_search_reaches_useful_depth_in_half_second(positions):
    # loose lower bound (phase-5 average is ~8): catches a return to the pre-phase-5 speed
    # only if it were far worse, and never fails on a merely slow machine by a small margin.
    depths = [Searcher(0).search(s, time_limit=0.5).depth for s in positions[:8]]
    assert sum(depths) / len(depths) >= 4
