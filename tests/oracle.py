"""Independent, deliberately naive reference model used to cross-check rules.py.

It shares no code with quoridor.rules/paths; walls are modelled as unit segments
between cells (cell pairs), so a bug in state.blocked() would not be mirrored here.
"""

from __future__ import annotations

from quoridor.state import GameState, WallMove

N = 9


def blocked_edges(state: GameState) -> set[frozenset]:
    edges: set[frozenset] = set()
    for x, y in state.h_walls:  # between rows y and y+1, columns x and x+1
        for cx in (x, x + 1):
            edges.add(frozenset({(cx, y), (cx, y + 1)}))
    for x, y in state.v_walls:  # between columns x and x+1, rows y and y+1
        for cy in (y, y + 1):
            edges.add(frozenset({(x, cy), (x + 1, cy)}))
    return edges


def neighbours(cell, edges):
    c, r = cell
    for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
        if 0 <= n[0] < N and 0 <= n[1] < N and frozenset({cell, n}) not in edges:
            yield n


def reaches(start, goal_row, edges) -> bool:
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        if cur[1] == goal_row:
            return True
        for n in neighbours(cur, edges):
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return False


def ref_pawn_dests(state: GameState) -> set[tuple[int, int]]:
    me = state.pawns[state.turn]
    opp = state.pawns[1 - state.turn]
    edges = blocked_edges(state)
    out = set()
    for n in neighbours(me, edges):
        if n != opp:
            out.add(n)
            continue
        opp_exits = set(neighbours(opp, edges))
        straight = (2 * opp[0] - me[0], 2 * opp[1] - me[1])
        if straight in opp_exits:
            out.add(straight)
        else:
            dc, dr = opp[0] - me[0], opp[1] - me[1]
            for side in ((dr, dc), (-dr, -dc)):
                t = (opp[0] + side[0], opp[1] + side[1])
                if t in opp_exits:
                    out.add(t)
    return out


def _segments(w: WallMove):
    if w.orient == "h":
        return {("h", w.x, w.y), ("h", w.x + 1, w.y)}
    return {("v", w.x, w.y), ("v", w.x, w.y + 1)}


def ref_wall_legal(state: GameState, w: WallMove) -> bool:
    if state.walls_left[state.turn] <= 0 or not (0 <= w.x <= 7 and 0 <= w.y <= 7):
        return False
    for other in state.walls():
        if _segments(other) & _segments(w):
            return False
        if (other.x, other.y) == (w.x, w.y) and other.orient != w.orient:
            return False
    h = state.h_walls | ({(w.x, w.y)} if w.orient == "h" else set())
    v = state.v_walls | ({(w.x, w.y)} if w.orient == "v" else set())
    nxt = GameState(state.pawns, h, v, state.walls_left, state.turn)
    edges = blocked_edges(nxt)
    return reaches(nxt.pawns[0], 8, edges) and reaches(nxt.pawns[1], 0, edges)
