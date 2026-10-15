"""Reproducible search benchmark (phase 5).

Run:  python -m tests.perf.bench_search [--depths 3 4 5] [--repeat 3] [--tt-bits B]
                                        [--wall-weight W] [--time 0.5] [--memory 15]

Positions are fixed: every 3rd ply of seeded random games (tests.playouts), terminal and
forced-win positions dropped. For each depth it prints ms per position, nodes per position
and nodes/sec (best of --repeat runs: the minimum is the least noisy estimate), plus the
speed of the bitboard BFS against the reference BFS in quoridor.paths.
--time T: average completed depth with time_limit=T over all positions.
--memory T: peak memory of one search with time_limit=T on a fixed middlegame position,
measured in a fresh child process (peak working set / max RSS of that process).
--tt-bits / --wall-weight: Searcher parameters (v3 = --tt-bits 18 --wall-weight 125).
Absolute numbers depend on the machine; compare runs taken back to back.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def make_searcher(tt_bits: int | None = None, wall_weight: int | None = None) -> Searcher:
    """Searcher(0); None keeps the Searcher default for that parameter."""
    kw: dict[str, int] = {}
    if tt_bits is not None:
        kw["tt_bits"] = tt_bits
    if wall_weight is not None:
        kw["wall_weight"] = wall_weight
    return Searcher(0, **kw)


def bench_depth(
    positions,
    depth: int,
    repeat: int = 3,
    tt_bits: int | None = None,
    wall_weight: int | None = None,
) -> DepthStat:
    best = float("inf")
    nodes = 0
    for _ in range(repeat):
        n = 0
        t0 = time.perf_counter()
        for s in positions:
            n += make_searcher(tt_bits, wall_weight).search(s, max_depth=depth).nodes
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


MEMORY_POSITION = 26  # index into bench_positions(): middlegame, 8 walls on board, 6 + 6 left


def peak_rss_bytes() -> int:
    """Peak resident memory of this process so far (bytes)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(pmc.PeakWorkingSetSize)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # Linux reports KiB


def memory_child(time_limit: float, tt_bits: int | None, wall_weight: int | None = None) -> dict:
    """Runs in the child process: one timed search, returns the peak memory figures."""
    s = bench_positions()[MEMORY_POSITION]
    sr = make_searcher(tt_bits, wall_weight)
    before = peak_rss_bytes()
    r = sr.search(s, time_limit=time_limit)
    return {
        "base_mb": before / 2**20,
        "peak_mb": peak_rss_bytes() / 2**20,
        "depth": r.depth,
        "nodes": r.nodes,
        "seconds": r.elapsed,
    }


def bench_memory(time_limit: float, tt_bits: int | None, wall_weight: int | None = None) -> dict:
    """memory_child in a fresh interpreter, so the peak belongs to this search only."""
    cmd = [sys.executable, "-m", "tests.perf.bench_search", "--memory-child", str(time_limit)]
    if tt_bits is not None:
        cmd += ["--tt-bits", str(tt_bits)]
    if wall_weight is not None:
        cmd += ["--wall-weight", str(wall_weight)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def bench_time_depth(
    positions, time_limit: float, tt_bits: int | None = None, wall_weight: int | None = None
) -> float:
    """Average completed depth with a time limit (machine-dependent)."""
    depths = [
        make_searcher(tt_bits, wall_weight).search(s, time_limit=time_limit).depth
        for s in positions
    ]
    return sum(depths) / len(depths)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depths", type=int, nargs="+", default=[3, 4, 5])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--tt-bits", type=int, default=None, help="Searcher(tt_bits=...)")
    ap.add_argument(
        "--wall-weight", type=int, default=None, help="Searcher(wall_weight=...), v3: 125"
    )
    ap.add_argument("--time", type=float, default=None, help="depth at this time limit")
    ap.add_argument("--memory", type=float, default=None, help="peak memory at this limit")
    ap.add_argument("--memory-child", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.memory_child is not None:
        print(json.dumps(memory_child(args.memory_child, args.tt_bits, args.wall_weight)))
        return 0
    ps = bench_positions()
    print(f"positions: {len(ps)}")
    for d in args.depths:
        st = bench_depth(ps, d, args.repeat, args.tt_bits, args.wall_weight)
        print(
            f"d{d}: {st.ms_per_position:8.1f} ms/pos  {st.nodes / st.positions:9.0f} nodes/pos"
            f"  {st.nps / 1000:6.1f}k nodes/s"
        )
    ref, fast = bench_bfs(ps, args.repeat)
    print(
        f"BFS x{2 * len(ps)}: reference {ref * 1000:.1f} ms, bitboard {fast * 1000:.1f} ms"
        f" (x{ref / fast:.1f})"
    )
    if args.time is not None:
        avg = bench_time_depth(ps, args.time, args.tt_bits, args.wall_weight)
        print(f"depth at {args.time} s: {avg:.2f} (average over {len(ps)} positions)")
    if args.memory is not None:
        m = bench_memory(args.memory, args.tt_bits, args.wall_weight)
        print(
            f"memory at {args.memory} s: peak {m['peak_mb']:.0f} MB (before search "
            f"{m['base_mb']:.0f} MB), depth {m['depth']}, {m['nodes']} nodes, "
            f"{m['seconds']:.1f} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
