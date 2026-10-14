"""Reproducible search benchmark (phase 5).

Run:  python -m tests.perf.bench_search [--depths 3 4 5] [--repeat 3]

Positions are fixed: every 3rd ply of seeded random games (tests.playouts), terminal and
forced-win positions dropped. For each depth it prints ms per position, nodes per position
and nodes/sec (best of --repeat runs: the minimum is the least noisy estimate), plus the
speed of the bitboard BFS against the reference BFS in quoridor.paths.
Absolute numbers depend on the machine; compare runs taken back to back.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from quoridor.eval import WIN
from quoridor.paths import shortest_path_length
from quoridor.search import Searcher
from tests.playouts import random_game

BENCH_SEEDS = (300, 301, 302, 303)
EVERY = 3
MAX_PLIES = 60
WALL_BIAS = 0.5


def bench_positions(limit: int | None = None):
    out = []
    for seed in BENCH_SEEDS:
        for i, s in enumerate(random_game(seed, max_plies=MAX_PLIES, wall_bias=WALL_BIAS)):
            if i % EVERY == 0 and i > 0:
                out.append(s)
    # forced-win positions are not representative (search ends immediately)
    keep = [s for s in out if abs(Searcher(0).search(s, max_depth=2).score) < WIN - 200]
    return keep if limit is None else keep[:limit]


@dataclass(frozen=True)
class DepthStat:
    depth: int
    positions: int
    seconds: float  # total, best run
    nodes: int  # total, same for every run (deterministic)

    @property
    def ms_per_position(self) -> float:
        return 1000 * self.seconds / self.positions

    @property
    def nps(self) -> float:
        return self.nodes / self.seconds


def bench_depth(positions, depth: int, repeat: int = 3) -> DepthStat:
    best = float("inf")
    nodes = 0
    for _ in range(repeat):
        n = 0
        t0 = time.perf_counter()
        for s in positions:
            n += Searcher(0).search(s, max_depth=depth).nodes
        dt = time.perf_counter() - t0
        best = min(best, dt)
        nodes = n
    return DepthStat(depth, len(positions), best, nodes)


def bench_bfs(positions, repeat: int = 3) -> tuple[float, float]:
    """(seconds with reference BFS, seconds with Searcher.dist cold cache) for all queries."""
    ref = fast = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        for s in positions:
            for p in (0, 1):
                shortest_path_length(s, p)
        ref = min(ref, time.perf_counter() - t0)
        t0 = time.perf_counter()
        for s in positions:
            sr = Searcher(0)  # fresh caches: every query is a real BFS
            for p in (0, 1):
                sr.dist(s, p)
        fast = min(fast, time.perf_counter() - t0)
    return ref, fast


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depths", type=int, nargs="+", default=[3, 4, 5])
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args(argv)
    ps = bench_positions()
    print(f"positions: {len(ps)}")
    for d in args.depths:
        st = bench_depth(ps, d, args.repeat)
        print(
            f"d{d}: {st.ms_per_position:8.1f} ms/pos  {st.nodes / st.positions:9.0f} nodes/pos"
            f"  {st.nps / 1000:6.1f}k nodes/s"
        )
    ref, fast = bench_bfs(ps, args.repeat)
    print(
        f"BFS x{2 * len(ps)}: reference {ref * 1000:.1f} ms, bitboard {fast * 1000:.1f} ms"
        f" (x{ref / fast:.1f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
