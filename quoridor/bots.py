"""Simple bots: random and greedy. Pure: no I/O, no global state (own random.Random)."""

from __future__ import annotations

import random
from typing import Protocol

from quoridor.paths import shortest_path, shortest_path_length
from quoridor.rules import apply_move, is_wall_legal, legal_moves, pawn_moves
from quoridor.state import (
    Cell,
    GameState,
    H,
    Move,
    PawnMove,
    V,
    WallMove,
    goal_row,
)


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
            if w in seen or not (0 <= w.x < 8 and 0 <= w.y < 8):
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
            sc = self._score(apply_move(state, m), me)
            if best_pawn_score is None or sc > best_pawn_score:
                best_pawn_score, best_pawn = sc, [m]
            elif sc == best_pawn_score:
                best_pawn.append(m)

        best_wall: list[WallMove] = []
        best_wall_score: int | None = None
        for w in candidate_walls(state):
            sc = self._score(apply_move(state, w), me)
            if best_wall_score is None or sc > best_wall_score:
                best_wall_score, best_wall = sc, [w]
            elif sc == best_wall_score:
                best_wall.append(w)

        assert best_pawn_score is not None
        if best_wall_score is not None and best_wall_score > best_pawn_score:
            return self._rng.choice(best_wall)
        return self._rng.choice(best_pawn)
