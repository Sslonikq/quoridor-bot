"""Shortest paths (BFS). Pure; pawns are ignored (standard Quoridor path rule)."""

from __future__ import annotations

from collections import deque

from quoridor.state import Cell, GameState, goal_row, in_board

DIRS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def shortest_path_length(state: GameState, player: int) -> int | None:
    """Number of steps from the player's pawn to its goal row, or None if cut off."""
    start = state.pawns[player]
    goal = goal_row(player)
    if start[1] == goal:
        return 0
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        (c, r), d = queue.popleft()
        for dc, dr in DIRS:
            nc, nr = c + dc, r + dr
            if not in_board(nc, nr) or (nc, nr) in seen or state.blocked(c, r, dc, dr):
                continue
            if nr == goal:
                return d + 1
            seen.add((nc, nr))
            queue.append(((nc, nr), d + 1))
    return None


def shortest_path(state: GameState, player: int) -> list[Cell] | None:
    """One shortest path of cells from the pawn (inclusive) to the goal row, or None."""
    start = state.pawns[player]
    goal = goal_row(player)
    if start[1] == goal:
        return [start]
    parent: dict[Cell, Cell | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        c, r = cur
        for dc, dr in DIRS:
            nxt = (c + dc, r + dr)
            if not in_board(*nxt) or nxt in parent or state.blocked(c, r, dc, dr):
                continue
            parent[nxt] = cur
            if nxt[1] == goal:
                path = [nxt]
                while (p := parent[path[-1]]) is not None:
                    path.append(p)
                return path[::-1]
            queue.append(nxt)
    return None


def has_path_to_goal(state: GameState, player: int) -> bool:
    return shortest_path_length(state, player) is not None
