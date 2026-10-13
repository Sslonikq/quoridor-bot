"""Bots: random, greedy and the alpha-beta bot v1.

No I/O, no global state (each bot owns its random.Random). SearchBot reads the clock only
when it has a time limit; with a fixed depth and no time limit it is fully deterministic.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from quoridor.eval import WALL
from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import apply_move_unchecked, is_wall_legal, legal_moves, pawn_moves
from quoridor.search import DEFAULT_WALL_LIMIT, Searcher, SearchResult
from quoridor.state import (
    WALL_GRID,
    Cell,
    GameState,
    H,
    Move,
    PawnMove,
    V,
    WallMove,
    goal_row,
)
from quoridor.timeman import GAME_TIME, move_budget


class Bot(Protocol):
    def choose_move(self, state: GameState) -> Move:
        """Return a legal move for the side to move (state must be non-terminal)."""
        ...


class RandomBot:
    """Uniformly random among all legal moves."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def choose_move(self, state: GameState) -> Move:
        moves = legal_moves(state)
        if not moves:
            raise ValueError("no legal moves (terminal position)")
        return self._rng.choice(moves)


def _edge_blockers(a: Cell, b: Cell) -> list[WallMove]:
    """All (in-grid-or-not) walls that block the edge between adjacent cells a and b."""
    (c, r), (nc, nr) = a, b
    if nr == r + 1:
        return [WallMove(c, r, H), WallMove(c - 1, r, H)]
    if nr == r - 1:
        return [WallMove(c, r - 1, H), WallMove(c - 1, r - 1, H)]
    if nc == c + 1:
        return [WallMove(c, r, V), WallMove(c, r - 1, V)]
    return [WallMove(c - 1, r, V), WallMove(c - 1, r - 1, V)]


def candidate_walls(state: GameState) -> list[WallMove]:
    """Legal walls that block an edge of the opponent's current shortest path.

    Any wall that lengthens the opponent's shortest path must block an edge of every
    shortest path, so this set is complete for a "lengthen the opponent" objective.
    Order is deterministic (path order).
    """
    me = state.turn
    if state.walls_left[me] <= 0:
        return []
    path = shortest_path(state, 1 - me)
    if not path:
        return []
    seen: set[WallMove] = set()
    out: list[WallMove] = []
    for a, b in zip(path, path[1:], strict=False):
        for w in _edge_blockers(a, b):
            if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                continue
            seen.add(w)
            if is_wall_legal(state, w):
                out.append(w)
    return out


class GreedyBot:
    """Maximises (opponent shortest path - own shortest path) after the move.

    Ties: pawn moves are preferred over walls, then a seeded random choice.
    A winning pawn move is always taken.
    """

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    @staticmethod
    def _score(state: GameState, me: int) -> int:
        mine = shortest_path_length(state, me)
        theirs = shortest_path_length(state, 1 - me)
        assert mine is not None and theirs is not None
        return theirs - mine

    def choose_move(self, state: GameState) -> Move:
        me = state.turn
        goal = goal_row(me)
        pawns = pawn_moves(state)
        if not pawns:
            raise ValueError("no legal moves (terminal position)")
        for m in pawns:
            if m.row == goal:
                return m

        best_pawn: list[PawnMove] = []
        best_pawn_score: int | None = None
        for m in pawns:
            sc = self._score(apply_move_unchecked(state, m), me)
            if best_pawn_score is None or sc > best_pawn_score:
                best_pawn_score, best_pawn = sc, [m]
            elif sc == best_pawn_score:
                best_pawn.append(m)

        best_wall: list[WallMove] = []
        best_wall_score: int | None = None
        for w in candidate_walls(state):
            sc = self._score(apply_move_unchecked(state, w), me)
            if best_wall_score is None or sc > best_wall_score:
                best_wall_score, best_wall = sc, [w]
            elif sc == best_wall_score:
                best_wall.append(w)

        assert best_pawn_score is not None
        if best_wall_score is not None and best_wall_score > best_pawn_score:
            return self._rng.choice(best_wall)
        return self._rng.choice(best_pawn)


class SearchBot:
    """Alpha-beta + iterative deepening (quoridor.search) with the eval of quoridor.eval.

    Limits (any combination, the tightest wins):
      depth     -- max search depth in plies (fixed depth + no time = deterministic);
      move_time -- seconds per move;
      game_time -- own clock for the whole game: per-move budget from quoridor.timeman.
    The seed only breaks ties between equally scored root moves.
    """

    def __init__(
        self,
        seed: int = 0,
        depth: int | None = None,
        move_time: float | None = None,
        game_time: float | None = None,
        wall_limit: int = DEFAULT_WALL_LIMIT,
        wall_weight: int = WALL,
    ) -> None:
        if depth is None and move_time is None and game_time is None:
            raise ValueError("SearchBot needs depth, move_time or game_time")
        self.depth = depth
        self.move_time = move_time
        self.game_time = game_time
        self._searcher = Searcher(seed, wall_limit=wall_limit, wall_weight=wall_weight)
        self.used = 0.0  # seconds spent in choose_move this game
        self.moves = 0
        self.last: SearchResult | None = None

    def _time_limit(self) -> float | None:
        limit = self.move_time
        if self.game_time is not None:
            budget = move_budget(self.game_time - self.used, self.moves)
            limit = budget if limit is None else min(limit, budget)
        return limit

    def choose_move(self, state: GameState) -> Move:
        t0 = time.perf_counter()
        res = self._searcher.search(state, self.depth, self._time_limit())
        self.used += time.perf_counter() - t0
        self.moves += 1
        self.last = res
        return res.move


V1_DEPTH = 3  # arena / default: fixed depth, deterministic per seed


def make_v1(seed: int) -> SearchBot:
    return SearchBot(seed, depth=V1_DEPTH)


def make_v1_clock(seed: int) -> SearchBot:
    """v1 on a 10-minute game clock (for real games; slow for 100-game matches)."""
    return SearchBot(seed, game_time=GAME_TIME)


# Single source of truth for the move limit and the bot registry (used by cli and arena).
DEFAULT_MAX_MOVES = 300
BotFactory = Callable[[int], Bot]

# Registry: name -> factory(seed). A new bot version is added with one line.
BOTS: dict[str, BotFactory] = {
    "random": RandomBot,
    "greedy": GreedyBot,
    "v1": make_v1,
    "v1-clock": make_v1_clock,
}
# Bots whose play does not depend on the seed in a meaningful way (seed only breaks ties):
# a mirror match of such a bot repeats the same game.
NEAR_DETERMINISTIC = frozenset({"greedy"})


class UnknownBotError(ValueError):
    """Bot name is not in the registry."""


def make_bot(name: str, seed: int, registry: Mapping[str, BotFactory] | None = None) -> Bot:
    reg = BOTS if registry is None else registry
    try:
        factory = reg[name]
    except KeyError:
        known = ", ".join(sorted(reg))
        raise UnknownBotError(f"неизвестный бот {name!r}. Доступны: {known}") from None
    return factory(seed)
