"""Bitboard shortest paths for the search. Pure; same results as quoridor.paths.

A cell (col, row) is bit row * 9 + col of a Python int. For a wall set four "move
allowed" masks are built once (cells whose step up / down / right / left is on the board
and not blocked); a BFS layer is then a handful of big-int operations instead of a
Python loop over cells. quoridor.paths stays the reference implementation; tests in
tests/paths compare the two. With numba (quoridor.jit) distance, player_distance,
shortest_layers and shortest_path run as compiled kernels (quoridor.kernels) with the same
results; the Python versions stay as py_* (tests/paths/test_jit.py compares them).
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


# ---------- numba (phase 9, step 6) ----------
# The functions above are the pure-Python reference (kept as py_*). With numba on
# (quoridor.jit.ENABLED) the public names are rebound to wrappers of quoridor.kernels with
# the same results; the masks are passed as eight 64-bit words (m8).

M64 = (1 << 64) - 1
# wall code of quoridor.kernels ((y * 8 + x) * 2 + o) -> (x, y, orient)
WALL_OF_CODE: tuple[tuple[int, int, str], ...] = tuple(
    (p % 8, p // 8, (H, "v")[o]) for p in range(64) for o in (0, 1)
)
# ... -> (x, y, orient, the four "not wall_bits" masks): child masks = m & not-masks
WALL_NOT_OF_CODE: tuple[tuple, ...] = tuple(
    (x, y, o, *(~b for b in wall_bits(x, y, o))) for x, y, o in WALL_OF_CODE
)

py_distance = distance
py_player_distance = player_distance
py_shortest_layers = shortest_layers
py_shortest_path = shortest_path


def m8(m: Masks) -> tuple[int, int, int, int, int, int, int, int]:
    """The masks as eight 64-bit words (lo, hi of up, down, right, left)."""
    a, b, c, d = m
    return (a & M64, a >> 64, b & M64, b >> 64, c & M64, c >> 64, d & M64, d >> 64)


def _jit_distance(m: Masks, start: int, goal: int) -> int | None:
    a, b, c, d = m
    r = _K.distance(
        a & M64, a >> 64, b & M64, b >> 64, c & M64, c >> 64, d & M64, d >> 64,
        start & M64, start >> 64, goal & M64, goal >> 64,
    )  # fmt: skip
    return None if r < 0 else r


def _jit_player_distance(m: Masks, cell: Cell, player: int) -> int | None:
    a, b, c, d = m
    r = _K.player_distance(
        a & M64, a >> 64, b & M64, b >> 64, c & M64, c >> 64, d & M64, d >> 64,
        cell[1] * N + cell[0], player,
    )  # fmt: skip
    return None if r < 0 else r


def _jit_shortest_layers(m: Masks, start: int, goal: int) -> list[int] | None:
    w = _K.shortest_layers(*m8(m), start & M64, start >> 64, goal & M64, goal >> 64).tolist()
    if not w:
        return None
    return [w[i] | (w[i + 1] << 64) for i in range(0, len(w), 2)]


def _jit_shortest_path(m: Masks, cell: Cell, player: int) -> list[Cell] | None:
    p = _K.shortest_path(*m8(m), cell[1] * N + cell[0], player).tolist()
    return [(i % N, i // N) for i in p] if p else None


from quoridor import jit as _jit  # noqa: E402  (after the reference code on purpose)

_K = _jit.kernels()
if _K is not None:
    distance = _jit_distance  # type: ignore[assignment]  # noqa: F811
    player_distance = _jit_player_distance  # type: ignore[assignment]  # noqa: F811
    shortest_layers = _jit_shortest_layers  # type: ignore[assignment]  # noqa: F811
    shortest_path = _jit_shortest_path  # type: ignore[assignment]  # noqa: F811
