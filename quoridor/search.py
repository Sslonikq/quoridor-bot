"""Alpha-beta (negamax) with iterative deepening.

* Moves: all pawn moves + a pruned set of walls. Candidate walls block an edge of the
  opponent's current shortest path (any wall that lengthens that path must do so); of
  those only walls with a positive gain (opponent path grows more than ours) are kept,
  best `wall_limit` by gain. Legality of every kept wall: rules.wall_fits + both distances
  after the wall exist (the same as rules.is_wall_legal, without a second BFS).
  Exception: if the opponent threatens to win with its next pawn move, the candidates are
  exactly the walls that stop that win, whatever their gain (a defensive wall often
  lengthens our own path too, so its gain is <= 0).
* Ordering: previous iteration's scores at the root; inside the tree the killer move of
  the ply (last cutoff move there, if it is in the move set) first, then pawn moves that
  shorten our path, walls by gain, other pawn moves. Walls are generated lazily: only
  if no earlier move gave a cutoff.
* Win/loss: a position where the previous mover reached its goal is lost for the side
  to move; a side that can step onto its goal row wins at once (WIN - ply, so faster
  wins / slower losses are preferred).
* Distances: bitboard BFS (quoridor.bitpaths), memoised per search by (wall masks, pawn
  cell, player), so a leaf evaluation usually reuses the BFS done while ordering its
  parent's moves. The memo caches are bounded (cleared when full, see cache_limit) and
  freed when search() returns. Candidate walls are scored on child masks without building a child
  state; states are built only for the kept walls.
* Transposition table (v2, tt_bits > 0): 2**tt_bits slots owned by the Searcher,
  indexed by an incremental Zobrist key (pawns, walls, walls left, side to move), entry =
  (key, depth, value, bound, best move); always-replace. Values are reused only from
  entries of exactly the same remaining depth (and mate scores are stored relative to
  the node), so with a fixed depth the search returns the same scores as without the
  table; entries of any depth give their best move, which is tried first. The slots are
  a dict {slot index: entry}, filled lazily: a new search() starts with an empty dict
  (no 2**tt_bits allocation per move, report 0043) and it is dropped on return. The
  replacement scheme is the same as a fixed-size list, so the results are too.
* Limits: fixed depth (deterministic, for the arena/tests) and/or a time budget. On
  timeout the result of the last completed iteration (or a fully searched improvement
  from the current one) is returned. Root ties are broken by a seeded shuffle.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from dataclasses import dataclass

from quoridor import bitpaths
from quoridor.bitpaths import GOAL_MASK, Masks, add_wall, bit, build_masks
from quoridor.eval import WALL, WIN, evaluate_dists
from quoridor.rules import (
    apply_move_unchecked,
    legal_moves,
    pawn_moves,
    pawn_moves_for,
    wall_fits,
    winner,
)
from quoridor.state import WALL_GRID, Cell, GameState, H, Move, PawnMove, V, WallMove, goal_row

INF = WIN * 2
MAX_DEPTH = 64
DEFAULT_WALL_LIMIT = 10
# Memo caches are cleared when they reach this many entries (distance cache; masks and
# paths get a quarter each), so memory stays bounded however long the search runs.
DEFAULT_CACHE_LIMIT = 300_000
_CHECK_EVERY = 64  # nodes between clock checks
V2_TT_BITS = 18  # 2**18 = 262 144 entries (bounded memory, see report 0036)

# ---- Zobrist keys (immutable module constants, fixed seed: reproducible) ----
_ZMAX_WALLS = 128  # walls_left values covered; larger stocks disable the table
_zrng = random.Random(0x51D0B0A2)
_ZPAWN = tuple(tuple(_zrng.getrandbits(64) for _ in range(81)) for _ in range(2))
_ZWALL = {o: tuple(_zrng.getrandbits(64) for _ in range(WALL_GRID * WALL_GRID)) for o in (H, V)}
_ZSTOCK = tuple(tuple(_zrng.getrandbits(64) for _ in range(_ZMAX_WALLS)) for _ in range(2))
_ZTURN = _zrng.getrandbits(64)
del _zrng
EXACT, LOWER, UPPER = 0, 1, 2  # TT bound types


def zobrist(state: GameState) -> int:
    """Full Zobrist key of a state (the search updates it incrementally: child_key)."""
    k = _ZTURN if state.turn else 0
    for p in (0, 1):
        c, r = state.pawns[p]
        k ^= _ZPAWN[p][r * 9 + c] ^ _ZSTOCK[p][state.walls_left[p]]
    for x, y in state.h_walls:
        k ^= _ZWALL[H][y * WALL_GRID + x]
    for x, y in state.v_walls:
        k ^= _ZWALL[V][y * WALL_GRID + x]
    return k


def child_key(key: int, state: GameState, move: Move) -> int:
    """zobrist(apply_move(state, move)) from key = zobrist(state), in O(1)."""
    me = state.turn
    if isinstance(move, PawnMove):
        c, r = state.pawns[me]
        return key ^ _ZTURN ^ _ZPAWN[me][r * 9 + c] ^ _ZPAWN[me][move.row * 9 + move.col]
    n = state.walls_left[me]
    return (
        key
        ^ _ZTURN
        ^ _ZWALL[move.orient][move.y * WALL_GRID + move.x]
        ^ _ZSTOCK[me][n]
        ^ _ZSTOCK[me][n - 1]
    )


def _to_tt(v: int, ply: int) -> int:
    """Mate scores relative to the node (WIN - ply depends on the path to it)."""
    if v >= WIN - 10_000:
        return v + ply
    if v <= -(WIN - 10_000):
        return v - ply
    return v


def _from_tt(v: int, ply: int) -> int:
    if v >= WIN - 10_000:
        return v - ply
    if v <= -(WIN - 10_000):
        return v + ply
    return v


class SearchTimeout(Exception):
    """Internal: raised inside the tree when the hard deadline passes."""


@dataclass(frozen=True)
class SearchResult:
    move: Move
    score: int  # for the side to move, centi-steps (or +-(WIN - ply))
    depth: int  # last fully completed depth
    nodes: int
    elapsed: float  # seconds
    # True if move/score come from the interrupted iteration depth + 1 (a fully searched
    # improvement found before the timeout), while `depth` is still the completed one.
    partial: bool = False

    @property
    def nps(self) -> float:
        return self.nodes / self.elapsed if self.elapsed > 0 else 0.0


def edge_walls(a: Cell, b: Cell) -> tuple[WallMove, WallMove]:
    """The two wall placements (maybe off-grid) that block the edge between adjacent a, b."""
    (c, r), (nc, nr) = a, b
    if nr == r + 1:
        return WallMove(c, r, H), WallMove(c - 1, r, H)
    if nr == r - 1:
        return WallMove(c, r - 1, H), WallMove(c - 1, r - 1, H)
    if nc == c + 1:
        return WallMove(c, r, V), WallMove(c, r - 1, V)
    return WallMove(c - 1, r, V), WallMove(c - 1, r - 1, V)


def is_win_score(score: int) -> bool:
    return abs(score) >= WIN - 10_000


def opponent_winning_moves(state: GameState) -> list[PawnMove]:
    """Pawn moves that would put the opponent of the side to move on its goal row now."""
    op = 1 - state.turn
    goal = goal_row(op)
    if abs(state.pawns[op][1] - goal) > 2:  # a step or a jump covers at most 2 rows
        return []
    return [m for m in pawn_moves_for(state, op) if m.row == goal]


class Searcher:
    """One searcher per bot; state between calls: only the RNG (for root tie-breaks)."""

    def __init__(
        self,
        seed: int = 0,
        wall_limit: int = DEFAULT_WALL_LIMIT,
        wall_weight: int = WALL,
        cache_limit: int = DEFAULT_CACHE_LIMIT,
        tt_bits: int = 0,
    ) -> None:
        self._rng = random.Random(seed)
        self.wall_limit = wall_limit
        self.wall_weight = wall_weight
        self.cache_limit = max(1, cache_limit)
        self._small_limit = max(1, cache_limit // 4)
        self.tt_bits = max(0, tt_bits)  # 0: no transposition table (v1)
        self._tt: dict[int, tuple] | None = None  # slot -> entry, empty at every search()
        self._tt_mask = (1 << self.tt_bits) - 1
        self._dist_cache: dict[tuple, int | None] = {}
        self._mask_cache: dict[tuple, Masks] = {}
        self._path_cache: dict[tuple, list[Cell] | None] = {}
        self._killers: list[Move | None] = [None] * (MAX_DEPTH + 2)  # by ply
        self.nodes = 0
        self._deadline: float | None = None

    # ---------- helpers ----------

    def masks(self, state: GameState) -> Masks:
        key = (state.h_walls, state.v_walls)
        m = self._mask_cache.get(key)
        if m is None:
            m = build_masks(state.h_walls, state.v_walls)
            self._store_masks(key, m)
        return m

    def _store_masks(self, key: tuple, m: Masks) -> None:
        if len(self._mask_cache) >= self._small_limit:
            self._mask_cache.clear()  # bounded memory; pure memo, only costs recomputation
        self._mask_cache[key] = m

    def dist_m(self, m: Masks, cell: Cell, player: int) -> int | None:
        """BFS length for `player` standing on `cell` with the wall masks `m` (memoised)."""
        key = (m, cell, player)
        cache = self._dist_cache
        d = cache.get(key, -1)
        if d == -1:
            if len(cache) >= self.cache_limit:
                cache.clear()
            d = cache[key] = bitpaths.distance(m, bit(cell), GOAL_MASK[player])
        return d

    def path_m(self, m: Masks, cell: Cell, player: int) -> list[Cell] | None:
        """Same path as paths.shortest_path (memoised; callers must not mutate it)."""
        key = (m, cell, player)
        cache = self._path_cache
        p = cache.get(key, 0)
        if p == 0:
            if len(cache) >= self._small_limit:
                cache.clear()
            p = cache[key] = bitpaths.shortest_path(m, cell, player)
        return p

    def dist(self, state: GameState, player: int) -> int | None:
        return self.dist_m(self.masks(state), state.pawns[player], player)

    def evaluate(self, state: GameState) -> int:
        me = state.turn
        d_me = self.dist(state, me)
        d_op = self.dist(state, 1 - me)
        assert d_me is not None and d_op is not None
        return evaluate_dists(
            d_me,
            d_op,
            state.walls_left[me],
            state.walls_left[1 - me],
            wall_weight=self.wall_weight,
        )

    def candidate_walls(self, state: GameState) -> list[tuple[int, WallMove, GameState]]:
        """(gain, wall, child) for pruned legal walls, best gain first (stable).

        If the opponent threatens to win with its next pawn move, only the walls that stop
        that win are returned, all of them, whatever their gain (see _saving_walls).
        """
        me = state.turn
        if state.walls_left[me] <= 0:
            return []
        threats = opponent_winning_moves(state)
        if threats:
            return self._saving_walls(state, threats)
        m = self.masks(state)
        my_cell, op_cell = state.pawns[me], state.pawns[1 - me]
        path = self.path_m(m, op_cell, 1 - me)
        if not path:
            return []
        d_me = self.dist_m(m, my_cell, me)
        d_op = len(path) - 1
        assert d_me is not None
        seen: set[WallMove] = set()
        scored: list[tuple[int, WallMove, Masks]] = []
        for a, b in zip(path, path[1:], strict=False):
            for w in edge_walls(a, b):
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if (w.x, w.y) in state.h_walls or (w.x, w.y) in state.v_walls:
                    continue  # occupied centre: illegal either way, skip the BFS
                cm = add_wall(m, w.x, w.y, w.orient)  # child masks, no child state yet
                op_after = self.dist_m(cm, op_cell, 1 - me)
                if op_after is None or op_after <= d_op:
                    continue
                me_after = self.dist_m(cm, my_cell, me)
                if me_after is None:
                    continue
                gain = (op_after - d_op) - (me_after - d_me)
                if gain <= 0:
                    continue
                scored.append((gain, w, cm))
        scored.sort(key=lambda t: -t[0])
        out: list[tuple[int, WallMove, GameState]] = []
        for gain, w, cm in scored:
            # Both players still have a path (distances above are not None), so the
            # path part of is_wall_legal holds; only the cheap part is left to check.
            if wall_fits(state, w):
                child = apply_move_unchecked(state, w)
                self._store_masks((child.h_walls, child.v_walls), cm)
                out.append((gain, w, child))
                if len(out) >= self.wall_limit:
                    break
        return out

    def _saving_walls(
        self, state: GameState, threats: list[PawnMove]
    ) -> list[tuple[int, WallMove, GameState]]:
        """Legal walls after which the opponent has no winning pawn move, best gain first.

        Complete: a wall changes nothing but the blocked edges, so a saving wall must block
        an edge of some winning route (step: P-G; jump or diagonal: P-O and O-G, where P is
        the opponent's pawn and O ours). Not cut by gain or wall_limit: when the opponent
        threatens a win in 1 every other wall loses at once, and saving walls are few.
        """
        me = state.turn
        op_cell = state.pawns[1 - me]
        my_cell = state.pawns[me]
        edges: list[tuple[Cell, Cell]] = []
        for m in threats:
            g = m.cell
            if abs(g[0] - op_cell[0]) + abs(g[1] - op_cell[1]) == 1:
                edges.append((op_cell, g))
            else:  # straight jump or diagonal over our pawn
                edges.append((op_cell, my_cell))
                edges.append((my_cell, g))
        m = self.masks(state)
        d_me = self.dist_m(m, my_cell, me)
        d_op = self.dist_m(m, op_cell, 1 - me)
        assert d_me is not None and d_op is not None
        op_goal = goal_row(1 - me)
        seen: set[WallMove] = set()
        scored: list[tuple[int, WallMove, GameState]] = []
        for a, b in edges:
            for w in edge_walls(a, b):
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if not wall_fits(state, w):
                    continue
                child = apply_move_unchecked(state, w)
                if any(pm.row == op_goal for pm in pawn_moves_for(child, 1 - me)):
                    continue  # the opponent still wins at once
                cm = add_wall(m, w.x, w.y, w.orient)
                op_after = self.dist_m(cm, op_cell, 1 - me)
                me_after = self.dist_m(cm, my_cell, me)
                if op_after is None or me_after is None:
                    continue  # cuts someone off: illegal
                self._store_masks((child.h_walls, child.v_walls), cm)
                scored.append(((op_after - d_op) - (me_after - d_me), w, child))
        scored.sort(key=lambda t: -t[0])
        return scored

    def _pawn_order(
        self, state: GameState, pawns: list[PawnMove]
    ) -> tuple[list[PawnMove], list[PawnMove]]:
        """Pawn moves split into (shorten our path, the rest), each by distance (stable)."""
        me = state.turn
        m = self.masks(state)  # a pawn move keeps the walls: children share these masks
        d_me = self.dist_m(m, state.pawns[me], me)
        assert d_me is not None
        good: list[tuple[int, PawnMove]] = []
        other: list[tuple[int, PawnMove]] = []
        for mv in pawns:
            d = self.dist_m(m, mv.cell, me)
            assert d is not None
            (good if d < d_me else other).append((d, mv))
        good.sort(key=lambda t: t[0])
        other.sort(key=lambda t: t[0])
        return [mv for _, mv in good], [mv for _, mv in other]

    def ordered_moves(
        self, state: GameState, pawns: list[PawnMove] | None = None
    ) -> list[tuple[Move, GameState]]:
        """Good pawn moves, then candidate walls (by gain), then the other pawn moves."""
        if pawns is None:
            pawns = pawn_moves(state)
        good, other = self._pawn_order(state, pawns)
        return (
            [(m, apply_move_unchecked(state, m)) for m in good]
            + [(w, c) for _, w, c in self.candidate_walls(state)]
            + [(m, apply_move_unchecked(state, m)) for m in other]
        )

    def _children(
        self,
        state: GameState,
        first: list[tuple[Move, GameState]],
        good: list[PawnMove],
        walls: list[tuple[int, WallMove, GameState]] | None,
        other: list[PawnMove],
        skip_wall: WallMove | None = None,
    ) -> Iterator[tuple[Move, GameState]]:
        """(move, child): `first`, good pawn moves, walls, other pawn moves.

        Lazy: children are built only when reached, and candidate walls (the expensive
        part) are generated only if no earlier move gave a cutoff. `skip_wall` is a wall
        already yielded in `first` without generating the walls (the TT move).
        """
        yield from first
        for m in good:
            yield m, apply_move_unchecked(state, m)
        if walls is None:
            walls = self.candidate_walls(state)
        for _, w, child in walls:
            if w == skip_wall:
                continue
            yield w, child
        for m in other:
            yield m, apply_move_unchecked(state, m)

    # ---------- tree ----------

    def _negamax(
        self, state: GameState, depth: int, alpha: int, beta: int, ply: int, key: int = 0
    ) -> int:
        """Fail-soft negamax value of `state` for its side to move.

        `key` = zobrist(state); used only when the transposition table is on.
        """
        self.nodes += 1
        if (
            self._deadline is not None
            and self.nodes % _CHECK_EVERY == 0
            and time.perf_counter() > self._deadline
        ):
            raise SearchTimeout
        if winner(state) is not None:  # the previous mover has reached its goal
            return -(WIN - ply)
        goal = goal_row(state.turn)
        if depth <= 0 and abs(state.pawns[state.turn][1] - goal) > 2:
            return self.evaluate(state)  # a step or a jump covers at most 2 rows: no win now
        pawns = pawn_moves(state)
        for m in pawns:
            if m.row == goal:
                return WIN - ply - 1
        if depth <= 0:
            return self.evaluate(state)
        tt = self._tt
        tt_move: Move | None = None
        if tt is not None:
            e = tt.get(key & self._tt_mask)
            if e is not None and e[0] == key:
                tt_move = e[4]
                if e[1] == depth:  # same depth only: the value equals the one without TT
                    v = _from_tt(e[2], ply)
                    bound = e[3]
                    if (
                        bound == EXACT
                        or (bound == LOWER and v >= beta)
                        or (bound == UPPER and v <= alpha)
                    ):
                        return v
        alpha0 = alpha
        # Order of ordered_moves, except that the killer of this ply (the last move that
        # gave a cutoff here) goes first if it is in the move set. The set itself is
        # unchanged, so the value of the node is the same; only the work differs.
        # A killer wall forces the wall generation up front (membership in the pruned
        # set needs the full ranking). Measured alternative "good pawn moves first, then
        # the killer wall" (report 0035): +25..48% nodes and slower at d5-d6, so not used.
        good, other = self._pawn_order(state, pawns)
        killer = self._killers[ply]
        walls: list[tuple[int, WallMove, GameState]] | None = None
        first: list[tuple[Move, GameState]] = []
        skip_wall: WallMove | None = None
        # The TT move (best move stored for this very state) goes first. It came from
        # this state's own move set, which depends on the state only, so a wall needs no
        # candidate generation. On a full 64-bit key collision (~2**-64) the move may be
        # foreign to this state, so a TT wall gets the whole is_wall_legal check: wall_fits
        # plus a path for both players on the child masks (cached BFS, TT move only).
        if tt_move is not None:
            if isinstance(tt_move, PawnMove):
                if tt_move in good:
                    good.remove(tt_move)
                    first.append((tt_move, apply_move_unchecked(state, tt_move)))
                elif tt_move in other:
                    other.remove(tt_move)
                    first.append((tt_move, apply_move_unchecked(state, tt_move)))
            elif state.walls_left[state.turn] > 0 and wall_fits(state, tt_move):
                cm = add_wall(self.masks(state), tt_move.x, tt_move.y, tt_move.orient)
                me = state.turn
                if (
                    self.dist_m(cm, state.pawns[me], me) is not None
                    and self.dist_m(cm, state.pawns[1 - me], 1 - me) is not None
                ):
                    child = apply_move_unchecked(state, tt_move)
                    self._store_masks((child.h_walls, child.v_walls), cm)
                    first.append((tt_move, child))
                    skip_wall = tt_move
        if killer is not None and killer != tt_move:
            if isinstance(killer, PawnMove):
                if killer in good:
                    good.remove(killer)
                    first.append((killer, apply_move_unchecked(state, killer)))
                elif killer in other:
                    other.remove(killer)
                    first.append((killer, apply_move_unchecked(state, killer)))
            elif state.walls_left[state.turn] > 0:
                walls = self.candidate_walls(state)
                for j, (_, w, c) in enumerate(walls):
                    if w == killer:
                        first.append((w, c))
                        del walls[j]
                        break
        best = -INF
        best_move: Move | None = None
        # Children of a depth-1 node are leaves (depth 0): they never probe or store the
        # table, so their key is not needed.
        need_key = tt is not None and depth > 1
        for mv, child in self._children(state, first, good, walls, other, skip_wall):
            ck = child_key(key, state, mv) if need_key else 0
            v = -self._negamax(child, depth - 1, -beta, -alpha, ply + 1, ck)
            if v > best:
                best = v
                best_move = mv
                if v > alpha:
                    alpha = v
                    if alpha >= beta:
                        self._killers[ply] = mv
                        break
        if best == -INF:  # boxed in, no useful wall: treat as quiet
            best = self.evaluate(state)
            bound = EXACT
        else:
            bound = UPPER if best <= alpha0 else LOWER if best >= beta else EXACT
        if tt is not None:
            tt[key & self._tt_mask] = (key, depth, _to_tt(best, ply), bound, best_move)
        return best

    def search(
        self,
        state: GameState,
        max_depth: int | None = None,
        time_limit: float | None = None,
    ) -> SearchResult:
        """Best move for state.turn. At least depth 1 is always completed.

        `time_limit` is a hard limit for depths >= 2; a new iteration is not started
        after half of it is used (it would most likely not finish). The memo caches are
        freed on return, so nothing big is held between moves.
        """
        try:
            return self._search(state, max_depth, time_limit)
        finally:
            self._dist_cache = {}
            self._mask_cache = {}
            self._path_cache = {}
            self._tt = None
            self._deadline = None

    def _search(
        self, state: GameState, max_depth: int | None, time_limit: float | None
    ) -> SearchResult:
        if winner(state) is not None:
            raise ValueError("no legal moves (terminal position)")
        if max_depth is None and time_limit is None:
            raise ValueError("need max_depth or time_limit")
        limit_depth = MAX_DEPTH if max_depth is None else max(1, max_depth)
        t0 = time.perf_counter()
        self.nodes = 0
        self._dist_cache = {}
        self._killers = [None] * (MAX_DEPTH + 2)
        self._mask_cache = {}
        self._path_cache = {}
        self._deadline = None
        use_tt = self.tt_bits > 0 and max(state.walls_left) < _ZMAX_WALLS
        self._tt = {} if use_tt else None  # lazily filled slots, at most 2**tt_bits

        pawns = pawn_moves(state)
        goal = goal_row(state.turn)
        for m in pawns:
            if m.row == goal:
                return SearchResult(m, WIN - 1, 1, 1, time.perf_counter() - t0)

        root = self.ordered_moves(state, pawns)
        if not root:  # boxed pawn and no useful wall: fall back to any legal move
            root = [(m, apply_move_unchecked(state, m)) for m in legal_moves(state)]
        # Seeded tie-break: a random rank per root move; sorts use it after the score.
        order = list(range(len(root)))
        self._rng.shuffle(order)
        prio = {i: rank for rank, i in enumerate(order)}
        idx = list(range(len(root)))
        rk = zobrist(state) if use_tt else 0
        keys = [child_key(rk, state, m) if use_tt else 0 for m, _ in root]
        best_move: Move = root[0][0]
        best_score = -INF
        done_depth = 0
        partial = False

        for depth in range(1, limit_depth + 1):
            if depth >= 2 and time_limit is not None:
                if time.perf_counter() - t0 > time_limit / 2:
                    break
                self._deadline = t0 + time_limit
            alpha = -INF
            iter_best_i = -1
            scores = [-INF] * len(root)
            try:
                for i in idx:
                    v = -self._negamax(root[i][1], depth - 1, -INF, -alpha, 1, keys[i])
                    scores[i] = v  # exact if > alpha, otherwise an upper bound
                    if iter_best_i < 0 or v > alpha:
                        alpha = v
                        iter_best_i = i
            except SearchTimeout:
                # The first move (previous best) was fully searched and every later
                # improvement beat it at this depth, so the partial result is usable.
                if iter_best_i >= 0:
                    best_move, best_score = root[iter_best_i][0], scores[iter_best_i]
                    partial = done_depth > 0
                break
            done_depth = depth
            # Next iteration: best first; ties keep the seeded order.
            idx.sort(key=lambda i: (-scores[i], prio[i]))
            if depth == 1:
                iter_best_i = idx[0]  # depth-1 scores are exact: tie-break by seed
            best_move, best_score = root[iter_best_i][0], scores[iter_best_i]
            if is_win_score(best_score) or len(root) == 1:
                break
        self._deadline = None
        return SearchResult(
            best_move, best_score, done_depth, self.nodes, time.perf_counter() - t0, partial
        )
