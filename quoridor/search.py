"""Alpha-beta (negamax) with iterative deepening.

* Moves: all pawn moves + a pruned set of walls. Candidate walls block an edge of the
  opponent's current shortest path (any wall that lengthens that path must do so); of
  those only walls with a positive gain (opponent path grows more than ours) are kept,
  best `wall_limit` by gain. Legality of every kept wall is checked by rules.is_wall_legal.
  Exception: if the opponent threatens to win with its next pawn move, the candidates are
  exactly the walls that stop that win, whatever their gain (a defensive wall often
  lengthens our own path too, so its gain is <= 0).
* Ordering: previous iteration's scores at the root; inside the tree pawn moves that
  shorten our path first, then walls by gain, then other pawn moves.
* Win/loss: a position where the previous mover reached its goal is lost for the side
  to move; a side that can step onto its goal row wins at once (WIN - ply, so faster
  wins / slower losses are preferred).
* Distances are memoised per search: (walls, pawn cell, player) -> BFS length, so a leaf
  evaluation usually reuses the BFS done while ordering its parent's moves.
* Limits: fixed depth (deterministic, for the arena/tests) and/or a time budget. On
  timeout the result of the last completed iteration (or a fully searched improvement
  from the current one) is returned. Root ties are broken by a seeded shuffle.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace

from quoridor.eval import WALL, WIN, evaluate_dists
from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import (
    apply_move_unchecked,
    is_wall_legal,
    legal_moves,
    pawn_moves,
    winner,
)
from quoridor.state import WALL_GRID, Cell, GameState, H, Move, PawnMove, V, WallMove, goal_row

INF = WIN * 2
MAX_DEPTH = 64
DEFAULT_WALL_LIMIT = 10
_CHECK_EVERY = 64  # nodes between clock checks


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
    return [m for m in pawn_moves(replace(state, turn=op)) if m.row == goal]


class Searcher:
    """One searcher per bot; state between calls: only the RNG (for root tie-breaks)."""

    def __init__(
        self,
        seed: int = 0,
        wall_limit: int = DEFAULT_WALL_LIMIT,
        wall_weight: int = WALL,
    ) -> None:
        self._rng = random.Random(seed)
        self.wall_limit = wall_limit
        self.wall_weight = wall_weight
        self._dist_cache: dict[tuple, int | None] = {}
        self.nodes = 0
        self._deadline: float | None = None

    # ---------- helpers ----------

    def dist(self, state: GameState, player: int) -> int | None:
        key = (state.h_walls, state.v_walls, state.pawns[player], player)
        d = self._dist_cache.get(key, -1)
        if d == -1:
            d = shortest_path_length(state, player)
            self._dist_cache[key] = d
        return d

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
        path = shortest_path(state, 1 - me)
        if not path:
            return []
        d_me = self.dist(state, me)
        d_op = len(path) - 1
        assert d_me is not None
        seen: set[WallMove] = set()
        scored: list[tuple[int, WallMove, GameState]] = []
        for a, b in zip(path, path[1:], strict=False):
            for w in edge_walls(a, b):
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if (w.x, w.y) in state.h_walls or (w.x, w.y) in state.v_walls:
                    continue  # occupied centre: illegal either way, skip the BFS
                child = apply_move_unchecked(state, w)
                op_after = self.dist(child, 1 - me)
                if op_after is None or op_after <= d_op:
                    continue
                me_after = self.dist(child, me)
                if me_after is None:
                    continue
                gain = (op_after - d_op) - (me_after - d_me)
                if gain <= 0:
                    continue
                scored.append((gain, w, child))
        scored.sort(key=lambda t: -t[0])
        out: list[tuple[int, WallMove, GameState]] = []
        for item in scored:
            if is_wall_legal(state, item[1]):
                out.append(item)
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
        d_me = self.dist(state, me)
        d_op = self.dist(state, 1 - me)
        assert d_me is not None and d_op is not None
        seen: set[WallMove] = set()
        scored: list[tuple[int, WallMove, GameState]] = []
        for a, b in edges:
            for w in edge_walls(a, b):
                if w in seen or not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
                    continue
                seen.add(w)
                if (w.x, w.y) in state.h_walls or (w.x, w.y) in state.v_walls:
                    continue
                child = apply_move_unchecked(state, w)
                op_goal = goal_row(1 - me)
                if any(pm.row == op_goal for pm in pawn_moves(child)):
                    continue  # the opponent still wins at once
                if not is_wall_legal(state, w):
                    continue
                op_after = self.dist(child, 1 - me)
                me_after = self.dist(child, me)
                assert op_after is not None and me_after is not None
                scored.append(((op_after - d_op) - (me_after - d_me), w, child))
        scored.sort(key=lambda t: -t[0])
        return scored

    def ordered_moves(
        self, state: GameState, pawns: list[PawnMove] | None = None
    ) -> list[tuple[Move, GameState]]:
        me = state.turn
        if pawns is None:
            pawns = pawn_moves(state)
        d_me = self.dist(state, me)
        assert d_me is not None
        good: list[tuple[int, Move, GameState]] = []
        other: list[tuple[int, Move, GameState]] = []
        for m in pawns:
            child = apply_move_unchecked(state, m)
            d = self.dist(child, me)
            assert d is not None
            (good if d < d_me else other).append((d, m, child))
        good.sort(key=lambda t: t[0])
        other.sort(key=lambda t: t[0])
        walls = self.candidate_walls(state)
        return (
            [(m, c) for _, m, c in good]
            + [(w, c) for _, w, c in walls]
            + [(m, c) for _, m, c in other]
        )

    # ---------- tree ----------

    def _negamax(self, state: GameState, depth: int, alpha: int, beta: int, ply: int) -> int:
        self.nodes += 1
        if (
            self._deadline is not None
            and self.nodes % _CHECK_EVERY == 0
            and time.perf_counter() > self._deadline
        ):
            raise SearchTimeout
        if winner(state) is not None:  # the previous mover has reached its goal
            return -(WIN - ply)
        pawns = pawn_moves(state)
        goal = goal_row(state.turn)
        for m in pawns:
            if m.row == goal:
                return WIN - ply - 1
        if depth <= 0:
            return self.evaluate(state)
        children = self.ordered_moves(state, pawns)
        if not children:  # boxed in, no useful wall: treat as quiet
            return self.evaluate(state)
        best = -INF
        for _, child in children:
            v = -self._negamax(child, depth - 1, -beta, -alpha, ply + 1)
            if v > best:
                best = v
                if v > alpha:
                    alpha = v
                    if alpha >= beta:
                        break
        return best

    def search(
        self,
        state: GameState,
        max_depth: int | None = None,
        time_limit: float | None = None,
    ) -> SearchResult:
        """Best move for state.turn. At least depth 1 is always completed.

        `time_limit` is a hard limit for depths >= 2; a new iteration is not started
        after half of it is used (it would most likely not finish).
        """
        if winner(state) is not None:
            raise ValueError("no legal moves (terminal position)")
        if max_depth is None and time_limit is None:
            raise ValueError("need max_depth or time_limit")
        limit_depth = MAX_DEPTH if max_depth is None else max(1, max_depth)
        t0 = time.perf_counter()
        self.nodes = 0
        self._dist_cache = {}
        self._deadline = None

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
                    v = -self._negamax(root[i][1], depth - 1, -INF, -alpha, 1)
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
