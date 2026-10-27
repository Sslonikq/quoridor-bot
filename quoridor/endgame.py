"""Exact endgame solver (phase 9, step 5): a proven win / loss with the mate distance.

When few walls are left (the bots call it with <= 2 walls per side) the game is searched
to the end: iterative deepening of a depth-limited negamax over ALL legal moves (every
pawn move and every legal wall: no pruned candidate set, so a proven result is exact)
with alpha-beta and a transposition table.

* Values are node-relative mate distances: MATE - n = the side to move wins with its move
  on ply n (n = 1: a winning step now), -(MATE - n) = it loses on ply n, 0 = no forced
  result within the depth. With iterative deepening the first depth that proves the root
  gives the shortest forced win, or the longest defence of a lost position.
* Exact cuts only:
  - a side can reach its goal within its k moves only if its BFS distance d <= k + J,
    where J counts its moves that can be jumps (a jump needs the pawns adjacent, and
    every ply changes their Manhattan distance by at most 2); walls only lengthen paths;
  - the earliest possible win / loss bounds the window (mate distance pruning);
  - TT entries are used only where they are valid for the asked depth (see _probe).
* Walls: a wall touching no edge on a shortest path of either player changes neither
  distance, so it is legal without a BFS; the others are checked by BFS (an unreachable
  goal = illegal) and ordered by the net change of the two distances.
* Move order (deterministic): the TT move, then the steps along the shortest path (first
  when we are not behind in the race), walls by net gain, other pawn moves, walls that
  change nothing.

Two cores with the same results (tests/search/test_endgame.py compares them node for
node): "jit" = quoridor.kernels.eg_search (numba), "py" = this module (the reference;
also the fallback without numba or with QUORIDOR_NO_NUMBA=1). They share the TT layout,
the Zobrist-like key (splitmix64 of the wall sets and of the pawns / stock / turn word),
the always-replace policy and the move order, so even the node counts agree.

Pure and deterministic for a node budget (no clock): solve(..., max_nodes=N). With a time
limit the node budget of each iteration is estimated from the speed so far.
"""

from __future__ import annotations

import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass

from quoridor import jit as _jit
from quoridor.bitpaths import GOAL_MASK, build_masks, m8, py_distance, py_shortest_layers
from quoridor.bitpaths import wall_bits as _wall_bits
from quoridor.eval import WIN
from quoridor.rules import pawn_moves, winner
from quoridor.search import _PAWN_TABLE
from quoridor.state import GameState, H, Move, PawnMove, WallMove, goal_row

_K = _jit.kernels()
if _K is not None:
    import numpy as np

MATE = 1000  # node-relative mate unit (a win on ply n = MATE - n)
INF = 30000
EXACT, LOWER, UPPER = 0, 1, 2
TT_BITS = 20  # 2**20 entries, 17 bytes each (~18 MB)
MAX_DEPTH = 160  # plies; deeper is never needed with <= 2 walls each
MAX_PLY = MAX_DEPTH + 2
WALL_MOVE = 128  # move code: 0..80 a pawn step to that cell, 128 + wall code a wall
CORES = ("jit", "py")

# move order keys (the same in the kernel)
K_TT = 100_000
K_STEP_AHEAD = 6_000
K_STEP_BEHIND = 2_500
K_OTHER_PAWN = 500
K_WALL_GAIN = 2_000  # + 1000 per step of net gain (> 0)
K_WALL_NONE = -1_000  # + 100 per step of net gain (<= 0, the wall changes a distance)
K_WALL_QUIET = -2_000  # the wall touches no shortest path

M64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_MIX1 = 0xBF58476D1CE4E5B9
_MIX2 = 0x94D049BB133111EB
_C_V = 0x5851F42D4C957F2D
_C_S = 0x2545F4914F6CDD1D

# (x, y, orient, wall_bits) per wall code ((y * 8 + x) * 2 + o) and its first cell index
_WALLS = tuple(
    ((p % 8), (p // 8), H if o == 0 else "v", _wall_bits(p % 8, p // 8, H if o == 0 else "v"))
    for p in range(64)
    for o in (0, 1)
)
_WALL_CELL = tuple((p // 8) * 9 + p % 8 for p in range(64) for _o in (0, 1))
_NOT_BITS = tuple(tuple(~b for b in w[3]) for w in _WALLS)


def _mix(x: int) -> int:
    z = (x + _GOLDEN) & M64
    z = ((z ^ (z >> 30)) * _MIX1) & M64
    z = ((z ^ (z >> 27)) * _MIX2) & M64
    return z ^ (z >> 31)


def tt_key(hb: int, vb: int, u0: int, u1: int, w0: int, w1: int, p: int) -> int:
    """Key of a position (quoridor.kernels._eg_key): never 0 (0 = an empty slot)."""
    small = u0 | (u1 << 7) | (w0 << 14) | (w1 << 18) | (p << 22)
    k = _mix(hb) ^ _mix(vb ^ _C_V) ^ _mix(small ^ _C_S)
    return k or 1


def adj(s: int) -> int:
    """A child's value (child's side) as the parent's value."""
    if s > 0:
        return -s + 1
    if s < 0:
        return -s - 1
    return 0


def inv(v: int) -> int:
    """The child value s with adj(s) == v (for the window); +-INF map to -+INF."""
    if v >= INF:
        return -INF
    if v <= -INF:
        return INF
    if v > 0:
        return -(v + 1)
    if v < 0:
        return 1 - v
    return 0


def can_reach(d: int, k: int, manhattan: int, t0: int) -> bool:
    """Can a pawn at BFS distance d reach its goal within its next k moves, made on the
    plies t0, t0 + 2, ...? A non-jump move gains at most 1, a jump at most 2, and a jump
    needs the pawns adjacent: on ply t their Manhattan distance is >= manhattan - 2 t."""
    if k <= 0:
        return False
    # moves i with 2 (t0 + 2 i) >= manhattan - 1 can be jumps
    need = manhattan - 1 - 2 * t0  # 4 i >= need
    i0 = 0 if need <= 0 else (need + 3) // 4
    j = k - i0 if k > i0 else 0
    return d <= k + j


@dataclass(frozen=True)
class SolveResult:
    move: Move | None  # the solver's move (None if unknown)
    plies: int  # +n: wins with its move on ply n; -n: loses on ply n; 0: unknown
    depth: int  # deepest completed iteration (plies)
    nodes: int
    elapsed: float

    @property
    def proven(self) -> bool:
        return self.plies != 0

    @property
    def score(self) -> int:
        """Search scale (eval.WIN - ply, as search.py / mcts.py mate scores); 0 unknown."""
        if self.plies > 0:
            return WIN - self.plies
        if self.plies < 0:
            return -(WIN + self.plies)
        return 0

    @property
    def moves_to_end(self) -> int:
        """Moves of the winner until the game ends (own moves for a win, the opponent's
        for a loss); 0 if unknown."""
        n = abs(self.plies)
        return (n + 1) // 2 if self.plies > 0 else n // 2


def plies_of_score(score: int) -> int:
    """SolveResult.plies of a search-scale mate score (WIN - n / -(WIN - n))."""
    return WIN - score if score > 0 else -(WIN + score)


def mate_text(plies: int) -> str:
    """Russian hint text of a proven result for the side to move."""
    if plies > 0:
        k = (plies + 1) // 2
        return f"выигрыш за {k} {_moves_word(k)}"
    k = -plies // 2
    return f"проигрыш: соперник выигрывает за {k} {_moves_word(k)}"


def _moves_word(k: int) -> str:
    if k % 10 == 1 and k % 100 != 11:
        return "ход"
    if 2 <= k % 10 <= 4 and not 12 <= k % 100 <= 14:
        return "хода"
    return "ходов"


def _move_of(code: int) -> Move:
    if code < WALL_MOVE:
        return PawnMove(code % 9, code // 9)
    x, y, o, _ = _WALLS[code - WALL_MOVE]
    return WallMove(x, y, o)


def _fits(hb: int, vb: int, w: int) -> bool:
    """quoridor.kernels._fits."""
    p = w >> 1
    x, y = p % 8, p // 8
    if hb >> p & 1 or vb >> p & 1:
        return False
    if w & 1 == 0:
        return not ((x > 0 and hb >> (p - 1) & 1) or (x < 7 and hb >> (p + 1) & 1))
    return not ((y > 0 and vb >> (p - 8) & 1) or (y < 7 and vb >> (p + 8) & 1))


def _edges(lay: list[int]) -> tuple[int, int]:
    """(vertical, horizontal) edges on the shortest paths of the layers: bit a of the
    first = the edge a - a+9, of the second = a - a+1."""
    ev = eh = 0
    for k in range(len(lay) - 1):
        a, b = lay[k], lay[k + 1]
        ev |= (a & (b >> 9)) | (b & (a >> 9))
        eh |= (a & (b >> 1)) | (b & (a >> 1))
    return ev, eh


class _PyCore:
    """The reference search (quoridor.kernels.eg_search is its port)."""

    def __init__(self, bits: int) -> None:
        size = 1 << bits
        self.shift = 64 - bits
        self.tk = array("Q", bytes(8 * size))
        self.tv = array("i", bytes(4 * size))
        self.td = array("h", bytes(2 * size))
        self.tf = array("b", bytes(size))
        self.tm = array("h", bytes(2 * size))
        self.nodes = 0
        self.limit = 0
        self.aborted = False

    def root(self, m, hb, vb, u0, u1, w0, w1, p, r, limit) -> tuple[int, int, int, bool]:
        """(value, best move code or -1, nodes, aborted) of a depth-r search."""
        self.nodes = 0
        self.limit = limit
        self.aborted = False
        v = self._search(m, hb, vb, u0, u1, w0, w1, p, r, -INF, INF)
        if self.aborted:
            return 0, -1, self.nodes, True
        key = tt_key(hb, vb, u0, u1, w0, w1, p)
        i = key >> self.shift
        best = self.tm[i] if self.tk[i] == key else -1
        return v, best, self.nodes, False

    def _search(self, m, hb, vb, u0, u1, w0, w1, p, r, alpha, beta) -> int:
        self.nodes += 1
        if self.nodes > self.limit:
            self.aborted = True
            return 0
        if r <= 0:
            return 0
        if p == 0:
            u, o, goal = u0, u1, 8
        else:
            u, o, goal = u1, u0, 0
        pm = []
        for d, n, j, s1, t1, s2, t2 in _PAWN_TABLE[u]:
            if not m[d] >> u & 1:
                continue
            if n != o:
                pm.append(n)
            elif m[d] >> n & 1:
                pm.append(j)
            else:
                if m[s1] >> n & 1:
                    pm.append(t1)
                if m[s2] >> n & 1:
                    pm.append(t2)
        for c in pm:
            if c // 9 == goal:
                return MATE - 1
        if r == 1:
            return 0
        q = 1 - p
        mywalls = w0 if p == 0 else w1
        lp = py_shortest_layers(m, 1 << u, GOAL_MASK[p])
        assert lp is not None
        d_me = len(lp) - 1
        if mywalls:
            lo = py_shortest_layers(m, 1 << o, GOAL_MASK[q])
            assert lo is not None
            d_op = len(lo) - 1
        else:
            lo = None
            d_op = py_distance(m, 1 << o, GOAL_MASK[q])
            assert d_op is not None
        man = abs(u % 9 - o % 9) + abs(u // 9 - o // 9)
        can_me = can_reach(d_me, (r + 1) // 2, man, 0)
        can_op = can_reach(d_op, r // 2, man, 1)
        if not can_me and not can_op:
            return 0
        hi = MATE - 3 if can_me else 0
        lo_b = -(MATE - 2) if can_op else 0
        if hi <= alpha:
            return hi
        if lo_b >= beta:
            return lo_b
        if alpha < lo_b:
            alpha = lo_b
        if beta > hi:
            beta = hi
        # transposition table
        key = tt_key(hb, vb, u0, u1, w0, w1, p)
        idx = key >> self.shift
        ttm = -1
        if self.tk[idx] == key:
            tv, td, tf = self.tv[idx], self.td[idx], self.tf[idx]
            ttm = self.tm[idx]
            if tf == EXACT:
                if tv != 0:
                    return tv if MATE - abs(tv) <= r else 0
                if r <= td:
                    return 0
            elif tf == LOWER:
                if (tv <= 0 and r <= td) or (tv > 0 and r >= td):
                    if tv >= beta:
                        return tv
                    if tv > alpha:
                        alpha = tv
            elif (tv >= 0 and r <= td) or (tv < 0 and r >= td):
                if tv <= alpha:
                    return tv
                if tv < beta:
                    beta = tv
        # moves: (key, code)
        moves = []
        l1 = lp[1] if d_me >= 1 else 0
        l2 = lp[2] if d_me >= 2 else 0
        step = K_STEP_AHEAD if d_me <= d_op else K_STEP_BEHIND
        for c in pm:
            b = 1 << c
            if b & l1:
                k = step + 100
            elif b & l2:
                k = step + 200
            else:
                k = K_OTHER_PAWN
            moves.append((K_TT if c == ttm else k, c))
        if mywalls:
            assert lo is not None
            evp, ehp = _edges(lp)
            evo, eho = _edges(lo)
            for w in range(128):
                if not _fits(hb, vb, w):
                    continue
                i = _WALL_CELL[w]
                if w & 1 == 0:
                    tp = evp >> i & 3
                    to = evo >> i & 3
                else:
                    tp = (ehp >> i | ehp >> (i + 9)) & 1
                    to = (eho >> i | eho >> (i + 9)) & 1
                if tp or to:
                    nb = _NOT_BITS[w]
                    m2 = (m[0] & nb[0], m[1] & nb[1], m[2] & nb[2], m[3] & nb[3])
                    d1 = d_me
                    if tp:
                        d1 = py_distance(m2, 1 << u, GOAL_MASK[p])
                        if d1 is None:
                            continue
                    d2 = d_op
                    if to:
                        d2 = py_distance(m2, 1 << o, GOAL_MASK[q])
                        if d2 is None:
                            continue
                    g = (d2 - d_op) - (d1 - d_me)
                    k = K_WALL_GAIN + 1000 * g if g > 0 else K_WALL_NONE + 100 * g
                else:
                    k = K_WALL_QUIET
                code = WALL_MOVE + w
                moves.append((K_TT if code == ttm else k, code))
        if not moves:
            return 0
        moves.sort(key=lambda t: -t[0])  # stable: equal keys keep the generation order
        a0 = alpha
        best = -INF
        bm = -1
        for _, c in moves:
            if c < WALL_MOVE:
                if p == 0:
                    s = self._search(m, hb, vb, c, u1, w0, w1, 1, r - 1, inv(beta), inv(alpha))
                else:
                    s = self._search(m, hb, vb, u0, c, w0, w1, 0, r - 1, inv(beta), inv(alpha))
            else:
                w = c - WALL_MOVE
                nb = _NOT_BITS[w]
                m2 = (m[0] & nb[0], m[1] & nb[1], m[2] & nb[2], m[3] & nb[3])
                if w & 1 == 0:
                    hb2, vb2 = hb | (1 << (w >> 1)), vb
                else:
                    hb2, vb2 = hb, vb | (1 << (w >> 1))
                v0, v1 = (w0 - 1, w1) if p == 0 else (w0, w1 - 1)
                s = self._search(m2, hb2, vb2, u0, u1, v0, v1, q, r - 1, inv(beta), inv(alpha))
            if self.aborted:
                return 0
            v = adj(s)
            if v > best:
                best = v
                bm = c
                if v > alpha:
                    alpha = v
                    if alpha >= beta:
                        break
        flag = LOWER if best >= beta else (UPPER if best <= a0 else EXACT)
        self.tk[idx] = key
        self.tv[idx] = best
        self.td[idx] = r
        self.tf[idx] = flag
        self.tm[idx] = bm
        return best


class _JitCore:
    """quoridor.kernels.eg_search with the TT as numpy arrays."""

    def __init__(self, bits: int) -> None:
        assert _K is not None
        size = 1 << bits
        self.shift = 64 - bits
        self.tk = np.zeros(size, dtype=np.uint64)
        self.tv = np.zeros(size, dtype=np.int32)
        self.td = np.zeros(size, dtype=np.int16)
        self.tf = np.zeros(size, dtype=np.int8)
        self.tm = np.zeros(size, dtype=np.int16)
        self.ctr = np.zeros(4, dtype=np.int64)
        self.mvbuf = np.zeros((MAX_PLY + 2, 256), dtype=np.int64)
        self.kbuf = np.zeros((MAX_PLY + 2, 256), dtype=np.int64)
        self.lay = np.zeros((4, 82), dtype=np.uint64)
        self.sm = np.zeros((MAX_PLY + 2, 10), dtype=np.uint64)
        self.si = np.zeros((MAX_PLY + 2, _K.EG_FIELDS), dtype=np.int64)
        self.sk = np.zeros(MAX_PLY + 2, dtype=np.uint64)

    def root(self, m, hb, vb, u0, u1, w0, w1, p, r, limit) -> tuple[int, int, int, bool]:
        assert _K is not None
        w = m8(m)
        v = _K.eg_search(
            np.uint64(w[0]), np.uint64(w[1]), np.uint64(w[2]), np.uint64(w[3]),
            np.uint64(w[4]), np.uint64(w[5]), np.uint64(w[6]), np.uint64(w[7]),
            np.uint64(hb), np.uint64(vb), u0, u1, w0, w1, p, r, limit, self.shift,
            self.tk, self.tv, self.td, self.tf, self.tm, self.ctr, self.mvbuf, self.kbuf,
            self.lay, self.sm, self.si, self.sk,
        )  # fmt: skip
        nodes, aborted, best = int(self.ctr[0]), bool(self.ctr[2]), int(self.ctr[3])
        if aborted:
            return 0, -1, nodes, True
        return int(v), best, nodes, False


def _encode(state: GameState) -> tuple:
    m = build_masks(state.h_walls, state.v_walls)
    hb = vb = 0
    for x, y in state.h_walls:
        hb |= 1 << (y * 8 + x)
    for x, y in state.v_walls:
        vb |= 1 << (y * 8 + x)
    (c0, r0), (c1, r1) = state.pawns
    return m, hb, vb, r0 * 9 + c0, r1 * 9 + c1


class SolverCancelled(Exception):
    """solve() stopped by should_stop()."""


CHUNK = {"jit": 1 << 18, "py": 1 << 11}  # nodes per kernel call with a clock (~0.1-0.2 s)


class EndgameSolver:
    """Iterative deepening over the exact search; keeps its TT between calls (entries
    are keyed by the full position, so they stay valid).

    core: "jit", "py" or None = "jit" when numba is on."""

    def __init__(self, tt_bits: int = TT_BITS, core: str | None = None) -> None:
        if core is not None and core not in CORES:
            raise ValueError(f"EndgameSolver: core must be one of {CORES}, not {core!r}")
        if core == "jit" and _K is None:
            raise ValueError(f"EndgameSolver: the jit core is not available ({_jit.REASON})")
        self.core = core or ("jit" if _K is not None else "py")
        self.tt_bits = tt_bits
        self._impl: _PyCore | _JitCore | None = None

    def _core(self) -> _PyCore | _JitCore:
        if self._impl is None:
            self._impl = _JitCore(self.tt_bits) if self.core == "jit" else _PyCore(self.tt_bits)
        return self._impl

    def solve(
        self,
        state: GameState,
        time_limit: float | None = None,
        max_nodes: int | None = None,
        max_depth: int = MAX_DEPTH,
        *,
        should_stop: Callable[[], bool] | None = None,
        chunk: int | None = None,
    ) -> SolveResult:
        """Search `state` until it is proven, the depth reaches max_depth, or the node /
        time budget runs out (then plies = 0).

        With a clock (time_limit or should_stop) every kernel call gets at most `chunk`
        nodes (default CHUNK of the core); a call cut by its chunk is repeated on the
        same depth (the TT keeps the finished subtrees), so the clock and should_stop()
        are checked every chunk. should_stop() true raises SolverCancelled. Without a
        clock and chunk: one call per depth, deterministic for max_nodes (the same in both
        cores; so is any fixed chunk)."""
        if winner(state) is not None:
            raise ValueError("terminal position")
        t0 = time.perf_counter()
        me = state.turn
        for mv in pawn_moves(state):  # a win now is not stored in the TT: answer it here
            if mv.row == goal_row(me):
                return SolveResult(mv, 1, 1, 1, time.perf_counter() - t0)
        m, hb, vb, u0, u1 = _encode(state)
        core = self._core()
        w0, w1 = state.walls_left
        deadline = None if time_limit is None else t0 + time_limit
        if chunk is None and (deadline is not None or should_stop is not None):
            chunk = CHUNK[self.core]
        total = 0
        done = 0
        for r in range(1, max(1, min(max_depth, MAX_DEPTH)) + 1):
            while True:
                if should_stop is not None and should_stop():
                    raise SolverCancelled
                if deadline is not None and time.perf_counter() >= deadline:
                    return SolveResult(None, 0, done, total, time.perf_counter() - t0)
                limit = (1 << 62) if max_nodes is None else max_nodes - total
                if chunk is not None:
                    limit = min(limit, chunk)
                if limit <= 0:
                    return SolveResult(None, 0, done, total, time.perf_counter() - t0)
                v, code, nodes, aborted = core.root(m, hb, vb, u0, u1, w0, w1, me, r, limit)
                total += min(nodes, limit)
                if not aborted:
                    break
                if chunk is None:  # the node budget is spent
                    return SolveResult(None, 0, done, total, time.perf_counter() - t0)
            done = r
            if v != 0:
                n = MATE - abs(v)
                move = _move_of(code) if code >= 0 else None
                plies = n if v > 0 else -n
                return SolveResult(move, plies, r, total, time.perf_counter() - t0)
        return SolveResult(None, 0, done, total, time.perf_counter() - t0)
