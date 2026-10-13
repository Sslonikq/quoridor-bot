"""Arena: bot-vs-bot matches. run_match() is pure logic (no I/O); only main() prints.

Usage: python -m quoridor.arena --games 100 --a greedy --b random [--seed N]
[--max-moves N] [--quiet]
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from quoridor.bots import (
    BOTS,
    DEFAULT_MAX_MOVES,
    NEAR_DETERMINISTIC,
    Bot,
    BotFactory,
    UnknownBotError,
    make_bot,
)
from quoridor.notation import format_move
from quoridor.rules import apply_move, winner
from quoridor.state import BLACK, WHITE, GameState

__all__ = [
    # re-exported from quoridor.bots (the registry lives there)
    "BOTS",
    "DEFAULT_MAX_MOVES",
    "NEAR_DETERMINISTIC",
    "Bot",
    "BotFactory",
    "UnknownBotError",
    "make_bot",
    # arena
    "EXIT_INTERRUPTED",
    "GameError",
    "GameResult",
    "MatchResult",
    "Tally",
    "build_parser",
    "format_report",
    "game_seed",
    "main",
    "play_game",
    "run_match",
    "wilson_interval",
]

EXIT_INTERRUPTED = 130


class GameError(RuntimeError):
    """A bot crashed or made an illegal move. Carries everything needed to replay the game."""

    def __init__(
        self,
        index: int,
        seed: int | None,
        a_color: int,
        side: int,
        moves: Sequence[str],
        cause: BaseException,
    ) -> None:
        self.index = index
        self.seed = seed
        self.a_color = a_color
        self.side = side  # colour of the side that failed
        self.moves = tuple(moves)  # moves played before the failure
        self.cause = cause
        super().__init__(self.describe())

    def describe(self) -> str:
        names = ("белые", "чёрные")
        a_side = "A" if self.side == self.a_color else "B"
        log = " ".join(self.moves) if self.moves else "(пусто)"
        return (
            f"партия {self.index + 1}, seed партии {self.seed}: ошибка бота {a_side} "
            f"({names[self.side]}), ход №{len(self.moves) + 1}: "
            f"{type(self.cause).__name__}: {self.cause}\n"
            f"A играет {names[self.a_color]}. Уже сыграно: {log}"
        )


def game_seed(base_seed: int, index: int) -> int:
    """Seed of game `index`, derived from the base seed and the game number."""
    return base_seed * 1_000_003 + index


@dataclass(frozen=True)
class GameResult:
    index: int
    a_color: int  # WHITE or BLACK: the colour A plays in this game
    winner: int | None  # WHITE / BLACK / None (draw by move limit)
    plies: int
    moves: tuple[str, ...]  # notation log
    time_a: float  # seconds spent by A thinking
    time_b: float
    moves_a: int
    moves_b: int

    @property
    def outcome_for_a(self) -> str:
        """'W' | 'L' | 'D' from A's point of view."""
        if self.winner is None:
            return "D"
        return "W" if self.winner == self.a_color else "L"


@dataclass(frozen=True)
class Tally:
    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.draws


@dataclass(frozen=True)
class MatchResult:
    a: str
    b: str
    seed: int
    max_moves: int
    games: tuple[GameResult, ...] = field(default_factory=tuple)

    def _tally(self, a_color: int | None = None) -> Tally:
        w = l_ = d = 0
        for g in self.games:
            if a_color is not None and g.a_color != a_color:
                continue
            o = g.outcome_for_a
            w += o == "W"
            l_ += o == "L"
            d += o == "D"
        return Tally(w, l_, d)

    @property
    def total(self) -> Tally:
        return self._tally()

    @property
    def as_white(self) -> Tally:
        """A's results in the games where A played white."""
        return self._tally(WHITE)

    @property
    def as_black(self) -> Tally:
        return self._tally(BLACK)

    @property
    def win_rate(self) -> float:
        """Share of A's wins among all games (draws count as non-wins)."""
        n = len(self.games)
        return self.total.wins / n if n else 0.0

    @property
    def score(self) -> float:
        """(W + D/2) / n: draws count as half a win."""
        n = len(self.games)
        return (self.total.wins + self.total.draws / 2) / n if n else 0.0

    @property
    def wilson(self) -> tuple[float, float]:
        return wilson_interval(self.total.wins, len(self.games))

    @property
    def avg_plies(self) -> float:
        return sum(g.plies for g in self.games) / len(self.games) if self.games else 0.0

    @property
    def avg_move_time_a(self) -> float:
        n = sum(g.moves_a for g in self.games)
        return sum(g.time_a for g in self.games) / n if n else 0.0

    @property
    def avg_move_time_b(self) -> float:
        n = sum(g.moves_b for g in self.games)
        return sum(g.time_b for g in self.games) / n if n else 0.0

    @property
    def mirror_deterministic(self) -> bool:
        return self.a == self.b and self.a in NEAR_DETERMINISTIC


def wilson_interval(wins: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval (95% by default) for a binomial proportion."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def play_game(
    bot_a: Bot,
    bot_b: Bot,
    a_color: int,
    max_moves: int = DEFAULT_MAX_MOVES,
    index: int = 0,
    seed: int | None = None,
) -> GameResult:
    """One game. Bots are called only on non-terminal positions; move limit gives a draw.

    A bot exception or an illegal bot move is re-raised as GameError (with game context).
    """
    bots = {a_color: bot_a, 1 - a_color: bot_b}
    state = GameState.initial()
    log: list[str] = []
    spent = {a_color: 0.0, 1 - a_color: 0.0}
    count = {a_color: 0, 1 - a_color: 0}
    plies = 0
    while winner(state) is None and plies < max_moves:
        side = state.turn
        t0 = time.perf_counter()
        try:
            move = bots[side].choose_move(state)
            spent[side] += time.perf_counter() - t0
            count[side] += 1
            new_state = apply_move(state, move)  # checked: an illegal bot move raises
            log.append(format_move(move))
        except Exception as e:
            raise GameError(index, seed, a_color, side, log, e) from e
        state = new_state
        plies += 1
    return GameResult(
        index=index,
        a_color=a_color,
        winner=winner(state),
        plies=plies,
        moves=tuple(log),
        time_a=spent[a_color],
        time_b=spent[1 - a_color],
        moves_a=count[a_color],
        moves_b=count[1 - a_color],
    )


def run_match(
    games: int,
    a: str,
    b: str,
    seed: int = 0,
    max_moves: int = DEFAULT_MAX_MOVES,
    registry: Mapping[str, BotFactory] | None = None,
    on_game: Callable[[GameResult], None] | None = None,
) -> MatchResult:
    """Play `games` games A vs B. A is white in even-numbered games, black in odd ones.

    With an odd `games` A gets white one time more than black. Per-game seeds derive from
    (seed, game index); bot A gets 2*s, bot B gets 2*s+1. `on_game` is called after each
    finished game (progress hook; run_match itself does no I/O). A bot failure raises GameError.
    """
    if games < 0:
        raise ValueError("games must be >= 0")
    if max_moves < 1:
        raise ValueError("max_moves must be >= 1")
    results: list[GameResult] = []
    for i in range(games):
        s = game_seed(seed, i)
        bot_a = make_bot(a, 2 * s, registry)
        bot_b = make_bot(b, 2 * s + 1, registry)
        a_color = WHITE if i % 2 == 0 else BLACK
        res = play_game(bot_a, bot_b, a_color, max_moves, i, s)
        results.append(res)
        if on_game is not None:
            on_game(res)
    return MatchResult(a, b, seed, max_moves, tuple(results))


def _fmt_tally(t: Tally) -> str:
    return f"W {t.wins} / L {t.losses} / D {t.draws}"


def format_report(m: MatchResult, elapsed: float | None = None) -> str:
    n = len(m.games)
    lo, hi = m.wilson
    lines = [f"Матч: A={m.a} против B={m.b}, партий: {n}, seed {m.seed}, лимит {m.max_moves} п/х"]
    if m.mirror_deterministic:
        lines.append(
            f"ПРЕДУПРЕЖДЕНИЕ: {m.a} против {m.b} почти детерминирован (seed влияет только на "
            "тай-брейки), партии повторяются; матч не информативен."
        )
    wa, ba = m.as_white.games, m.as_black.games
    note = "" if wa == ba else f" (нечётное число партий: A белыми на {wa - ba} больше)"
    lines.append(f"Партий: A белыми {wa}, A чёрными {ba}{note}")
    lines.append(f"Итого для A: {_fmt_tally(m.total)}")
    lines.append(f"  белыми: {_fmt_tally(m.as_white)}")
    lines.append(f"  чёрными: {_fmt_tally(m.as_black)}")
    lines.append(
        f"Доля побед A: {m.win_rate * 100:.1f}% (ничьи не в счёт побед), "
        f"95% Уилсон: [{lo * 100:.1f}%, {hi * 100:.1f}%]"
    )
    lines.append(f"Счёт A (W + D/2)/n: {m.score * 100:.1f}%")
    lines.append(f"Среднее число ходов (полуходов) за партию: {m.avg_plies:.1f}")
    lines.append(
        f"Среднее время хода: A {m.avg_move_time_a * 1000:.2f} мс, "
        f"B {m.avg_move_time_b * 1000:.2f} мс"
    )
    if elapsed is not None:
        lines.append(f"Общее время матча: {elapsed:.1f} с")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m quoridor.arena", description="Матч бот против бота")
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--a", required=True, help=f"бот A ({', '.join(sorted(BOTS))})")
    p.add_argument("--b", required=True, help=f"бот B ({', '.join(sorted(BOTS))})")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-moves", type=int, default=DEFAULT_MAX_MOVES, help="лимит полуходов")
    p.add_argument("--quiet", action="store_true", help="не печатать прогресс по партиям")
    return p


def _progress_line(g: GameResult) -> str:
    side = "белыми" if g.a_color == WHITE else "чёрными"
    return f"Партия {g.index + 1}: A {side}, {g.outcome_for_a}, {g.plies} п/х"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Russian text on Windows consoles
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if args.games < 1 or args.max_moves < 1:
        print("Ошибка: --games и --max-moves должны быть >= 1", file=sys.stderr)
        return 2
    try:  # fail fast on bad names, before any game is played
        make_bot(args.a, 0)
        make_bot(args.b, 0)
    except UnknownBotError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2

    def on_game(g: GameResult) -> None:
        print(_progress_line(g), flush=True)

    t0 = time.perf_counter()
    try:
        result = run_match(
            args.games,
            args.a,
            args.b,
            args.seed,
            args.max_moves,
            on_game=None if args.quiet else on_game,
        )
    except GameError as e:
        print(f"Ошибка матча: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nМатч прерван.", file=sys.stderr)
        return EXIT_INTERRUPTED
    elapsed = time.perf_counter() - t0
    print(format_report(result, elapsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
