"""Move generation, legality and application. Pure and deterministic."""

from __future__ import annotations

from quoridor.paths import DIRS, has_path_to_goal
from quoridor.state import (
    BLACK,
    BOARD_SIZE,
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
    return pawn_moves_for(state, state.turn)


def pawn_moves_for(state: GameState, player: int) -> list[PawnMove]:
    """Pawn destinations for `player` as if it were their move (turn is ignored)."""
    c, r = state.pawns[player]
    opp = state.pawns[1 - player]
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
        return GameState(
            state.pawns, state.h_walls | {(w.x, w.y)}, state.v_walls, state.walls_left, state.turn
        )
    return GameState(
        state.pawns, state.h_walls, state.v_walls | {(w.x, w.y)}, state.walls_left, state.turn
    )


def _touched(state: GameState, px: int, py: int) -> bool:
    """True if the grid point (px, py) (0..9) lies on the border or on any placed wall."""
    if px <= 0 or py <= 0 or px >= BOARD_SIZE or py >= BOARD_SIZE:
        return True
    h, v = state.h_walls, state.v_walls
    return (
        (px, py - 1) in h
        or (px - 1, py - 1) in h
        or (px - 2, py - 1) in h
        or (px - 1, py) in v
        or (px - 1, py - 1) in v
        or (px - 1, py - 2) in v
    )


def _cannot_cut(state: GameState, w: WallMove) -> bool:
    """Cheap sufficient condition for "this wall cannot disconnect anyone".

    A cut is a curve of wall segments from border to border (or a cycle). The new wall
    is part of it only if at least two of its three grid points (start, middle, end)
    touch the border or another wall. Valid for states where both players still have
    a path (always true for states reached by legal play).
    """
    x, y = w.x, w.y
    if w.orient == H:
        pts = ((x, y + 1), (x + 1, y + 1), (x + 2, y + 1))
    else:
        pts = ((x + 1, y), (x + 1, y + 1), (x + 1, y + 2))
    n = 0
    for px, py in pts:
        if _touched(state, px, py):
            n += 1
            if n == 2:
                return False
    return True


def wall_fits(state: GameState, w: WallMove) -> bool:
    """Cheap legality part: on board, walls left, no overlap/crossing. No path check."""
    if w.orient not in (H, V):
        return False
    if not (0 <= w.x < WALL_GRID and 0 <= w.y < WALL_GRID):
        return False
    if state.walls_left[state.turn] <= 0:
        return False
    return not _overlaps(state, w)


def is_wall_legal(state: GameState, w: WallMove) -> bool:
    """Full legality of placing wall `w` for the side to move.

    Checks: orientation, on board, walls left, no overlap/crossing, and that after the
    wall both players still have a path to their goal row.

    Precondition: in `state` BOTH players already have a path to their goal (always true
    for states reached by legal play). The path check is shortcut by a topological test
    (`_cannot_cut`) that relies on it. For an artificially cut-off state (e.g. built from
    a misrecognised screen) a fitting wall far from the cut may be reported legal, while
    a full BFS would reject it. Validate such states with `has_path_to_goal` first.
    """
    if not wall_fits(state, w):
        return False
    if _cannot_cut(state, w):
        return True
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
        a, b = state.pawns
        pawns = (move.cell, b) if p == 0 else (a, move.cell)
        return GameState(pawns, state.h_walls, state.v_walls, state.walls_left, 1 - p)
    a, b = state.walls_left
    left = (a - 1, b) if p == 0 else (a, b - 1)
    if move.orient == H:
        h, v = state.h_walls | {(move.x, move.y)}, state.v_walls
    else:
        h, v = state.h_walls, state.v_walls | {(move.x, move.y)}
    return GameState(state.pawns, h, v, left, 1 - p)
