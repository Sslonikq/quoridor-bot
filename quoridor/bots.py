"""Bots: random, greedy, the alpha-beta bots and frozen baselines (quoridor.frozen).

No I/O, no global state (each bot owns its random.Random). SearchBot reads the clock only
when it has a time limit; with a fixed depth and no time limit it is fully deterministic.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from quoridor.eval import PLAN_V4, RESERVE_V4, SOFT_V4, THREAT_V4, WALL, WALL_V3
from quoridor.frozen import search_v1, search_v4
from quoridor.mcts import MCTSBot
from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import apply_move_unchecked, is_wall_legal, legal_moves, pawn_moves
from quoridor.search import (
    COMBO_BEAM,
    COMBO_COST,
    COMBO_K,
    DEFAULT_WALL_LIMIT,
    V2_TT_BITS,
    Searcher,
    SearchResult,
)
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


# Searcher options of v5 (phase 9) with their "off" defaults; SearchBot passes them through.
V5_OPTIONS: dict[str, int] = {
    "combo": 0,
    "combo_k": COMBO_K,
    "combo_beam": COMBO_BEAM,
    "combo_cost": COMBO_COST,
    "combo_root": 0,
    "combo_stock": 0,
    "combo_exact": 0,
    "combo_narrow": 0,
    "rep": 0,
    "rep_penalty": 0,
    "progress": 0,
    "race_plan": PLAN_V4,
    "race_soft": SOFT_V4,
}


class SearcherLike(Protocol):
    def search(
        self, state: GameState, max_depth: int | None = None, time_limit: float | None = None
    ) -> SearchResult: ...


class SearchBot:
    """Alpha-beta + iterative deepening (quoridor.search) with the eval of quoridor.eval.

    Limits (any combination, the tightest wins):
      depth     -- max search depth in plies (fixed depth + no time = deterministic);
      move_time -- seconds per move;
      game_time -- own clock for the whole game: per-move budget from quoridor.timeman.
    tt_bits > 0 turns on the transposition table of 2**tt_bits entries (v2); v4=1 the v4
    eval (search.Searcher.wall_race when a side has no walls left).
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
        searcher: SearcherLike | None = None,
        tt_bits: int = 0,
        v4: int = 0,
        pvs: int = 0,
        lmr: int = 0,
        delay_slack: int = 0,
        threat: int = 0,
        root_defense: int = 0,
        reserve: int = 0,
        **v5: int,
    ) -> None:
        unknown = set(v5) - set(V5_OPTIONS)
        if unknown:
            raise TypeError(f"SearchBot: unknown options {', '.join(sorted(unknown))}")
        if depth is None and move_time is None and game_time is None:
            raise ValueError("SearchBot needs depth, move_time or game_time")
        self.depth = depth
        self.move_time = move_time
        self.game_time = game_time
        # `searcher` replaces the default Searcher (e.g. a frozen older version); then
        # seed, wall_limit, wall_weight and tt_bits are the injected searcher's business.
        # Non-default values next to `searcher` would be silently ignored, so they are an
        # error (review 0040, minor 2: "frozen:tt_bits=18" must not run as plain v1).
        if searcher is not None:
            ignored = [
                name
                for name, val, default in (
                    ("wall_limit", wall_limit, DEFAULT_WALL_LIMIT),
                    ("wall_weight", wall_weight, WALL),
                    ("tt_bits", tt_bits, 0),
                    ("v4", v4, 0),
                    ("pvs", pvs, 0),
                    ("lmr", lmr, 0),
                    ("delay_slack", delay_slack, 0),
                    ("threat", threat, 0),
                    ("root_defense", root_defense, 0),
                    ("reserve", reserve, 0),
                    *((k, val, V5_OPTIONS[k]) for k, val in v5.items()),
                )
                if val != default
            ]
            if ignored:
                raise ValueError(
                    f"SearchBot: {', '.join(ignored)} would be ignored with searcher=; "
                    "configure the injected searcher instead"
                )
        self._searcher: SearcherLike = (
            Searcher(
                seed,
                wall_limit=wall_limit,
                wall_weight=wall_weight,
                tt_bits=tt_bits,
                v4=v4,
                pvs=pvs,
                lmr=lmr,
                delay_slack=delay_slack,
                threat=threat,
                root_defense=root_defense,
                reserve=reserve,
                **v5,
            )
            if searcher is None
            else searcher
        )
        self.used = 0.0  # seconds spent in choose_move this game
        self.moves = 0
        self.last: SearchResult | None = None
        # Positions of the current game before the one to move in (v5: repetitions). The
        # bot records what it sees and its own moves, so in a game played move by move
        # (arena, cli) the history is complete; set_history() gives a game prefix.
        self._history: list[GameState] = []
        self._after: GameState | None = None  # the position after our last move

    def set_history(self, states: list[GameState]) -> None:
        """The game's positions before the next one the bot is asked about (oldest first)."""
        self._history = list(states)
        self._after = None

    def _track(self, state: GameState) -> None:
        """Extend the history with `state`, or start a new one if it does not follow
        from our last move by one opponent move (a new game or an edited position)."""
        after = self._after
        if after is not None:
            if _follows(after, state):
                self._history.append(after)
            else:
                self._history = []
        self._after = None

    def _time_limit(self) -> float | None:
        limit = self.move_time
        if self.game_time is not None:
            budget = move_budget(self.game_time - self.used, self.moves)
            limit = budget if limit is None else min(limit, budget)
        return limit

    def choose_move(self, state: GameState) -> Move:
        t0 = time.perf_counter()
        self._track(state)
        set_history = getattr(self._searcher, "set_history", None)
        if set_history is not None:
            set_history(self._history)
        res = self._searcher.search(state, self.depth, self._time_limit())
        self.used += time.perf_counter() - t0
        self.moves += 1
        self.last = res
        self._history.append(state)
        self._after = apply_move_unchecked(state, res.move)
        return res.move


def _follows(before: GameState, state: GameState) -> bool:
    """True if `state` is `before` after one move of its side to move."""
    p = before.turn
    if state.turn == p or state.pawns[1 - p] != before.pawns[1 - p]:
        return False
    if state.walls_left[1 - p] != before.walls_left[1 - p]:
        return False
    if state.pawns[p] != before.pawns[p]:  # a pawn move: walls unchanged
        return (
            state.h_walls == before.h_walls
            and state.v_walls == before.v_walls
            and state.walls_left == before.walls_left
        )
    added = len(state.h_walls) + len(state.v_walls) - len(before.h_walls) - len(before.v_walls)
    return (
        added == 1
        and state.h_walls >= before.h_walls
        and state.v_walls >= before.v_walls
        and state.walls_left[p] == before.walls_left[p] - 1
    )


V1_DEPTH = 3  # arena / default: fixed depth, deterministic per seed


def make_v1(seed: int) -> SearchBot:
    return SearchBot(seed, depth=V1_DEPTH)


def make_v1_clock(seed: int) -> SearchBot:
    """v1 on a 10-minute game clock (for real games; slow for 100-game matches)."""
    return SearchBot(seed, game_time=GAME_TIME)


def make_v1_frozen(seed: int) -> SearchBot:
    """v1 exactly as at the end of phase 5 (quoridor/frozen/search_v1.py), depth V1_DEPTH.

    A fixed baseline: unlike "v1" it does not change when search.py / eval.py do.
    """
    return SearchBot(seed, depth=V1_DEPTH, searcher=search_v1.Searcher(seed))


V2_DEPTH = V1_DEPTH  # same depth as v1: at a fixed depth the TT changes no score, only speed
FAST_MOVE_TIME = 0.2  # seconds per move for the equal-time matches "v1-0.2s" vs "v2-0.2s"


def make_v2(seed: int) -> SearchBot:
    """v1 + transposition table (search.V2_TT_BITS), fixed depth V2_DEPTH."""
    return SearchBot(seed, depth=V2_DEPTH, tt_bits=V2_TT_BITS)


def make_v2_clock(seed: int) -> SearchBot:
    """v2 on a 10-minute game clock."""
    return SearchBot(seed, game_time=GAME_TIME, tt_bits=V2_TT_BITS)


V3_DEPTH = V1_DEPTH  # fixed-depth variant for deterministic tests; strength is at equal time
V3_WALL_WEIGHT = WALL_V3


def make_v3(seed: int) -> SearchBot:
    """v2 (TT) + the v3 eval (wall stock weight WALL_V3), fixed depth V3_DEPTH."""
    return SearchBot(seed, depth=V3_DEPTH, tt_bits=V2_TT_BITS, wall_weight=V3_WALL_WEIGHT)


def make_v3_clock(seed: int) -> SearchBot:
    """v3 on a 10-minute game clock (the version for real games)."""
    return SearchBot(seed, game_time=GAME_TIME, tt_bits=V2_TT_BITS, wall_weight=V3_WALL_WEIGHT)


def make_v3_fast(seed: int) -> SearchBot:
    """v3 with FAST_MOVE_TIME per move (equal-time comparison; not deterministic)."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, tt_bits=V2_TT_BITS, wall_weight=V3_WALL_WEIGHT)


def make_v1_frozen_fast(seed: int) -> SearchBot:
    """The frozen v1 with FAST_MOVE_TIME per move: an equal-time baseline that never changes."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, searcher=search_v1.Searcher(seed))


def make_v1_fast(seed: int) -> SearchBot:
    """v1 with FAST_MOVE_TIME per move (equal-time comparison; not deterministic)."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME)


def make_v2_fast(seed: int) -> SearchBot:
    """v2 with FAST_MOVE_TIME per move (equal-time comparison; not deterministic)."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, tt_bits=V2_TT_BITS)


V4_DEPTH = V1_DEPTH  # fixed-depth variant for deterministic tests; strength is at equal time
# Searcher settings of v4 (also used by the analyzer): v3 + the v4 eval.
V4_SETTINGS: dict[str, int] = {
    "tt_bits": V2_TT_BITS,
    "wall_weight": WALL_V3,
    "v4": 1,
    "pvs": 1,
    "lmr": 3,
    "delay_slack": 2,
    "threat": THREAT_V4,
    "root_defense": 3,
    # report 0066: the opponent's wall stock as a threat while we have 0-2 walls; at 3 s
    # 64.5% against v4-frozen and 64.0% against v3 (100 games each)
    "reserve": RESERVE_V4,
}


def make_v4(seed: int) -> SearchBot:
    """v3 + the v4 eval (report 0061), fixed depth V4_DEPTH."""
    return SearchBot(seed, depth=V4_DEPTH, **V4_SETTINGS)


def make_v4_clock(seed: int) -> SearchBot:
    """v4 on a 10-minute game clock (the version for real games, default in cli)."""
    return SearchBot(seed, game_time=GAME_TIME, **V4_SETTINGS)


def make_v4_fast(seed: int) -> SearchBot:
    """v4 with FAST_MOVE_TIME per move (equal-time comparison; not deterministic)."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, **V4_SETTINGS)


MATCH_MOVE_TIME = 3.0  # seconds per move for the equal-time matches at a realistic control

V5_DEPTH = V1_DEPTH  # fixed-depth variant for deterministic tests; strength is at equal time
# v5 (phase 9, steps 3-4, report 0075): v4 + combined wall threats (quoridor.threat): in the
# eval (root-wall potentials, 2 walls, narrow corridor parts) and at the root (our
# combination walls and walls breaking the opponent's, 4 at most); a root penalty of two
# steps per recent return to an own position, progress on equal root scores. Tree
# repetitions ("rep") and the capped wall stock ("combo_stock") stay off: they lost matches.
# 1 s per move: 133/200 (66.5%) against v4; anti-shuffle alone 99/200.
V5_SETTINGS: dict[str, int] = {
    **V4_SETTINGS,
    "combo": 20,
    "combo_k": 3,
    "combo_beam": 3,
    "combo_narrow": 3,
    "combo_root": 4,
    "rep_penalty": 200,
    "progress": 1,
}


def make_v5(seed: int) -> SearchBot:
    """v4 + v5 options (V5_SETTINGS), fixed depth V5_DEPTH."""
    return SearchBot(seed, depth=V5_DEPTH, **V5_SETTINGS)


def make_v5_clock(seed: int) -> SearchBot:
    """v5 on a 10-minute game clock."""
    return SearchBot(seed, game_time=GAME_TIME, **V5_SETTINGS)


def make_v5_fast(seed: int) -> SearchBot:
    """v5 with FAST_MOVE_TIME per move (equal-time comparison; not deterministic)."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, **V5_SETTINGS)


def make_v3_match(seed: int) -> SearchBot:
    """v3 with MATCH_MOVE_TIME per move (equal-time matches at 3 s; not deterministic)."""
    return SearchBot(
        seed, move_time=MATCH_MOVE_TIME, tt_bits=V2_TT_BITS, wall_weight=V3_WALL_WEIGHT
    )


def make_v4_match(seed: int) -> SearchBot:
    """v4 with MATCH_MOVE_TIME per move (equal-time matches at 3 s; not deterministic)."""
    return SearchBot(seed, move_time=MATCH_MOVE_TIME, **V4_SETTINGS)


def make_v5_match(seed: int) -> SearchBot:
    """v5 with MATCH_MOVE_TIME per move (equal-time matches at 3 s; not deterministic)."""
    return SearchBot(seed, move_time=MATCH_MOVE_TIME, **V5_SETTINGS)


# MCTS experiment (phase 9, step 5, report 0076): PUCT with a wall-aware policy and the v5
# eval as the leaf value (quoridor.mcts). Not a default anywhere.
MCTS_SIMS = 300  # fixed simulations: deterministic per seed (tests); strength is at equal time
MCTS_SEARCHER: dict[str, int] = {
    k: val for k, val in V5_SETTINGS.items() if k not in ("rep_penalty", "progress", "tt_bits")
}
MCTS_OPTIONS: dict[str, float] = {"rep_penalty": 0.2}


def make_mcts(seed: int) -> MCTSBot:
    """MCTS with MCTS_SIMS simulations per move (deterministic per seed)."""
    return MCTSBot(seed, sims=MCTS_SIMS, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)


def make_mcts_clock(seed: int) -> MCTSBot:
    """MCTS on a 10-minute game clock."""
    return MCTSBot(seed, game_time=GAME_TIME, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)


def make_mcts_fast(seed: int) -> MCTSBot:
    """MCTS with FAST_MOVE_TIME per move."""
    return MCTSBot(seed, move_time=FAST_MOVE_TIME, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)


def make_mcts_match(seed: int) -> MCTSBot:
    """MCTS with MATCH_MOVE_TIME per move."""
    return MCTSBot(seed, move_time=MATCH_MOVE_TIME, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)


# MCTS2 (phase 9, step 4, report 0083): the MCTS with lazy expansion (a leaf gets its
# moves on its second visit: x1.3-1.5 simulations per second, half the edge memory) and a
# strong repetition penalty (game03 21+, game07 26: no walking back into own cells).
# Report 0084: plus the exact endgame solver at the root when both sides have <= 2 walls.
MCTS2_OPTIONS: dict[str, float] = {
    **MCTS_OPTIONS,
    "rep_penalty": 1.0,
    "expand_visits": 2,
    "solver": 2,
}


def _mcts2(seed: int, **limit: float) -> MCTSBot:
    return MCTSBot(seed, searcher_settings=MCTS_SEARCHER, **limit, **MCTS2_OPTIONS)  # type: ignore[arg-type]


def make_mcts2(seed: int) -> MCTSBot:
    """MCTS2 with MCTS_SIMS simulations per move (deterministic per seed)."""
    return _mcts2(seed, sims=MCTS_SIMS)


def make_mcts2_clock(seed: int) -> MCTSBot:
    """MCTS2 on a 10-minute game clock."""
    return _mcts2(seed, game_time=GAME_TIME)


def make_mcts2_fast(seed: int) -> MCTSBot:
    """MCTS2 with FAST_MOVE_TIME per move."""
    return _mcts2(seed, move_time=FAST_MOVE_TIME)


def make_mcts2_match(seed: int) -> MCTSBot:
    """MCTS2 with MATCH_MOVE_TIME per move."""
    return _mcts2(seed, move_time=MATCH_MOVE_TIME)


def make_v4_frozen(seed: int) -> SearchBot:
    """v4 exactly as in reports 0061/0062 (quoridor/frozen/search_v4.py), depth V4_DEPTH.

    A fixed baseline: unlike "v4" it does not change when search.py / eval.py /
    V4_SETTINGS do.
    """
    return SearchBot(seed, depth=V4_DEPTH, searcher=search_v4.make_searcher(seed))


def make_v4_frozen_fast(seed: int) -> SearchBot:
    """The frozen v4 with FAST_MOVE_TIME per move."""
    return SearchBot(seed, move_time=FAST_MOVE_TIME, searcher=search_v4.make_searcher(seed))


def make_v4_frozen_match(seed: int) -> SearchBot:
    """The frozen v4 with MATCH_MOVE_TIME per move."""
    return SearchBot(seed, move_time=MATCH_MOVE_TIME, searcher=search_v4.make_searcher(seed))


def make_v4_frozen_clock(seed: int) -> SearchBot:
    """The frozen v4 on a 10-minute game clock."""
    return SearchBot(seed, game_time=GAME_TIME, searcher=search_v4.make_searcher(seed))


# Single source of truth for the move limit and the bot registry (used by cli and arena).
DEFAULT_MAX_MOVES = 300
BotFactory = Callable[[int], Bot]

# Registry: name -> factory(seed). A new bot version is added with one line.
BOTS: dict[str, BotFactory] = {
    "random": RandomBot,
    "greedy": GreedyBot,
    "v1": make_v1,
    "v1-clock": make_v1_clock,
    "v1-frozen": make_v1_frozen,
    "v1-0.2s": make_v1_fast,
    "v2": make_v2,
    "v2-clock": make_v2_clock,
    "v2-0.2s": make_v2_fast,
    "v1-frozen-0.2s": make_v1_frozen_fast,
    "v3": make_v3,
    "v3-clock": make_v3_clock,
    "v3-0.2s": make_v3_fast,
    "v4": make_v4,
    "v4-clock": make_v4_clock,
    "v4-0.2s": make_v4_fast,
    "v3-3s": make_v3_match,
    "v4-3s": make_v4_match,
    "v4-frozen": make_v4_frozen,
    "v4-frozen-clock": make_v4_frozen_clock,
    "v4-frozen-0.2s": make_v4_frozen_fast,
    "v4-frozen-3s": make_v4_frozen_match,
    "v5": make_v5,
    "v5-clock": make_v5_clock,
    "v5-0.2s": make_v5_fast,
    "v5-3s": make_v5_match,
    "mcts": make_mcts,
    "mcts-clock": make_mcts_clock,
    "mcts-0.2s": make_mcts_fast,
    "mcts-3s": make_mcts_match,
    "mcts2": make_mcts2,
    "mcts2-clock": make_mcts2_clock,
    "mcts2-0.2s": make_mcts2_fast,
    "mcts2-3s": make_mcts2_match,
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
