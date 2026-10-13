"""Move generation, legality and application. Pure and deterministic."""

from __future__ import annotations

from dataclasses import replace

from quoridor.paths import DIRS, has_path_to_goal
from quoridor.state import (
    BLACK,
    WALL_GRID,
    WHITE,
    GameState,
    H,
    Move,
    PawnMove,
    V,
    WallMove,
    goal_row,
    in_board,
)


def winner(state: GameState) -> int | None:
    for p in (WHITE, BLACK):
        if state.pawns[p][1] == goal_row(p):
            return p
    return None


def is_terminal(state: GameState) -> bool:
    return winner(state) is not None


def pawn_moves(state: GameState) -> list[PawnMove]:
    """Legal pawn destinations for the side to move (steps, jumps, diagonals)."""
    c, r = state.pawns[state.turn]
    opp = state.pawns[1 - state.turn]
    out: list[PawnMove] = []
    for dc, dr in DIRS:
        nc, nr = c + dc, r + dr
        if not in_board(nc, nr) or state.blocked(c, r, dc, dr):
            continue
        if (nc, nr) != opp:
            out.append(PawnMove(nc, nr))
            continue
        jc, jr = nc + dc, nr + dr
        if in_board(jc, jr) and not state.blocked(nc, nr, dc, dr):
            out.append(PawnMove(jc, jr))
            continue
        # straight jump impossible (wall or edge behind opponent): diagonals
        for sc, sr in ((dr, dc), (-dr, -dc)):
            tc, tr = nc + sc, nr + sr
            if in_board(tc, tr) and not state.blocked(nc, nr, sc, sr):
                out.append(PawnMove(tc, tr))
    return out


def _overlaps(state: GameState, w: WallMove) -> bool:
    x, y = w.x, w.y
    if w.orient == H:
        return (
            (x, y) in state.h_walls
            or (x - 1, y) in state.h_walls
            or (x + 1, y) in state.h_walls
            or (x, y) in state.v_walls
        )
    return (
        (x, y) in state.v_walls
        or (x, y - 1) in state.v_walls
        or (x, y + 1) in state.v_walls
        or (x, y) in state.h_walls
    )


def _with_wall(state: GameState, w: WallMove) -> GameState:
    if w.orient == H:
        return replace(state, h_walls=state.h_walls | {(w.x, w.y)})
    return replace(state, v_walls=state.v_walls | {(w.x, w.y)})


def is_wall_legal(state: GameState, w: WallMove) -> bool:
    if w.orient not in (H, V):
        return False
    if not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
        return False
    if state.walls_left[state.turn] <= 0:
        return False
    if _overlaps(state, w):
        return False
    nxt = _with_wall(state, w)
    return has_path_to_goal(nxt, WHITE) and has_path_to_goal(nxt, BLACK)


def wall_moves(state: GameState) -> list[WallMove]:
    if state.walls_left[state.turn] <= 0:
        return []
    return [
        w
        for o in (H, V)
        for x in range(WALL_GRID)
        for y in range(WALL_GRID)
        if is_wall_legal(state, w := WallMove(x, y, o))
    ]


def legal_moves(state: GameState) -> list[Move]:
    if is_terminal(state):
        return []
    return [*pawn_moves(state), *wall_moves(state)]


def is_legal_move(state: GameState, move: Move) -> bool:
    if is_terminal(state):
        return False
    if isinstance(move, PawnMove):
        return move in pawn_moves(state)
    return is_wall_legal(state, move)


def apply_move(state: GameState, move: Move) -> GameState:
    """Apply a move; raises ValueError if illegal."""
    if not is_legal_move(state, move):
        raise ValueError(f"illegal move: {move}")
    return apply_move_unchecked(state, move)


def apply_move_unchecked(state: GameState, move: Move) -> GameState:
    """Apply a move WITHOUT legality checks. For internal callers (bots, search) that
    already took the move from legal generators; illegal input gives undefined results."""
    p = state.turn
    if isinstance(move, PawnMove):
        pawns = list(state.pawns)
        pawns[p] = move.cell
        return replace(state, pawns=(pawns[0], pawns[1]), turn=1 - p)
    nxt = _with_wall(state, move)
    left = list(state.walls_left)
    left[p] -= 1
    return replace(nxt, walls_left=(left[0], left[1]), turn=1 - p)
