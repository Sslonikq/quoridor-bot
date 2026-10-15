"""Parameter-tuning stand: parallel equal-time matches between two bot specs (offline).

Run:  python -m tests.perf.tune A B [--games 100] [--procs 8] [--move-time 0.2] [--seed 100]

A and B are specs:
  NAME                      a bot from quoridor.bots.BOTS (as is, e.g. v3 or greedy);
  search:k=v,k=v,...        SearchBot with kwargs (ints/floats), e.g.
                            search:wall_weight=125,tt_bits=18,move_time=1.0
  frozen:k=v,...            the same, but with the frozen v1 searcher (quoridor.frozen).
If move_time/depth/game_time is absent in a `search:`/`frozen:` spec, --move-time is used.
The games are split between --procs processes (seeds seed, seed+1, ...; colours alternate
inside every chunk, as in the arena). Both sides play under the same machine load, so the
result is a comparison at equal time. Prints W/L/D for A, score with the 95% Wilson interval,
average move time and plies (weighted by games per process) and the wall time of the run.
No network, nothing is written to disk.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from quoridor.arena import run_match, wilson_interval
from quoridor.bots import BOTS, BotFactory, SearchBot
from quoridor.frozen import search_v1


def parse_spec(spec: str, default_move_time: float) -> BotFactory:
    """Spec string -> bot factory(seed). Raises ValueError on a malformed spec."""
    if spec in BOTS:
        return BOTS[spec]
    kind, _, rest = spec.partition(":")
    if kind not in ("search", "frozen"):
        raise ValueError(f"unknown bot spec {spec!r}")
    kw: dict[str, int | float] = {}
    for item in filter(None, rest.split(",")):
        k, eq, v = item.partition("=")
        if not eq:
            raise ValueError(f"bad parameter {item!r} in {spec!r} (expected k=v)")
        kw[k] = float(v) if "." in v else int(v)
    if not any(k in kw for k in ("move_time", "depth", "game_time")):
        kw["move_time"] = default_move_time

    def factory(seed: int) -> SearchBot:
        if kind == "frozen":
            return SearchBot(seed, searcher=search_v1.Searcher(seed), **kw)  # type: ignore[arg-type]
        return SearchBot(seed, **kw)  # type: ignore[arg-type]

    # Build one bot now: an unknown key or a setting that SearchBot would ignore (e.g.
    # frozen:tt_bits=18, review 0040 minor 2) fails here, in the parent, before any game.
    try:
        factory(0)
    except TypeError as e:
        raise ValueError(f"bad parameters in {spec!r}: {e}") from e
    return factory


def combine(results: list[tuple]) -> tuple[int, int, int, float, float, float]:
    """Sum W/L/D over the chunks; average move times and plies weighted by games per chunk.

    Chunks differ in size when games % procs != 0 (review 0040, nit 9), so a plain mean
    of the chunk averages would overweight the smaller chunks.
    """
    w = sum(r[0] for r in results)
    lo = sum(r[1] for r in results)
    d = sum(r[2] for r in results)
    n = w + lo + d
    if n == 0:
        return 0, 0, 0, 0.0, 0.0, 0.0

    def avg(i: int) -> float:
        return sum(r[i] * (r[0] + r[1] + r[2]) for r in results) / n

    return w, lo, d, avg(3), avg(4), avg(5)


def _job(args: tuple[str, str, float, int, int, int]):
    a, b, move_time, games, seed, max_moves = args
    reg = {"A": parse_spec(a, move_time), "B": parse_spec(b, move_time)}
    m = run_match(games, "A", "B", seed=seed, max_moves=max_moves, registry=reg)
    t = m.total
    return t.wins, t.losses, t.draws, m.avg_move_time_a, m.avg_move_time_b, m.avg_plies


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--games", type=int, default=100, help="total games (default 100)")
    p.add_argument("--procs", type=int, default=8, help="parallel processes (default 8)")
    p.add_argument("--move-time", type=float, default=0.2, help="seconds per move (default 0.2)")
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--max-moves", type=int, default=300)
    ns = p.parse_args(argv)
    parse_spec(ns.a, ns.move_time)  # validate early, in the parent
    parse_spec(ns.b, ns.move_time)
    procs = max(1, min(ns.procs, ns.games))
    base, extra = divmod(ns.games, procs)
    jobs = [
        (ns.a, ns.b, ns.move_time, base + (i < extra), ns.seed + i, ns.max_moves)
        for i in range(procs)
    ]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(procs) as ex:
        res = list(ex.map(_job, jobs))
    w, lo, d, ta, tb, pl = combine(res)
    n = w + lo + d
    lo_ci, hi_ci = wilson_interval(w + d / 2, n) if n else (0.0, 0.0)
    print(f"{ns.a} vs {ns.b}: W {w} L {lo} D {d} n={n}")
    print(
        f"score {100 * (w + d / 2) / max(n, 1):.1f}%  "
        f"Wilson95 [{100 * lo_ci:.1f}, {100 * hi_ci:.1f}]"
    )
    print(f"avg move time A {1000 * ta:.0f} ms, B {1000 * tb:.0f} ms; avg plies {pl:.0f}")
    print(f"elapsed {time.perf_counter() - t0:.0f} s ({procs} procs, move_time {ns.move_time})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
