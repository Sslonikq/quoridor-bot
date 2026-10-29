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
* numba (phase 9, step 6): with quoridor.jit.ENABLED the BFS, _race_cands, the
  combination threats and candidate_walls run as compiled kernels (quoridor.kernels),
  with exactly the results of the Python code; pawn moves come from the wall masks
  (pawn_moves_m, the moves and order of rules.pawn_moves_for).
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
* Proven loss at the root (report 0053): every move loses, so the plain search would
  return any of the slowest losses. Instead all root moves and every legal wall that
  lengthens the opponent's path (not only the pruned set) are re-checked with a null
  window at the same depth; among those that lose no faster (or do not lose) the move
  with the largest (opponent - own) distance, then opponent distance, is played. Only
  proven-lost positions are affected.
* search_root: scores of all (or the best `top`) root moves with one shared window (the
  `top`-th best exact score so far) and one TT; search_top: the ordinary search for the
  best move plus a cheap search_root of the other root moves for the analyzer's top-N.
* Displayed scores: the static eval ignores the tempo of the side to move, so the root
  score swings by about one step between odd and even depths. display_score() adds half a
  step (TEMPO) for the side to move at the leaves; move choice is not affected.
* v4 (report 0061; options, off for v1-v3): eval.evaluate_v4 with wall_race when only one
  side has walls left; PVS (null window after the first move, also at the root) and late
  move reductions (moves from the lmr-th one on, one ply less at depth >= LMR_DEPTH, re-
  searched on a fail high); delay_slack > 0: the practical choice in a proven-lost
  position (_practical_loss). reserve > 0 (report 0066): eval.reserve_term, the
  opponent's wall stock as a threat to our path while we have 0-2 walls ourselves.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from quoridor import bitpaths
from quoridor.bitpaths import GOAL_MASK, WALL_NOT_OF_CODE, Masks, add_wall, bit, build_masks, m8
from quoridor.eval import (
    PLAN_V4,
    RACE_MIN_GAIN_V4,
    RESERVE_SHARE,
    SOFT_V4,
    STEP,
    WALL,
    WIN,
    _reserve_threat,
    evaluate_dists,
    evaluate_v4,
)
from quoridor.rules import (
    apply_move_unchecked,
    legal_moves,
    pawn_moves,
    pawn_moves_for,
    wall_fits,
    winner,
)
from quoridor.state import (
    WALL_GRID,
    WHITE,
    Cell,
    GameState,
    H,
    Move,
    PawnMove,
    V,
    WallMove,
    goal_row,
)
from quoridor.threat import combo_threat

INF = WIN * 2
MAX_DEPTH = 64
DEFAULT_WALL_LIMIT = 10
# Memo caches are cleared when they reach this many entries (distance cache; masks and
# paths get a quarter each), so memory stays bounded however long the search runs.
DEFAULT_CACHE_LIMIT = 300_000
_CHECK_EVERY = 64  # nodes between clock checks
LMR_DEPTH = 3  # late move reductions only at nodes with at least this remaining depth
_NO_NODE_LIMIT = 1 << 62
# Move index from which LMR reduces when it is off: never reached (no node has that many
# children), review 0064 nit.
_NO_LMR = MAX_DEPTH * 1000
# _delay_loss without a clock (fixed depth): at most this many times the nodes of the
# main search (and at least _DELAY_MIN_NODES), so a proven-lost position costs <= ~2x.
_DELAY_NODE_SHARE = 1.0
_DELAY_MIN_NODES = 2_000
V2_TT_BITS = 18  # 2**18 = 262 144 entries (bounded memory, see report 0036)
# v5 (phase 9): combined wall threats (quoridor.threat) and repetitions
COMBO_K = 4  # at most this many walls in a combination
COMBO_BEAM = 4  # beam width of the combination search
COMBO_COST = 100  # centi-steps per wall of a combination (the move spent on it)
COMBO_CACHE_LIMIT = 20_000  # combo memo entries (kept between searches), cleared when full
RESERVE_K = 7  # reserve_combo: the opponent's combination of at most this many walls
REP_WINDOW = 8  # rep_penalty looks at this many last own positions of the game
# rep_penalty, proven loss (_practical_loss): a move that does not go back to a recent own
# position may lose this many plies faster than delay_slack allows
REP_SLACK = 4

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

    @property
    def display_score(self) -> int:
        """score without the odd/even depth swing (see display_score())."""
        return display_score(self.score, self.depth + (1 if self.partial else 0))


TEMPO = STEP // 2  # half a step: the side to move is half a step ahead in a race


def display_score(score: int, depth: int) -> int:
    """Root `score` of a `depth`-ply search with the tempo of the leaf's side to move.

    The leaves are at ply `depth`: for odd depths the opponent of the root is to move
    there, and the static eval does not credit it with its tempo, so the root score is
    about half a step too high; for even depths half a step too low (measured: the start
    position gives +100 / 0 at odd / even depths, game02 move 19 +500 / +400). Mate
    scores are exact and unchanged. For display only: the search itself never uses it.
    """
    if is_win_score(score):
        return score
    return score - TEMPO if depth % 2 else score + TEMPO


@dataclass(frozen=True)
class RootScore:
    """One root move scored by search_root / search_top (side-to-move view)."""

    move: Move
    score: int  # exact if `exact`, otherwise an upper bound
    depth: int  # plies from the root (the root move included)
    exact: bool


@dataclass(frozen=True)
class RootSearch:
    scores: list[RootScore]  # exact scores best first, then upper bounds (best first)
    depth: int  # last fully completed depth (0: nothing completed, scores empty)
    nodes: int
    elapsed: float


@dataclass(frozen=True)
class TopResult:
    """search_top: the ordinary best move plus up to top_n - 1 alternatives.

    `alts` carry display scores (display_score, never above the best move's one, and
    never above the upper bound the main search proved for that move): comparable with
    best.display_score. Upper-bound entries (exact=False) fill the list only if fewer
    exact scores were found.
    """

    best: SearchResult
    alts: list[RootScore]
    alt_depth: int  # completed depth of the alternatives search (0: not run)
    nodes: int
    elapsed: float


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


def _edge_wall_table() -> dict[tuple[int, int], tuple[tuple, ...]]:
    """(i, j) bit indexes (row * 9 + col) of adjacent cells -> in-grid walls blocking that
    edge, as (x, y, orient, the four "not wall_bits" masks) for wall_race (no WallMove
    objects in its inner loop)."""
    out: dict[tuple[int, int], tuple[tuple, ...]] = {}
    for c in range(9):
        for r in range(9):
            for dc, dr in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nc, nr = c + dc, r + dr
                if not (0 <= nc < 9 and 0 <= nr < 9):
                    continue
                ws = []
                for w in edge_walls((c, r), (nc, nr)):
                    if 0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID:
                        b = bitpaths.wall_bits(w.x, w.y, w.orient)
                        ws.append((w.x, w.y, w.orient, ~b[0], ~b[1], ~b[2], ~b[3]))
                out[r * 9 + c, nr * 9 + nc] = tuple(ws)
    return out


_EDGE_WALLS = _edge_wall_table()
# numba kernels (quoridor.jit; None: pure Python) and the WallMove of each kernel wall code
_K = bitpaths._K
_WALL_MOVES = tuple(WallMove(x, y, o) for x, y, o in bitpaths.WALL_OF_CODE)
_PAWN_MOVES = tuple(PawnMove(i % 9, i // 9) for i in range(81))  # by bit index


def _pawn_table() -> tuple[tuple[tuple[int, int, int, int, int, int, int], ...], ...]:
    """Per bit index u: (d, n, jump, side1, t1, side2, t2) for the directions d (up, down,
    right, left = the order of paths.DIRS and of the masks) with an on-board neighbour n:
    the straight jump target and the two diagonal sides (mask index, target) in the
    order of rules.pawn_moves_for. Off-board targets are never reached: the mask bit of
    the step towards them is 0."""
    dirs = ((0, 1), (0, -1), (1, 0), (-1, 0))
    out = []
    for u in range(81):
        c, r = u % 9, u // 9
        row = []
        for d, (dc, dr) in enumerate(dirs):
            nc, nr = c + dc, r + dr
            if not (0 <= nc < 9 and 0 <= nr < 9):
                continue
            jc, jr = nc + dc, nr + dr
            sides = []
            for sc, sr in ((dr, dc), (-dr, -dc)):
                sides += [dirs.index((sc, sr)), (nr + sr) * 9 + nc + sc]
            j = jr * 9 + jc if 0 <= jc < 9 and 0 <= jr < 9 else -1
            row.append((d, nr * 9 + nc, j, *sides))
        out.append(tuple(row))
    return tuple(out)


_PAWN_TABLE = _pawn_table()


def pawn_moves_m(m: Masks, cell: Cell, opp: Cell) -> list[PawnMove]:
    """rules.pawn_moves_for on the wall masks: the same moves in the same order (a mask
    bit is set exactly when the step is on the board and not blocked by a wall)."""
    u = cell[1] * 9 + cell[0]
    o = opp[1] * 9 + opp[0]
    out = []
    for d, n, j, s1, t1, s2, t2 in _PAWN_TABLE[u]:
        if not m[d] >> u & 1:
            continue
        if n != o:
            out.append(_PAWN_MOVES[n])
        elif m[d] >> n & 1:
            out.append(_PAWN_MOVES[j])
        else:
            if m[s1] >> n & 1:
                out.append(_PAWN_MOVES[t1])
            if m[s2] >> n & 1:
                out.append(_PAWN_MOVES[t2])
    return out


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
        v4: int = 0,
        pvs: int = 0,
        lmr: int = 0,
        delay_slack: int = 0,
        threat: int = 0,
        root_defense: int = 0,
        reserve: int = 0,
        combo: int = 0,
        combo_k: int = COMBO_K,
        combo_beam: int = COMBO_BEAM,
        combo_cost: int = COMBO_COST,
        combo_root: int = 0,
        combo_stock: int = 0,
        combo_exact: int = 0,
        combo_narrow: int = 0,
        rep: int = 0,
        rep_penalty: int = 0,
        progress: int = 0,
        race_plan: int = PLAN_V4,
        race_soft: int = SOFT_V4,
        reserve_combo: int = 0,
        reserve_k: int = RESERVE_K,
        reserve_min: int = 0,
    ) -> None:
        self._rng = random.Random(seed)
        # mcts3 (phase 9, report 0085): the reserve term on the opponent's combination
        # instead of its wall count. While a side has 0 / 1 / 2 walls, it loses
        # reserve_combo * RESERVE_SHARE[own] / 4 per step that the opponent's best
        # combination of at most min(its walls, reserve_k) walls adds to its path
        # (combo_gain; quoridor.threat on the root walls). 0: off. Seven walls that can
        # build a cage (game07) weigh much more than seven walls against a pawn in a
        # one-cell corridor (game03), where the flat reserve overrates them.
        # reserve_min: only while the opponent has at least this many walls (a big
        # stock); then this replaces the flat reserve term of that side, below it the
        # flat term stays (endgames with 1-2 walls each: report 0085).
        self.reserve_combo = reserve_combo
        self.reserve_k = max(1, reserve_k)
        self.reserve_min = max(1, reserve_min)
        # v5 (phase 9, step 3): combined wall threats (quoridor.threat), all off by default.
        # combo: per step of (our walls' combined potential against the opponent's path -
        # theirs against ours), see _combo_term; combo_k / combo_beam: the size of the
        # threat search; combo_cost: centi-steps a wall of the combination costs;
        # combo_root > 0: the root also tries our combination walls and walls that break
        # the opponent's best combinations (_combo_walls).
        self.combo = combo
        self.combo_k = max(1, combo_k)
        # levels of the memoised combination search (the first combo_k serve combo /
        # combo_root; a beam level does not depend on how many levels follow it)
        self.combo_levels = max(self.combo_k, self.reserve_k if reserve_combo else 0)
        self.combo_beam = max(1, combo_beam)
        self.combo_cost = combo_cost
        self.combo_root = combo_root
        # > 0: a side's wall stock is worth at most its potential + combo_stock per wall
        self.combo_stock = combo_stock
        # 1: the potential on the node's own walls (exact; else on the root's, cheaper)
        self.combo_exact = combo_exact
        # > 0: combination walls only where B's shortest paths are at most this wide
        self.combo_narrow = combo_narrow
        # Bounded memo of quoridor.threat.combo_threat, KEPT between searches: it is keyed
        # by the wall masks of a root position, and those rarely change from move to move.
        self._combo_cache: dict[tuple, tuple[int, tuple[int, ...], tuple]] = {}
        self._root_walls: tuple = (build_masks((), ()), frozenset(), frozenset())
        # v5 (phase 9, step 4): repetitions. rep: a position of the game history (or of
        # the current search path) met again inside the tree scores 0 (nobody progressed);
        # rep_penalty: centi-steps per earlier occurrence (in the last REP_WINDOW own
        # positions) of the key (own cell, own walls left) a root move leads to;
        # progress: equal root scores prefer the move with the best path difference
        # (then a pawn move), not the seeded random order. History: set_history().
        self.rep = rep
        self.rep_penalty = rep_penalty
        self.progress = progress
        self._hist_keys: frozenset[int] = frozenset()
        self._hist_own: tuple[list, list] = ([], [])
        self._path_keys: set[int] = set()
        self.wall_limit = wall_limit
        self.wall_weight = wall_weight
        self.v4 = v4  # 0: v1-v3 eval; 1: v4 eval (wall race when one side has no walls)
        self.pvs = bool(pvs)  # principal variation search (null windows after the 1st move)
        self.lmr = lmr if pvs else 0  # with pvs: reduce moves from this index on (0: off)
        # proven loss: 0 = the slowest loss (v3); > 0 = _practical_loss with this slack
        self.delay_slack = delay_slack
        # root only: our walls against the opponent's best root_defense walls (0: off)
        self.root_defense = root_defense
        # per step of (our best single wall - the opponent's), while both have walls
        self.threat = threat  # (v4: eval.THREAT_V4; 0: off)
        self._race_cache: dict[tuple, tuple[int, int]] = {}
        self._race_cands_cache: dict[tuple, tuple] = {}
        # v4 eval, one side out of walls (eval.evaluate_v4 plan / soft); options for tuning
        self.race_plan = race_plan
        self.race_soft = race_soft
        self.race_min_gain = RACE_MIN_GAIN_V4
        # v4 eval: per opponent wall when we have few walls (eval.reserve_term; 0: off)
        self.reserve = reserve
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
        # Node cap, checked only together with the deadline (see _delay_loss).
        self._node_limit = _NO_NODE_LIMIT
        # (root moves, child keys, scores, depth) of search()'s last completed iteration
        self._root_info: tuple | None = None
        # Optional progress callback: called with the depth of every completed iteration of
        # search() / search_top() (the analyzer's live depth); None: off.
        self.on_depth: Callable[[int], None] | None = None

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
            d = cache[key] = bitpaths.player_distance(m, cell, player)
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
        m = self.masks(state)
        d_me = self.dist_m(m, state.pawns[me], me)
        d_op = self.dist_m(m, state.pawns[1 - me], 1 - me)
        assert d_me is not None and d_op is not None
        wm, wo = state.walls_left[me], state.walls_left[1 - me]
        if self.v4:
            e_me, e_op = d_me, d_op
            # exactly one side (A) has walls left and does not win the pure race anyway
            # (then RACE decides and A's wall plan is not needed)
            if (wm == 0) != (wo == 0) and (d_op < d_me if wm else d_me <= d_op):
                if wo:  # only the opponent has walls: its plan against us
                    e_op, e_me = self.wall_race(state, 1 - me)
                else:
                    e_me, e_op = self.wall_race(state, me)
            score = evaluate_v4(
                d_me,
                d_op,
                wm,
                wo,
                e_me,
                e_op,
                plan=self.race_plan,
                soft=self.race_soft,
                wall_weight=self.wall_weight,
                reserve=self.reserve,
            )
            if self.threat and wm and wo:
                # both sides have walls: our best single wall against theirs
                h, v = state.h_walls, state.v_walls
                d, c, *_ = self._race_cands(m, h, v, state.pawns[1 - me], 1 - me)
                g_me = c[0][0] - d if c else 0
                d, c, *_ = self._race_cands(m, h, v, state.pawns[me], me)
                g_op = c[0][0] - d if c else 0
                score += self.threat * (g_me - g_op)
            if self.combo or self.combo_stock:
                score += self._combo_term(state, me, d_me, d_op, wm, wo)
            if self.reserve_combo:
                score += self._reserve_combo_term(state, me, d_me, d_op, wm, wo)
            return score
        return evaluate_dists(d_me, d_op, wm, wo, wall_weight=self.wall_weight)

    # ---------- v5: combined wall threats ----------

    def combo_of(self, m: Masks, h: frozenset, v: frozenset, cell: Cell, player: int) -> tuple:
        """quoridor.threat.combo_threat (combo_levels walls, combo_beam), memoised and
        kept between searches (bounded by COMBO_CACHE_LIMIT)."""
        key = (m, cell, player)
        cache = self._combo_cache
        hit = cache.get(key)
        if hit is None:
            hit = combo_threat(
                m, h, v, cell, player, self.combo_levels, self.combo_beam, self.combo_narrow
            )
            if len(cache) >= COMBO_CACHE_LIMIT:
                cache.clear()
            cache[key] = hit
        return hit

    def potential(
        self, cell: Cell, player: int, walls: int, d_now: int, state: GameState | None = None
    ) -> int:
        """Centi-steps the opponent's `walls` can still add to `player`'s path (>= 0):
        best over j <= walls of (j walls' gain * STEP - j * combo_cost).

        combo_exact (or no `state`): the combinations are searched on the walls of the
        ROOT position (one memo entry serves every node where the pawn stands on
        `cell`), minus what the walls placed inside the tree already took (d_now - the
        distance from `cell` at the root walls). With combo_exact and `state`: on the
        walls of `state` itself (exact, one memo entry per wall set and cell)."""
        if self.combo_exact and state is not None:
            m, h, v = self.masks(state), state.h_walls, state.v_walls
        else:
            m, h, v = self._root_walls
        d0, ds, _ = self.combo_of(m, h, v, cell, player)
        best = 0
        cost = self.combo_cost
        for j in range(min(walls, len(ds), self.combo_k)):
            val = (ds[j] - d0) * STEP - (j + 1) * cost
            if val > best:
                best = val
        if best <= 0:
            return 0
        best -= (d_now - d0) * STEP
        return best if best > 0 else 0

    def combo_gain(self, cell: Cell, player: int, walls: int, d_now: int) -> int:
        """Steps (>= 0) the best combination of at most min(walls, reserve_k) opponent
        walls adds to `player`'s path from `cell`: on the ROOT walls (as potential, one
        memo entry per cell), minus what the walls placed inside the tree already took
        (d_now: the distance on the node's walls). No per-wall cost: reserve_combo."""
        m, h, v = self._root_walls
        _, ds, _ = self.combo_of(m, h, v, cell, player)
        j = min(walls, len(ds), self.reserve_k)
        if j <= 0:
            return 0
        g = ds[j - 1] - d_now
        return g if g > 0 else 0

    def _reserve_combo_term(
        self, state: GameState, me: int, d_me: int, d_op: int, wm: int, wo: int
    ) -> int:
        """reserve_combo: while the opponent has >= reserve_min walls, the side with
        0 / 1 / 2 walls loses reserve_combo * RESERVE_SHARE[own] / 4 per step of the
        opponent's combination (combo_gain) instead of the flat reserve term (which
        evaluate_v4 added and is taken back here)."""
        r, lo, flat = self.reserve_combo, self.reserve_min, self.reserve
        score = 0
        if wo >= lo and wm < len(RESERVE_SHARE):
            g = self.combo_gain(state.pawns[me], me, wo, d_me)
            score += _reserve_threat(wm, wo, flat) - r * RESERVE_SHARE[wm] * g // 4
        if wm >= lo and wo < len(RESERVE_SHARE):
            g = self.combo_gain(state.pawns[1 - me], 1 - me, wm, d_op)
            score += r * RESERVE_SHARE[wo] * g // 4 - _reserve_threat(wo, wm, flat)
        return score

    def _combo_term(self, state: GameState, me: int, d_me: int, d_op: int, wm: int, wo: int) -> int:
        """combo per step of (our walls' potential against the opponent - theirs against
        us); with combo_stock > 0 also the wall stock capped by the potential: what a
        side's walls are worth in the v4 eval (wall_weight each + the reserve_term part)
        is capped at their potential + combo_stock per wall (walls that can lengthen
        nothing, e.g. against a pawn in a one-cell corridor, game03 move 17, are not worth
        a step and a quarter each)."""
        mine = self.potential(state.pawns[1 - me], 1 - me, wm, d_op, state) if wm else 0
        theirs = self.potential(state.pawns[me], me, wo, d_me, state) if wo else 0
        score = self.combo * (mine - theirs) // STEP if wm and wo else 0
        if self.combo_stock:
            # a side's walls in the v4 eval: wall_weight each + its reserve_term part
            ww, floor, r = self.wall_weight, self.combo_stock, self.reserve
            if wm:
                score -= max(0, ww * wm + _reserve_threat(wo, wm, r) - (mine + floor * wm))
            if wo:
                score += max(0, ww * wo + _reserve_threat(wm, wo, r) - (theirs + floor * wo))
        return score

    def _combo_walls(self, state: GameState, have: set[Move]) -> list[tuple[WallMove, GameState]]:
        """Root only (combo_root > 0): the walls of our best combinations against the
        opponent (they may lengthen nothing alone: the first walls of a cage), and walls
        that break the opponent's best combinations against us (the threat wall's square
        taken first, or a wall overlapping / crossing it, as in _defensive_walls), the
        latter only if they do not lengthen our path more than the opponent's. Every wall
        is checked for legality (wall_fits + both paths)."""
        me = state.turn
        if state.walls_left[me] <= 0:
            return []
        m = self.masks(state)
        h, v = state.h_walls, state.v_walls
        my_cell, op_cell = state.pawns[me], state.pawns[1 - me]
        d_me = self.dist_m(m, my_cell, me)
        d_op = self.dist_m(m, op_cell, 1 - me)
        assert d_me is not None and d_op is not None
        attack: list[WallMove] = []
        _, _, sets = self.combo_of(m, h, v, op_cell, 1 - me)
        for ws in sets[: min(state.walls_left[me], self.combo_k)]:
            attack += [WallMove(x, y, o) for x, y, o in ws]
        defend: list[WallMove] = []
        if state.walls_left[1 - me] > 0:
            _, _, sets = self.combo_of(m, h, v, my_cell, me)
            for ws in sets[: min(state.walls_left[1 - me], self.combo_k)]:
                for x, y, o in ws:
                    if o == H:
                        defend += [WallMove(x, y, H), WallMove(x, y, V)]
                        defend += [WallMove(x - 1, y, H), WallMove(x + 1, y, H)]
                    else:
                        defend += [WallMove(x, y, V), WallMove(x, y, H)]
                        defend += [WallMove(x, y - 1, V), WallMove(x, y + 1, V)]
        out: list[tuple[WallMove, GameState]] = []
        seen = set(have)
        for kind, walls in ((0, attack), (1, defend)):
            for w in walls:
                if len(out) >= self.combo_root:
                    return out
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if not wall_fits(state, w):
                    continue
                cm = add_wall(m, w.x, w.y, w.orient)
                me_after = self.dist_m(cm, my_cell, me)
                op_after = self.dist_m(cm, op_cell, 1 - me)
                if me_after is None or op_after is None:
                    continue
                if kind and me_after - d_me > op_after - d_op:
                    continue
                child = apply_move_unchecked(state, w)
                self._store_masks((child.h_walls, child.v_walls), cm)
                out.append((w, child))
        return out

    # ---------- v5: repetitions ----------

    def set_history(self, states: list[GameState]) -> None:
        """The positions of the game before the one to search (oldest first; the
        searched position itself may be included). Used only with rep / rep_penalty."""
        if not (self.rep or self.rep_penalty):
            return
        self._hist_keys = frozenset(zobrist(s) for s in states) if self.rep else frozenset()
        own: tuple[list, list] = ([], [])
        for s in states:
            p = s.turn
            own[p].append((s.pawns[p], s.walls_left[p]))
        self._hist_own = own

    def _root_penalties(self, state: GameState, root: list[tuple[Move, GameState]]) -> list[int]:
        me = state.turn
        recent = self._hist_own[me][-REP_WINDOW:]
        if not recent:
            return [0] * len(root)
        return [self.rep_penalty * recent.count((c.pawns[me], c.walls_left[me])) for _, c in root]

    def _progress_keys(self, state: GameState, root: list[tuple[Move, GameState]]) -> list:
        me = state.turn
        out = []
        for mv, c in root:
            d_me, d_op = self.dist(c, me), self.dist(c, 1 - me)
            assert d_me is not None and d_op is not None
            out.append((d_me - d_op, isinstance(mv, WallMove)))
        return out

    def wall_race(self, state: GameState, a: int) -> tuple[int, int]:
        """(moves A needs, moves B needs) after A's greedy wall plan.

        A = player `a` (it has walls), B = the other one. Greedy: while A has walls, place
        the legal-looking wall (it fits; blocking an edge of B's current shortest paths:
        any wall that lengthens them must) that lengthens B's path most; stop when the
        best one adds < race_min_gain steps (a wall costs A a move, so +1 wins nothing).
        A's pawn is ignored (its own detours and walls that would cut it off: rare), so
        the plan depends on the walls and B's pawn only and is shared by all positions of
        A's pawn; pawns stand still and jumps are ignored: an estimate, not a proof.
        Memoised per search (bounded like the distance cache).
        """
        n = state.walls_left[a]
        b = 1 - a
        b_cell = state.pawns[b]
        key = (state.h_walls, state.v_walls, b_cell, b, n)
        cache = self._race_cache
        hit = cache.get(key)
        if hit is None:
            m = self.masks(state)
            h = set(state.h_walls)
            v = set(state.v_walls)
            min_gain = self.race_min_gain
            used = 0
            while True:
                d_b, cands = self._race_cands(m, h, v, b_cell, b)
                if used >= n or not cands or cands[0][0] - d_b < min_gain:
                    break
                w = cands[0][1]
                m = add_wall(m, w[0], w[1], w[2])
                (h if w[2] == H else v).add((w[0], w[1]))
                used += 1
            hit = (used, d_b)
            if len(cache) >= self._small_limit:
                cache.clear()
            cache[key] = hit
        used, d_b = hit
        d_a = self.dist(state, a)
        assert d_a is not None
        return d_a + used, d_b

    def _race_cands(
        self,
        m: Masks,
        h: AbstractSet[tuple[int, int]],
        v: AbstractSet[tuple[int, int]],
        b_cell: Cell,
        b: int,
    ) -> tuple[int, list[tuple[int, tuple[int, int, str]]]]:
        """(B's distance, [(B's distance after the wall, (x, y, orient))]) for the walls
        that fit (h / v: the wall set of `m`) and lengthen B's path, by B's new distance
        (best first). Memoised by (m, b_cell, b). The child masks are not kept (report
        0073: they were most of the memo's memory); add_wall rebuilds them."""
        key = (m, b_cell, b)
        cache = self._race_cands_cache
        hit = cache.get(key)
        if hit is not None:
            return hit
        if _K is not None:  # numba: quoridor.kernels.race_cands, the same list
            r = _K.race_cands(*m8(m), b_cell[1] * 9 + b_cell[0], b).tolist()
            assert r[0] >= 0
            walls = bitpaths.WALL_OF_CODE
            res = (r[0], [(e >> 8, walls[e & 255]) for e in r[2:]])
            if len(cache) >= self._small_limit:
                cache.clear()
            cache[key] = res
            return res
        start, goal = bit(b_cell), GOAL_MASK[b]
        layers = bitpaths.shortest_layers(m, start, goal)
        assert layers is not None
        d_b = len(layers) - 1
        m0, m1, m2, m3 = m
        distance = bitpaths.distance
        seen: set[tuple[int, int, str]] = set()
        cands: list[tuple[int, tuple, tuple]] = []
        # A heuristic filter, checked by brute force (not proven; review 0064 nit): a wall
        # blocks two parallel edges, and we only try walls on the edges between two
        # consecutive layers that both have <= 2 cells (then two edges can cut them).
        # It can in principle miss a wall whose two edges lie in different layer pairs
        # (L_k -> L_k+1 and L_k+1 -> L_k+2): then only L_k+1 must have <= 2 cells.
        # Against all 128 walls: 0 misses on 7136 positions (review 0064) and on the
        # random positions of report 0073; widening the filter would change v4's play.
        for k in range(d_b):
            lk, lk1 = layers[k], layers[k + 1]
            if lk.bit_count() > 2 or lk1.bit_count() > 2:
                continue
            us = [i for i in range(81) if lk >> i & 1] if lk & (lk - 1) else [lk.bit_length() - 1]
            for u in us:
                for j, ok in ((u + 9, m0), (u - 9, m1), (u + 1, m2), (u - 1, m3)):
                    if not (ok >> u) & 1 or not (lk1 >> j) & 1:
                        continue
                    for x, y, o, n0, n1, n2, n3 in _EDGE_WALLS[u, j]:
                        w = (x, y, o)
                        if w in seen:
                            continue
                        seen.add(w)
                        if (x, y) in h or (x, y) in v:
                            continue
                        if o == H:
                            if (x - 1, y) in h or (x + 1, y) in h:
                                continue
                        elif (x, y - 1) in v or (x, y + 1) in v:
                            continue
                        cm = (m0 & n0, m1 & n1, m2 & n2, m3 & n3)
                        nb = distance(cm, start, goal)
                        if nb is not None and nb > d_b:
                            # ties: by the wall seen from B's side of the board, so that
                            # a position and its colour mirror get the same plan
                            yb = y if b == WHITE else WALL_GRID - 1 - y
                            cands.append((nb, (x, yb, o), w))
        cands.sort(key=lambda t: (-t[0], t[1]))
        res = (d_b, [(nb, w) for nb, _, w in cands])
        if len(cache) >= self._small_limit:
            cache.clear()
        cache[key] = res
        return res

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
        if _K is not None:
            return self._candidate_walls_jit(state, m, my_cell, op_cell)
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

    def _candidate_walls_jit(
        self, state: GameState, m: Masks, my_cell: Cell, op_cell: Cell
    ) -> list[tuple[int, WallMove, GameState]]:
        """candidate_walls (no threat) on quoridor.kernels.cand_walls: the same list. The
        distances after each kept wall go to the distance memo, as in the Python code."""
        me = state.turn
        r = _K.cand_walls(
            *m8(m), my_cell[1] * 9 + my_cell[0], op_cell[1] * 9 + op_cell[0], me, self.wall_limit
        ).tolist()
        n = r[0]
        assert n >= 0
        out: list[tuple[int, WallMove, GameState]] = []
        if not n:
            return out
        m0, m1, m2, m3 = m
        cache = self._dist_cache
        op = 1 - me
        for i in range(1, 4 * n + 1, 4):
            code = r[i + 1]
            w = _WALL_MOVES[code]
            _, _, _, n0, n1, n2, n3 = WALL_NOT_OF_CODE[code]
            cm = (m0 & n0, m1 & n1, m2 & n2, m3 & n3)
            child = apply_move_unchecked(state, w)
            self._store_masks((child.h_walls, child.v_walls), cm)
            if len(cache) >= self.cache_limit - 1:
                cache.clear()
            cache[(cm, op_cell, op)] = r[i + 2]
            cache[(cm, my_cell, me)] = r[i + 3]
            out.append((r[i], w, child))
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
            and (time.perf_counter() > self._deadline or self.nodes > self._node_limit)
        ):
            raise SearchTimeout
        if winner(state) is not None:  # the previous mover has reached its goal
            return -(WIN - ply)
        if self.rep and (key in self._path_keys or key in self._hist_keys):
            return 0  # v5: a repeated position, nobody progressed
        goal = goal_row(state.turn)
        if depth <= 0 and abs(state.pawns[state.turn][1] - goal) > 2:
            return self.evaluate(state)  # a step or a jump covers at most 2 rows: no win now
        me = state.turn
        pawns = pawn_moves_m(self.masks(state), state.pawns[me], state.pawns[1 - me])
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
        need_key = (tt is not None and depth > 1) or self.rep
        # PVS (v4): moves after the first one get a null window first, and with LMR the
        # moves from the lmr-th one on also one ply less; a fail-high is re-searched.
        pvs = self.pvs
        late = self.lmr if self.lmr and depth >= LMR_DEPTH else _NO_LMR
        i = 0
        rep = self.rep
        if rep:
            self._path_keys.add(key)
        try:
            for mv, child in self._children(state, first, good, walls, other, skip_wall):
                ck = child_key(key, state, mv) if need_key else 0
                if not pvs or i == 0:
                    v = -self._negamax(child, depth - 1, -beta, -alpha, ply + 1, ck)
                else:
                    r = 1 if i >= late else 0
                    v = -self._negamax(child, depth - 1 - r, -alpha - 1, -alpha, ply + 1, ck)
                    if v > alpha and (r or v < beta):
                        v = -self._negamax(child, depth - 1, -beta, -alpha, ply + 1, ck)
                i += 1
                if v > best:
                    best = v
                    best_move = mv
                    if v > alpha:
                        alpha = v
                        if alpha >= beta:
                            self._killers[ply] = mv
                            break
        finally:
            if rep:
                self._path_keys.discard(key)
        if best == -INF:  # boxed in, no useful wall: treat as quiet
            best = self.evaluate(state)
            bound = EXACT
        else:
            bound = UPPER if best <= alpha0 else LOWER if best >= beta else EXACT
        if tt is not None:
            tt[key & self._tt_mask] = (key, depth, _to_tt(best, ply), bound, best_move)
        return best

    # ---------- root ----------

    def _reset(self, state: GameState) -> None:
        """Fresh memo caches, killers and (if on) an empty TT for one search call."""
        self.nodes = 0
        self._dist_cache = {}
        self._mask_cache = {}
        self._path_cache = {}
        self._race_cache = {}
        self._race_cands_cache = {}
        self._killers = [None] * (MAX_DEPTH + 2)
        self._deadline = None
        self._node_limit = _NO_NODE_LIMIT
        use_tt = self.tt_bits > 0 and max(state.walls_left) < _ZMAX_WALLS
        self._tt = {} if use_tt else None  # lazily filled slots, at most 2**tt_bits
        self._root_info = None
        self._path_keys = {zobrist(state)} if self.rep else set()
        if self.combo or self.combo_root or self.combo_stock or self.reserve_combo:
            self._root_walls = (self.masks(state), state.h_walls, state.v_walls)

    def _free(self) -> None:
        """Drop everything big, so that nothing is held between calls."""
        self._dist_cache = {}
        self._mask_cache = {}
        self._path_cache = {}
        self._race_cache = {}
        self._race_cands_cache = {}
        self._tt = None
        self._deadline = None
        self._node_limit = _NO_NODE_LIMIT
        self._root_info = None

    def _keys(self, state: GameState, root: list[tuple[Move, GameState]]) -> list[int]:
        if self._tt is None and not self.rep:
            return [0] * len(root)
        rk = zobrist(state)
        return [child_key(rk, state, m) for m, _ in root]

    @staticmethod
    def _check_args(state: GameState, max_depth: int | None, time_limit: float | None) -> None:
        if winner(state) is not None:
            raise ValueError("no legal moves (terminal position)")
        if max_depth is None and time_limit is None:
            raise ValueError("need max_depth or time_limit")

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
            self._check_args(state, max_depth, time_limit)
            self._reset(state)
            return self._search(state, max_depth, time_limit)
        finally:
            self._free()

    def _search(
        self, state: GameState, max_depth: int | None, time_limit: float | None
    ) -> SearchResult:
        """Iterative deepening at the root (after _reset; the caches are kept).

        Leaves the root moves and their scores of the last completed iteration in
        self._root_info (for search_top).
        """
        limit_depth = MAX_DEPTH if max_depth is None else max(1, max_depth)
        t0 = time.perf_counter()
        pawns = pawn_moves(state)
        goal = goal_row(state.turn)
        for m in pawns:
            if m.row == goal:
                return SearchResult(m, WIN - 1, 1, 1, time.perf_counter() - t0)

        root = self._root_moves(state, pawns)
        # Seeded tie-break: a random rank per root move; sorts use it after the score.
        order = list(range(len(root)))
        self._rng.shuffle(order)
        if self.progress:  # v5: equal scores prefer progress (path difference), then pawns
            pk = self._progress_keys(state, root)
            rnd = {i: rank for rank, i in enumerate(order)}
            order.sort(key=lambda i: (pk[i], rnd[i]))
        prio = {i: rank for rank, i in enumerate(order)}
        # v5: root moves back to an own position seen lately cost rep_penalty per time
        pen = self._root_penalties(state, root) if self.rep_penalty else [0] * len(root)
        idx = list(range(len(root)))
        keys = self._keys(state, root)
        best_move: Move = root[0][0]
        best_score = -INF
        done_depth = 0
        done_scores: list[int] = []
        partial = False
        prev_best_i = -1  # best root move of the last completed iteration

        for depth in range(1, limit_depth + 1):
            if depth >= 2 and time_limit is not None:
                if time.perf_counter() - t0 > time_limit / 2:
                    break
                self._deadline = t0 + time_limit
            alpha = -INF
            iter_best_i = -1
            prev_done = prev_best_i < 0  # the previous best searched again at this depth
            scores = [-INF] * len(root)
            try:
                for i in idx:
                    p = pen[i]
                    a = alpha + p if alpha > -INF else alpha  # the window of the raw score
                    if self.pvs and iter_best_i >= 0:
                        v = -self._negamax(root[i][1], depth - 1, -a - 1, -a, 1, keys[i])
                        if v > a:  # fail high: the exact score
                            v = -self._negamax(root[i][1], depth - 1, -INF, -a, 1, keys[i])
                    else:
                        v = -self._negamax(root[i][1], depth - 1, -INF, -a, 1, keys[i])
                    if p and not is_win_score(v):
                        v -= p
                    scores[i] = v  # exact if > alpha, otherwise an upper bound
                    if iter_best_i < 0 or v > alpha:
                        alpha = v
                        iter_best_i = i
                    if i == prev_best_i:
                        prev_done = True
            except SearchTimeout:
                # Usable only once the previous best has been searched at this depth: the
                # best so far then beat it (or is it). The iteration usually starts with
                # it, but a fail-low move whose bound equals the best score may be
                # ordered first (ties: seeded order); a timeout right after such a move
                # gave a worse move than the completed iteration (report 0077).
                if iter_best_i >= 0 and prev_done:
                    best_move, best_score = root[iter_best_i][0], scores[iter_best_i]
                    partial = done_depth > 0
                break
            done_depth = depth
            done_scores = scores
            if self.on_depth is not None:
                self.on_depth(depth)
            # Next iteration: best first; ties keep the seeded order.
            idx.sort(key=lambda i: (-scores[i], prio[i]))
            if depth == 1:
                iter_best_i = idx[0]  # depth-1 scores are exact: tie-break by seed
            prev_best_i = iter_best_i
            best_move, best_score = root[iter_best_i][0], scores[iter_best_i]
            if is_win_score(best_score) or len(root) == 1:
                break
        if best_score <= -(WIN - 10_000):  # proven loss: play the longest resistance
            best_move, best_score = self._delay_loss_limited(
                state, root, keys, prio, done_depth, partial, best_move, best_score, t0, time_limit
            )
        self._deadline = None
        self._root_info = (root, keys, done_scores, done_depth)
        if self.rep_penalty and not is_win_score(best_score):
            # the reported score is the position's, without the repetition penalty
            # (a move from _delay_loss may lie outside the root set: no penalty there)
            best_score += next((pen[i] for i, (m, _) in enumerate(root) if m == best_move), 0)
        return SearchResult(
            best_move, best_score, done_depth, self.nodes, time.perf_counter() - t0, partial
        )

    def _delay_loss_limited(
        self,
        state: GameState,
        root: list[tuple[Move, GameState]],
        keys: list[int],
        prio: dict[int, int],
        done_depth: int,
        partial: bool,
        best_move: Move,
        best_score: int,
        t0: float,
        time_limit: float | None,
    ) -> tuple[Move, int]:
        """_delay_loss inside the move's budget (review 0057, minor 3).

        With a clock: skipped once its deadline t0 + time_limit has passed (also when
        only depth 1 ran and no deadline was set yet). Without one (fixed depth): a node
        cap of _DELAY_NODE_SHARE x the main search's nodes (>= _DELAY_MIN_NODES), so the
        result stays deterministic; on hitting it the candidates checked so far count.
        """
        if time_limit is not None:
            self._deadline = t0 + time_limit
            if time.perf_counter() >= self._deadline:
                return best_move, best_score
        else:
            self._deadline = float("inf")  # turns on the node-cap check in _negamax
            budget = max(_DELAY_MIN_NODES, int(self.nodes * _DELAY_NODE_SHARE))
            self._node_limit = self.nodes + budget
        try:
            sd = done_depth + (1 if partial else 0)
            return self._delay_loss(
                state, root, keys, prio, sd, best_move, best_score, exact_root=not partial
            )
        finally:
            self._node_limit = _NO_NODE_LIMIT

    def _root_moves(self, state: GameState, pawns: list[PawnMove]) -> list[tuple[Move, GameState]]:
        """The root set of search() and search_root(moves=None): ordered_moves, plus the
        defensive walls with root_defense > 0; a boxed pawn with no useful wall falls
        back to every legal move."""
        root = self.ordered_moves(state, pawns)
        if self.root_defense:
            root += self._defensive_walls(state, {m for m, _ in root})
        if self.combo_root:
            root += self._combo_walls(state, {m for m, _ in root})
        if not root:
            root = [(m, apply_move_unchecked(state, m)) for m in legal_moves(state)]
        return root

    def _defensive_walls(
        self, state: GameState, have: set[Move]
    ) -> list[tuple[WallMove, GameState]]:
        """Root only (root_defense > 0): our walls that take away the opponent's best
        walls against us (gain >= 2, the root_defense best ones): the wall overlaps or
        crosses the threat, so it no longer fits. Kept if it is legal, not in `have`
        and does not lengthen our path more than the opponent's. Such walls lengthen
        nothing themselves, so candidate_walls never offers them (game03: the fence
        on the 3rd row)."""
        me = state.turn
        if state.walls_left[me] <= 0 or state.walls_left[1 - me] <= 0:
            return []
        flipped = GameState(state.pawns, state.h_walls, state.v_walls, state.walls_left, 1 - me)
        threats = [w for g, w, _ in self.candidate_walls(flipped) if g >= 2]
        m = self.masks(state)
        my_cell, op_cell = state.pawns[me], state.pawns[1 - me]
        d_me = self.dist_m(m, my_cell, me)
        d_op = self.dist_m(m, op_cell, 1 - me)
        assert d_me is not None and d_op is not None
        out: list[tuple[WallMove, GameState]] = []
        seen = set(have)
        for t in threats[: self.root_defense]:
            x, y = t.x, t.y
            if t.orient == H:
                blockers = [WallMove(x - 1, y, H), WallMove(x + 1, y, H), WallMove(x, y, V)]
            else:
                blockers = [WallMove(x, y - 1, V), WallMove(x, y + 1, V), WallMove(x, y, H)]
            blockers.append(t)  # taking the square first
            for w in blockers:
                if w in seen or not wall_fits(state, w):
                    continue
                seen.add(w)
                cm = add_wall(m, w.x, w.y, w.orient)
                me_after = self.dist_m(cm, my_cell, me)
                op_after = self.dist_m(cm, op_cell, 1 - me)
                if me_after is None or op_after is None:
                    continue
                if me_after - d_me > op_after - d_op:
                    continue
                child = apply_move_unchecked(state, w)
                self._store_masks((child.h_walls, child.v_walls), cm)
                out.append((w, child))
        return out

    def _lengthening_walls(self, state: GameState) -> list[tuple[WallMove, GameState]]:
        """All legal walls that lengthen the opponent's shortest path (no gain filter or
        wall_limit, unlike candidate_walls). Such a wall must block an edge of every
        shortest path, so the edges of one path are enough."""
        me = state.turn
        if state.walls_left[me] <= 0:
            return []
        m = self.masks(state)
        my_cell, op_cell = state.pawns[me], state.pawns[1 - me]
        path = self.path_m(m, op_cell, 1 - me)
        if not path:
            return []
        d_op = len(path) - 1
        seen: set[WallMove] = set()
        out: list[tuple[WallMove, GameState]] = []
        for a, b in zip(path, path[1:], strict=False):
            for w in edge_walls(a, b):
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if not wall_fits(state, w):
                    continue
                cm = add_wall(m, w.x, w.y, w.orient)
                op_after = self.dist_m(cm, op_cell, 1 - me)
                if op_after is None or op_after <= d_op:
                    continue
                if self.dist_m(cm, my_cell, me) is None:
                    continue  # cuts us off: illegal
                child = apply_move_unchecked(state, w)
                self._store_masks((child.h_walls, child.v_walls), cm)
                out.append((w, child))
        return out

    def _delay_loss(
        self,
        state: GameState,
        root: list[tuple[Move, GameState]],
        keys: list[int],
        prio: dict[int, int],
        depth: int,
        best_move: Move,
        best_score: int,
        exact_root: bool = True,
    ) -> tuple[Move, int]:
        """The best resistance in a position proven lost at `depth` (best_score = loss).

        Candidates: the root moves plus every legal wall that lengthens the opponent's
        path. Each is checked with a null window at the same depth: a move that loses
        faster is dropped; one that is not worse than best_score gets its exact score by
        a re-search (v >= best_score from the null window is only a lower bound), except
        for root moves when `exact_root`: the main search at this depth already proved
        them <= best_score, so v == best_score is exact there. Among the rest: the
        latest loss (or the best score), then the largest (opponent - own) distance after
        the move, then the opponent's distance, then the seeded root order. On timeout
        the candidates checked so far are used (the searched best move is first).
        """
        me = state.turn
        best_i = next(i for i, (m, _) in enumerate(root) if m == best_move)
        cands: list[tuple[Move, GameState, int, int]] = [
            (best_move, root[best_i][1], keys[best_i], prio[best_i])
        ]
        cands += [(m, c, keys[i], prio[i]) for i, (m, c) in enumerate(root) if i != best_i]
        in_root = {m for m, _ in root}
        rk = zobrist(state) if self._tt is not None else 0
        extra = len(root)
        for w, child in self._lengthening_walls(state):
            if w not in in_root:
                k = child_key(rk, state, w) if self._tt is not None else 0
                cands.append((w, child, k, extra))
                extra += 1
        if self.delay_slack:
            return self._practical_loss(state, cands, depth, best_move, best_score)
        choice = (best_move, best_score)
        best_key: tuple[int, int, int, int] | None = None
        try:
            for j, (m, child, k, p) in enumerate(cands):
                if j == 0:
                    v = best_score
                else:
                    v = -self._negamax(child, depth - 1, -best_score, -(best_score - 1), 1, k)
                    if v < best_score:
                        continue  # loses faster
                    if v > best_score or not (exact_root and j < len(root)):
                        # v is a lower bound only: the exact score (a fail-low here
                        # means exactly best_score)
                        w = -self._negamax(child, depth - 1, -INF, -best_score, 1, k)
                        v = max(best_score, w)
                d_me, d_op = self.dist(child, me), self.dist(child, 1 - me)
                assert d_me is not None and d_op is not None
                key = (v, d_op - d_me, d_op, -p)
                if best_key is None or key > best_key:
                    best_key, choice = key, (m, v)
        except SearchTimeout:
            pass
        return choice

    def _practical_loss(
        self,
        state: GameState,
        cands: list[tuple[Move, GameState, int, int]],
        depth: int,
        best_move: Move,
        best_score: int,
    ) -> tuple[Move, int]:
        """v4's choice in a proven-lost position (delay_slack > 0, report 0061).

        Against a human a proven loss is not the end, but playing only for the slowest
        loss makes the pawn walk back and forth (game03, moves 21-33: retreating in front
        of the opponent so that it gets no jump). Here every candidate that loses at most
        delay_slack plies faster than the searched loss is kept (exact score by a
        re-search); a candidate that does not lose at all wins outright (best score).
        Among the kept losses: the wall that lengthens the opponent's path most, then
        the largest (opponent - own) distance (progress, no retreat), then the latest
        loss, then the opponent's distance and the seeded order. On timeout the
        candidates checked so far are used (the searched best move is first).
        """
        me = state.turn
        m0 = self.masks(state)
        op_now = self.dist_m(m0, state.pawns[1 - me], 1 - me)
        assert op_now is not None
        floor = best_score - self.delay_slack
        choice = (best_move, best_score)
        best_key: tuple | None = None
        # v5 (rep_penalty): going back to a recent own position is the last resort
        recent = self._hist_own[me][-REP_WINDOW:] if self.rep_penalty else []
        try:
            for j, (m, child, k, p) in enumerate(cands):
                reps = recent.count((child.pawns[me], child.walls_left[me])) if recent else 0
                # v5: a move that does not go back may lose up to REP_SLACK plies faster
                f = floor - REP_SLACK if recent and not reps else floor
                if j == 0:
                    v = best_score
                else:
                    v = -self._negamax(child, depth - 1, -f, -(f - 1), 1, k)
                    if v < f:
                        continue  # loses much faster
                    v = -self._negamax(child, depth - 1, -INF, -(f - 1), 1, k)
                d_me, d_op = self.dist(child, me), self.dist(child, 1 - me)
                assert d_me is not None and d_op is not None
                if is_win_score(v) and v < 0:
                    key: tuple = (0, d_op - op_now, -reps, d_op - d_me, v, d_op, -p)
                else:  # not lost: the best score decides
                    key = (1, v, -reps, d_op - d_me, 0, d_op, -p)
                if best_key is None or key > best_key:
                    best_key, choice = key, (m, v)
        except SearchTimeout:
            pass
        return choice

    def _root_scores(
        self,
        state: GameState,
        root: list[tuple[Move, GameState]],
        keys: list[int],
        max_depth: int | None,
        time_limit: float | None,
        top: int | None,
    ) -> RootSearch:
        """Iterative deepening over `root` with one shared window: alpha = the top-th
        best exact score of the iteration so far (-INF until there are `top`), so the
        best `top` moves get exact scores and the others upper bounds. Iteration 1 always
        completes; an unfinished iteration is dropped. Ties keep the order of `root`."""
        t0 = time.perf_counter()
        n = len(root)
        k = n if top is None else max(1, min(top, n))
        limit_depth = MAX_DEPTH if max_depth is None else max(1, max_depth)
        idx = list(range(n))
        done: list[RootScore] = []
        done_depth = 0
        for depth in range(1, limit_depth + 1):
            if depth >= 2 and time_limit is not None:
                if time.perf_counter() - t0 > time_limit / 2:
                    break
                self._deadline = t0 + time_limit
            scores = [-INF] * n
            exact = [False] * n
            best_k: list[int] = []  # exact scores of this iteration, best first, <= k
            try:
                for i in idx:
                    alpha = best_k[-1] if len(best_k) >= k else -INF
                    v = -self._negamax(root[i][1], depth - 1, -INF, -alpha, 1, keys[i])
                    scores[i] = v
                    if v > alpha:
                        exact[i] = True
                        best_k.append(v)
                        best_k.sort(reverse=True)
                        del best_k[k:]
            except SearchTimeout:
                break
            done_depth = depth
            idx.sort(key=lambda i: (not exact[i], -scores[i], i))
            done = [RootScore(root[i][0], scores[i], depth, exact[i]) for i in idx]
            if all(is_win_score(v) for v in best_k):
                break  # proven results do not change with depth
        self._deadline = None
        return RootSearch(done, done_depth, self.nodes, time.perf_counter() - t0)

    def search_root(
        self,
        state: GameState,
        max_depth: int | None = None,
        time_limit: float | None = None,
        moves: list[Move] | None = None,
        top: int | None = None,
    ) -> RootSearch:
        """Scores of the root moves, one shared window and TT (see _root_scores).

        moves=None: the root set of search() (_root_moves: pawn moves + pruned walls +
        with root_defense > 0 the defensive walls, review 0064 minor 5); otherwise
        exactly the given legal moves (any wall, e.g. a played move outside the pruned
        set; ValueError for an illegal one). top=None: every score exact; top=k: the
        best k exact, the others upper bounds (cheaper). Limits as in search().
        """
        try:
            self._check_args(state, max_depth, time_limit)
            self._reset(state)
            if moves is None:
                root = self._root_moves(state, pawn_moves(state))
            else:
                legal = set(legal_moves(state))
                for m in moves:
                    if m not in legal:
                        raise ValueError(f"illegal move {m!r}")
                root = [(m, apply_move_unchecked(state, m)) for m in dict.fromkeys(moves)]
                if not root:
                    raise ValueError("no moves to search")
            return self._root_scores(
                state, root, self._keys(state, root), max_depth, time_limit, top
            )
        finally:
            self._free()

    def search_top(
        self,
        state: GameState,
        time_limit: float | None = None,
        top_n: int = 3,
        alt_share: float = 0.1,
        max_depth: int | None = None,
    ) -> TopResult:
        """The best move by the ordinary search, plus up to top_n - 1 alternatives.

        The main search is exactly search() with the WHOLE time_limit (same moves, same
        scores, same iteration rule: so the same depth as SearchBot at that move time;
        phase 8, report 0061). It usually stops early (no new iteration after half the
        budget), and the rest of time_limit, but at least alt_share of it, goes to a
        search_root of the other root moves with top = top_n - 1, no deeper than the main
        search and with the TT / caches the main search left: the whole call takes at
        most about time_limit * (1 + alt_share). Alternative
        scores are display scores, capped by the main one and by the upper bound the main
        search proved for that move at its depth (so a move refuted there, e.g. by a
        proven loss, is not shown better than that). With LMR (v4) that bound is not
        strict: a late move searched one ply shallower inside the main search may get a
        too low null-window bound, and the cap then shows the alternative lower than an
        exact search of it at that depth would (review 0064 minor 5). The best move
        and its score stay exactly those of search().
        """
        t0 = time.perf_counter()
        try:
            self._check_args(state, max_depth, time_limit)
            self._reset(state)
            best = self._search(state, max_depth, time_limit)
            info = self._root_info
            alts: list[RootScore] = []
            alt_depth = 0
            if top_n > 1 and info is not None:
                root, keys, bounds, bdepth = info
                rest = [i for i in range(len(root)) if root[i][0] != best.move]
                if rest:
                    left = (
                        None
                        if time_limit is None
                        else max(alt_share * time_limit, time_limit - (time.perf_counter() - t0))
                    )
                    rs = self._root_scores(
                        state,
                        [root[i] for i in rest],
                        [keys[i] for i in rest],
                        best.depth,
                        left,
                        top_n - 1,
                    )
                    alt_depth = rs.depth
                    bound = {root[i][0]: bounds[i] for i in rest} if bounds else {}
                    cap = best.display_score
                    for r in rs.scores:
                        s = display_score(r.score, r.depth)
                        b = bound.get(r.move)
                        if b is not None:
                            s = min(s, display_score(b, bdepth))
                        alts.append(RootScore(r.move, min(s, cap), r.depth, r.exact))
                    alts.sort(key=lambda r: (not r.exact, -r.score))
                    del alts[top_n - 1 :]
            return TopResult(best, alts, alt_depth, self.nodes, time.perf_counter() - t0)
        finally:
            self._free()
