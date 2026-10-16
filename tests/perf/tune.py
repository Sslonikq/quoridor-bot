"""Parameter-tuning stand: parallel equal-time matches between two bot specs (offline).

Run:  python -m tests.perf.tune A B [--games 100] [--procs 8] [--move-time 0.2] [--seed 100]

A and B are specs:
  NAME                      a bot from quoridor.bots.BOTS (as is, e.g. v3 or greedy);
  search:k=v,k=v,...        SearchBot with kwargs (ints/floats), e.g.
                            search:wall_weight=125,tt_bits=18,move_time=1.0
  frozen:k=v,...            the same, but with the frozen v1 searcher (quoridor.frozen);
  frozen4:k=v,...           the same with the frozen v4 searcher (its own settings), e.g.
                            frozen4: (= v4-frozen at --move-time).
If move_time/depth/game_time is absent in a `search:`/`frozen*:` spec, --move-time is used.
The games are split between --procs processes (seeds seed, seed+1, ...; colours alternate
inside every chunk, as in the arena). Both sides play under the same machine load, so the
result is a comparison at equal time. Prints W/L/D for A, score with the 95% Wilson interval,
average move time and plies (weighted by games per process) and the wall time of the run.
Sleep guard: a game in which two consecutive wall-clock stamps (start / end of every move)
are more than --max-gap seconds apart (default 30: the machine slept or the process was
suspended, so the clock bots' budgets and the move times are garbage) is dropped; the
number of dropped games is printed and the statistics are over the remaining ones.
No network, nothing is written to disk.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor

from quoridor.arena import GameResult, run_match, wilson_interval
from quoridor.bots import BOTS, Bot, BotFactory, SearchBot
from quoridor.frozen import search_v1, search_v4
from quoridor.state import GameState, Move

MAX_GAP = 30.0  # seconds between consecutive move stamps: more = the machine slept


def parse_spec(spec: str, default_move_time: float) -> BotFactory:
    """Spec string -> bot factory(seed). Raises ValueError on a malformed spec."""
    if spec in BOTS:
        return BOTS[spec]
    kind, _, rest = spec.partition(":")
    if kind not in ("search", "frozen", "frozen4"):
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
        if kind == "frozen4":
            return SearchBot(seed, searcher=search_v4.make_searcher(seed), **kw)  # type: ignore[arg-type]
        return SearchBot(seed, **kw)  # type: ignore[arg-type]

    # Build one bot now: an unknown key or a setting that SearchBot would ignore (e.g.
    # frozen:tt_bits=18, review 0040 minor 2) fails here, in the parent, before any game.
    try:
        factory(0)
    except TypeError as e:
        raise ValueError(f"bad parameters in {spec!r}: {e}") from e
    return factory


class StampedBot:
    """Wraps a bot: appends a wall-clock stamp (time.time) before and after every move."""

    def __init__(
        self, bot: Bot, stamps: list[float], clock: Callable[[], float] = time.time
    ) -> None:
        self._bot = bot
        self._stamps = stamps
        self._clock = clock

    def choose_move(self, state: GameState) -> Move:
        self._stamps.append(self._clock())
        move = self._bot.choose_move(state)
        self._stamps.append(self._clock())
        return move


def max_gap(stamps: Sequence[float]) -> float:
    """Largest difference between consecutive stamps (0 for fewer than two)."""
    return max((b - a for a, b in zip(stamps, stamps[1:], strict=False)), default=0.0)


def summarize(games: Sequence[GameResult], dropped: int = 0) -> tuple:
    """(W, L, D for A, avg move time A, avg move time B, avg plies, dropped) over `games`."""
    w = sum(g.outcome_for_a == "W" for g in games)
    lo = sum(g.outcome_for_a == "L" for g in games)
    d = sum(g.outcome_for_a == "D" for g in games)
    na = sum(g.moves_a for g in games)
    nb = sum(g.moves_b for g in games)
    ta = sum(g.time_a for g in games) / na if na else 0.0
    tb = sum(g.time_b for g in games) / nb if nb else 0.0
    pl = sum(g.plies for g in games) / len(games) if games else 0.0
    return w, lo, d, ta, tb, pl, dropped


def combine(results: list[tuple]) -> tuple[int, int, int, float, float, float, int]:
    """Sum W/L/D and dropped games over the chunks; average move times and plies weighted
    by kept games per chunk.

    Chunks differ in size when games % procs != 0 (review 0040, nit 9), so a plain mean
    of the chunk averages would overweight the smaller chunks. A chunk tuple is
    (W, L, D, time A, time B, plies[, dropped]).
    """
    w = sum(r[0] for r in results)
    lo = sum(r[1] for r in results)
    d = sum(r[2] for r in results)
    dropped = sum(r[6] if len(r) > 6 else 0 for r in results)
    n = w + lo + d
    if n == 0:
        return 0, 0, 0, 0.0, 0.0, 0.0, dropped

    def avg(i: int) -> float:
        return sum(r[i] * (r[0] + r[1] + r[2]) for r in results) / n

    return w, lo, d, avg(3), avg(4), avg(5), dropped


def run_guarded(
    games: int,
    reg: dict[str, BotFactory],
    seed: int,
    max_moves: int,
    gap_limit: float = MAX_GAP,
    clock: Callable[[], float] = time.time,
) -> tuple:
    """run_match "A" vs "B" with the sleep guard: summarize() of the games whose largest
    gap between move stamps is <= gap_limit, plus the number of dropped games.
    `clock` replaces time.time for the stamps (tests)."""
    stamps: list[float] = []
    kept: list[GameResult] = []
    dropped = 0

    def wrap(f: BotFactory) -> BotFactory:
        def factory(s: int) -> Bot:
            return StampedBot(f(s), stamps, clock)

        return factory

    def on_game(g: GameResult) -> None:
        nonlocal dropped
        if max_gap(stamps) > gap_limit:
            dropped += 1
        else:
            kept.append(g)
        stamps.clear()

    wrapped = {k: wrap(f) for k, f in reg.items()}
    run_match(games, "A", "B", seed=seed, max_moves=max_moves, registry=wrapped, on_game=on_game)
    return summarize(kept, dropped)


def _job(args: tuple[str, str, float, int, int, int, float]):
    a, b, move_time, games, seed, max_moves, gap_limit = args
    reg = {"A": parse_spec(a, move_time), "B": parse_spec(b, move_time)}
    return run_guarded(games, reg, seed, max_moves, gap_limit)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--games", type=int, default=100, help="total games (default 100)")
    p.add_argument("--procs", type=int, default=8, help="parallel processes (default 8)")
    p.add_argument("--move-time", type=float, default=0.2, help="seconds per move (default 0.2)")
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--max-moves", type=int, default=300)
    p.add_argument(
        "--max-gap",
        type=float,
        default=MAX_GAP,
        help=f"drop games with a wall-clock gap between moves above this (s, default {MAX_GAP:g})",
    )
    ns = p.parse_args(argv)
    parse_spec(ns.a, ns.move_time)  # validate early, in the parent
    parse_spec(ns.b, ns.move_time)
    procs = max(1, min(ns.procs, ns.games))
    base, extra = divmod(ns.games, procs)
    jobs = [
        (ns.a, ns.b, ns.move_time, base + (i < extra), ns.seed + i, ns.max_moves, ns.max_gap)
        for i in range(procs)
    ]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(procs) as ex:
        res = list(ex.map(_job, jobs))
    w, lo, d, ta, tb, pl, dropped = combine(res)
    n = w + lo + d
    lo_ci, hi_ci = wilson_interval(w + d / 2, n) if n else (0.0, 0.0)
    print(f"{ns.a} vs {ns.b}: W {w} L {lo} D {d} n={n}")
    print(
        f"score {100 * (w + d / 2) / max(n, 1):.1f}%  "
        f"Wilson95 [{100 * lo_ci:.1f}, {100 * hi_ci:.1f}]"
    )
    print(
        f"dropped {dropped} game(s) (wall-clock gap between moves > {ns.max_gap:g} s: "
        "sleep / suspend); the statistics are over the other games"
    )
    print(f"avg move time A {1000 * ta:.0f} ms, B {1000 * tb:.0f} ms; avg plies {pl:.0f}")
    print(f"elapsed {time.perf_counter() - t0:.0f} s ({procs} procs, move_time {ns.move_time})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
