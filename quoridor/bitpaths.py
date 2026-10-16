"""Bitboard shortest paths for the search. Pure; same results as quoridor.paths.

A cell (col, row) is bit row * 9 + col of a Python int. For a wall set four "move
allowed" masks are built once (cells whose step up / down / right / left is on the board
and not blocked); a BFS layer is then a handful of big-int operations instead of a
Python loop over cells. quoridor.paths stays the reference implementation; tests in
tests/paths compare the two.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from quoridor.state import BOARD_SIZE, WHITE, Cell, H

N = BOARD_SIZE
ALL = (1 << (N * N)) - 1
ROW0 = (1 << N) - 1
COL0 = sum(1 << (r * N) for r in range(N))
UP0 = ALL & ~(ROW0 << (N * (N - 1)))  # every cell except the top row
DOWN0 = ALL & ~ROW0
RIGHT0 = ALL & ~(COL0 << (N - 1))
LEFT0 = ALL & ~COL0
GOAL_MASK = (ROW0 << (N * (N - 1)), ROW0)  # by player: WHITE -> row 9, BLACK -> row 1

Masks = tuple[int, int, int, int]  # (up, down, right, left) allowed moves
EMPTY_MASKS: Masks = (UP0, DOWN0, RIGHT0, LEFT0)


def bit(cell: Cell) -> int:
    return 1 << (cell[1] * N + cell[0])


def wall_bits(x: int, y: int, orient: str) -> Masks:
    """Bits a wall removes from (up, down, right, left); internal anchor (see state.py)."""
    i = y * N + x
    if orient == H:  # between rows y and y+1, columns x and x+1
        return (3 << i, 3 << (i + N), 0, 0)
    # between columns x and x+1, rows y and y+1
    return (0, 0, (1 << i) | (1 << (i + N)), (2 << i) | (2 << (i + N)))


def add_wall(m: Masks, x: int, y: int, orient: str) -> Masks:
    b = wall_bits(x, y, orient)
    return (m[0] & ~b[0], m[1] & ~b[1], m[2] & ~b[2], m[3] & ~b[3])


def build_masks(h_walls: Iterable[tuple[int, int]], v_walls: Iterable[tuple[int, int]]) -> Masks:
    up, down, right, left = EMPTY_MASKS
    for x, y in h_walls:
        b = 3 << (y * N + x)
        up &= ~b
        down &= ~(b << N)
    for x, y in v_walls:
        i = y * N + x
        b = (1 << i) | (1 << (i + N))
        right &= ~b
        left &= ~(b << 1)
    return (up, down, right, left)


def distance(m: Masks, start: int, goal: int) -> int | None:
    """BFS length from the start bit to any bit of `goal`, or None if unreachable."""
    if start & goal:
        return 0
    up, down, right, left = m
    seen = front = start
    d = 0
    while front:
        d += 1
        front = (
            ((front & up) << N)
            | ((front & down) >> N)
            | ((front & right) << 1)
            | ((front & left) >> 1)
        ) & ~seen
        if front & goal:
            return d
        seen |= front
    return None


def shortest_layers(m: Masks, start: int, goal: int) -> list[int] | None:
    """Cells on shortest paths from the start bit to `goal`, layer by layer.

    Returns [L_0, ..., L_d] (d = distance; L_0 = start, L_d = the goal cells reached at
    distance d): L_k is the set of cells at distance k from the start lying on some
    shortest path. None if the goal is unreachable. Edges are undirected, so the
    backward pass uses the same masks.
    """
    up, down, right, left = m
    fronts = [start]
    seen = front = start
    while not front & goal:
        front = (
            ((front & up) << N)
            | ((front & down) >> N)
            | ((front & right) << 1)
            | ((front & left) >> 1)
        ) & ~seen
        if not front:
            return None
        seen |= front
        fronts.append(front)
    layer = front & goal
    out = [layer]
    for k in range(len(fronts) - 2, -1, -1):
        layer = fronts[k] & (
            ((layer & up) << N)
            | ((layer & down) >> N)
            | ((layer & right) << 1)
            | ((layer & left) >> 1)
        )
        out.append(layer)
    out.reverse()
    return out


def player_distance(m: Masks, cell: Cell, player: int) -> int | None:
    return distance(m, bit(cell), GOAL_MASK[player])


def shortest_path(m: Masks, cell: Cell, player: int) -> list[Cell] | None:
    """Exactly the path quoridor.paths.shortest_path returns (same BFS order), or None."""
    goal = N - 1 if player == WHITE else 0
    if cell[1] == goal:
        return [cell]
    up, down, right, left = m
    start = cell[1] * N + cell[0]
    parent = {start: -1}
    queue = deque([start])
    pop, push = queue.popleft, queue.append
    while queue:
        i = pop()
        # paths.DIRS order: up, down, right, left
        for ok, j in ((up, i + N), (down, i - N), (right, i + 1), (left, i - 1)):
            if not (ok >> i) & 1 or j in parent:
                continue
            parent[j] = i
            if j // N == goal:
                path = []
                while j >= 0:
                    path.append((j % N, j // N))
                    j = parent[j]
                return path[::-1]
            push(j)
    return None
