"""Parameter-tuning stand: parallel equal-time matches between two bot specs (offline).

Run:  python -m tests.perf.tune A B [--games 100] [--procs 8] [--move-time 0.2] [--seed 100]

A and B are specs:
  NAME                      a bot from quoridor.bots.BOTS (as is, e.g. v3 or greedy);
  search:k=v,k=v,...        SearchBot with kwargs (ints/floats), e.g.
                            search:wall_weight=125,tt_bits=18,move_time=1.0
  frozen:k=v,...            the same, but with the frozen v1 searcher (quoridor.frozen);
  frozen4:k=v,...           the same with the frozen v4 searcher (its own settings), e.g.
                            frozen4: (= v4-frozen at --move-time).
  v4:k=v,... / v5:k=v,...   SearchBot with bots.V4_SETTINGS / V5_SETTINGS plus overrides,
                            e.g. v5:move_time=1.0 or v4: (= v4 at --move-time);
  mcts:k=v,...              quoridor.mcts.MCTSBot (bots.MCTS_SEARCHER, MCTS_OPTIONS plus
                            overrides: options of mcts.MCTS_DEFAULTS, sims, move_time).
If move_time/depth/game_time/sims is absent in such a spec, --move-time is used.
The games are split between --procs processes (seeds seed, seed+1, ...; colours alternate
inside every chunk, as in the arena). Both sides play under the same machine load, so the
result is a comparison at equal time. Prints W/L/D for A, score with the 95% Wilson interval,
average move time and plies (weighted by games per process) and the wall time of the run.
Sleep guard: a game in which two consecutive wall-clock stamps (start / end of every move)
are more than --max-gap seconds apart (default max(30, 3 * budget), where budget is the
longest per-move limit of the two bots: move_time, or timeman.MAX_TIME for a game_time
bot; a jump above it means the machine slept or the process was suspended, so the clock
bots' budgets and the move times are garbage) is dropped; the number of dropped games is
printed and the statistics are over the remaining ones. An explicit --max-gap that is not
above the budget would drop every game, so it is an argparse error (review 0069 minor 3).
No network, nothing is written to disk.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor

from quoridor.arena import GameResult, run_match, wilson_interval
from quoridor.bots import (
    BOTS,
    MCTS_OPTIONS,
    MCTS_SEARCHER,
    V4_SETTINGS,
    V5_SETTINGS,
    Bot,
    BotFactory,
    SearchBot,
)
from quoridor.frozen import search_v1, search_v4
from quoridor.mcts import MCTSBot
from quoridor.state import GameState, Move
from quoridor.timeman import MAX_TIME

MAX_GAP = 30.0  # seconds between consecutive move stamps: more = the machine slept
GAP_FACTOR = 3.0  # the default --max-gap is at least GAP_FACTOR * the per-move budget
_TIME_KEYS = ("move_time", "depth", "game_time", "sims")
_KINDS = ("search", "frozen", "frozen4", "v4", "v5", "mcts")


def parse_spec(spec: str, default_move_time: float) -> BotFactory:
    """Spec string -> bot factory(seed). Raises ValueError on a malformed spec."""
    if spec in BOTS:
        return BOTS[spec]
    kind, _, rest = spec.partition(":")
    if kind not in _KINDS:
        raise ValueError(f"unknown bot spec {spec!r}")
    kw: dict[str, int | float] = {}
    for item in filter(None, rest.split(",")):
        k, eq, v = item.partition("=")
        if not eq:
            raise ValueError(f"bad parameter {item!r} in {spec!r} (expected k=v)")
        kw[k] = float(v) if "." in v else int(v)
    if not any(k in kw for k in _TIME_KEYS):
        kw["move_time"] = default_move_time

    def factory(seed: int) -> Bot:
        if kind == "mcts":
            return MCTSBot(seed, searcher_settings=MCTS_SEARCHER, **{**MCTS_OPTIONS, **kw})  # type: ignore[arg-type]
        if kind in ("v4", "v5"):
            base = V4_SETTINGS if kind == "v4" else V5_SETTINGS
            return SearchBot(seed, **{**base, **kw})  # type: ignore[arg-type]
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


def uses_default_move_time(spec: str) -> bool:
    """True if the spec takes --move-time (a spec of a kind in _KINDS without own limit)."""
    if spec in BOTS:
        return False
    kind, _, rest = spec.partition(":")
    keys = {item.partition("=")[0] for item in filter(None, rest.split(","))}
    return kind in _KINDS and not keys & set(_TIME_KEYS)


def move_budget_of(factory: BotFactory) -> float:
    """Longest wall time one move of this bot may plan for (s): its move_time, MAX_TIME
    of quoridor.timeman for a game_time bot, 0 for depth-only and non-search bots."""
    bot = factory(0)
    budget = getattr(bot, "move_time", None) or 0.0
    if getattr(bot, "game_time", None):
        budget = max(budget, MAX_TIME)
    return float(budget)


def gap_limit_for(budget: float, max_gap: float | None) -> float:
    """--max-gap for a run whose slowest bot plans `budget` s per move.

    None -> max(MAX_GAP, GAP_FACTOR * budget). An explicit value <= budget would drop
    (nearly) every game, so it raises ValueError (review 0069 minor 3)."""
    if max_gap is None:
        return max(MAX_GAP, GAP_FACTOR * budget)
    if max_gap <= budget:
        raise ValueError(
            f"--max-gap {max_gap:g} s is not above the per-move budget {budget:g} s: "
            "every game would be dropped"
        )
    return max_gap


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
        default=None,
        help="drop games with a wall-clock gap between moves above this "
        f"(s, default max({MAX_GAP:g}, {GAP_FACTOR:g} * per-move budget))",
    )
    ns = p.parse_args(argv)
    # validate early, in the parent
    budget = max(move_budget_of(parse_spec(s, ns.move_time)) for s in (ns.a, ns.b))
    try:
        gap_limit = gap_limit_for(budget, ns.max_gap)
    except ValueError as e:
        p.error(str(e))
    procs = max(1, min(ns.procs, ns.games))
    base, extra = divmod(ns.games, procs)
    jobs = [
        (ns.a, ns.b, ns.move_time, base + (i < extra), ns.seed + i, ns.max_moves, gap_limit)
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
        f"dropped {dropped} game(s) (wall-clock gap between moves > {gap_limit:g} s: "
        "sleep / suspend); the statistics are over the other games"
    )
    print(f"avg move time A {1000 * ta:.0f} ms, B {1000 * tb:.0f} ms; avg plies {pl:.0f}")
    if uses_default_move_time(ns.a) or uses_default_move_time(ns.b):
        timing = f"move_time {ns.move_time:g} for specs without own limit"
    else:
        timing = "own time limits of the bots"
    print(f"elapsed {time.perf_counter() - t0:.0f} s ({procs} procs, {timing})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
