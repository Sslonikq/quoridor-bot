"""Combined wall threats (phase 9, step 3): what the best 1..k walls TOGETHER do to a path.

v4 values a wall stock by the best single wall (search.Searcher._race_cands). A cage is
different: two walls that each add nothing (they close one of two equal routes) can
together add many steps (game07: c8h b7v e6h g8h h7v, white's path 3 -> 10). Here a small
beam search over wall sets finds that:

  level 0: the current wall masks; level j: the `beam` best sets of j walls, each set
  extended by every wall that blocks an edge of B's current shortest-path DAG (any wall
  that lengthens B's path, now or together with later walls of the set, must block such
  an edge: walls blocking no DAG edge leave the distance and the DAG unchanged).
  Sets are ranked by (B's distance, fewer cells on B's shortest paths: a narrower
  corridor is closer to a cage), duplicates (the same walls in another order) dropped.

The result is the best distance after 1..k walls and the walls of those sets. B's pawn
stands still, A's pawn and jumps are ignored and A's own path is not checked (a wall that
would cut A off is rare and only makes the estimate optimistic): an estimate for the
evaluation and for move ordering, not a proof. Pure: no I/O, no global state; the search
memoises the calls (search.Searcher.combo). With numba (quoridor.jit) combo_threat runs as
the compiled kernel quoridor.kernels.combo with the same result (the Python version stays
as py_combo_threat; tests/paths/test_jit.py compares them).
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from quoridor import bitpaths
from quoridor.bitpaths import GOAL_MASK, Masks, N, bit
from quoridor.state import WALL_GRID, Cell, H, V

Wall = tuple[int, int, str]  # (x, y, orient), internal anchor (state.py)


def _wall_table() -> tuple[dict[tuple[int, str], tuple[tuple, ...]], ...]:
    """Per direction (up, down, right, left): bit index u of the cell the edge leaves ->
    in-grid walls blocking that edge, as (x, y, orient, the four "not wall_bits" masks)."""
    tables: list[dict] = [{}, {}, {}, {}]
    for c in range(N):
        for r in range(N):
            u = r * N + c
            for d, (dc, dr) in enumerate(((0, 1), (0, -1), (1, 0), (-1, 0))):
                nc, nr = c + dc, r + dr
                if not (0 <= nc < N and 0 <= nr < N):
                    continue
                if dr == 1:
                    ws = [(c, r, H), (c - 1, r, H)]
                elif dr == -1:
                    ws = [(c, r - 1, H), (c - 1, r - 1, H)]
                elif dc == 1:
                    ws = [(c, r, V), (c, r - 1, V)]
                else:
                    ws = [(c - 1, r, V), (c - 1, r - 1, V)]
                out = []
                for x, y, o in ws:
                    if 0 <= x < WALL_GRID and 0 <= y < WALL_GRID:
                        b = bitpaths.wall_bits(x, y, o)
                        out.append((x, y, o, ~b[0], ~b[1], ~b[2], ~b[3]))
                tables[d][u] = tuple(out)
    return tuple(tables)


_WALLS_BY_DIR = _wall_table()


def fits(h: AbstractSet[tuple[int, int]], v: AbstractSet[tuple[int, int]], w: Wall) -> bool:
    """rules.wall_fits on plain sets: no shared centre, no overlap along the wall."""
    x, y, o = w
    if (x, y) in h or (x, y) in v:
        return False
    if o == H:
        return (x - 1, y) not in h and (x + 1, y) not in h
    return (x, y - 1) not in v and (x, y + 1) not in v


def _bits(mask: int) -> list[int]:
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


def dag_walls(m: Masks, layers: list[int], narrow: int = 0) -> list[tuple]:
    """In-grid walls blocking an edge of the shortest-path DAG `layers` (no duplicates),
    as table entries (x, y, orient, not-masks). narrow > 0: only edges between two
    consecutive layers of at most `narrow` cells each (the narrow parts of the corridor,
    where a few walls can close it; cheaper, may miss a combination)."""
    up, down, right, left = m
    seen: set[tuple[int, int, str]] = set()
    out: list[tuple] = []
    for k in range(len(layers) - 1):
        a, b = layers[k], layers[k + 1]
        if narrow and (a.bit_count() > narrow or b.bit_count() > narrow):
            continue
        for d, us in enumerate(
            (
                a & up & (b >> N),
                a & down & (b << N),
                a & right & (b >> 1),
                a & left & (b << 1),
            )
        ):
            if not us:
                continue
            table = _WALLS_BY_DIR[d]
            for u in _bits(us):
                for e in table[u]:
                    key = (e[0], e[1], e[2])
                    if key not in seen:
                        seen.add(key)
                        out.append(e)
    return out


def combo_threat(
    m: Masks,
    h: AbstractSet[tuple[int, int]],
    v: AbstractSet[tuple[int, int]],
    cell: Cell,
    player: int,
    k: int,
    beam: int = 4,
    narrow: int = 0,
) -> tuple[int, tuple[int, ...], tuple[tuple[Wall, ...], ...]]:
    """(B's distance now, B's best distance after 1..k walls, the walls of those sets).

    B = `player` on `cell`; h / v: the wall set of `m`. The j-th entries are the best set
    of at most j walls found by the beam (so the distances never decrease with j). If no
    wall lengthens or narrows B's path the lists stop early. narrow > 0: candidate walls
    only at the narrow parts of B's shortest paths (dag_walls).
    """
    start, goal = bit(cell), GOAL_MASK[player]
    layers = bitpaths.shortest_layers(m, start, goal)
    assert layers is not None
    d0 = len(layers) - 1
    # beam entry: (dist, -dag cells, walls tuple, masks, layers, h, v)
    level = [(d0, -sum(x.bit_count() for x in layers), (), m, layers, h, v)]
    best_d: list[int] = []
    best_w: list[tuple[Wall, ...]] = []
    shortest_layers = bitpaths.shortest_layers
    for _ in range(k):
        nxt: dict[frozenset, tuple] = {}
        for _d, _n, walls, cm, lay, ch, cv in level:
            m0, m1, m2, m3 = cm
            for x, y, o, n0, n1, n2, n3 in dag_walls(cm, lay, narrow):
                w = (x, y, o)
                if not fits(ch, cv, w):
                    continue
                key = frozenset(walls + (w,))
                if key in nxt:
                    continue
                nm = (m0 & n0, m1 & n1, m2 & n2, m3 & n3)
                nl = shortest_layers(nm, start, goal)
                if nl is None:
                    continue  # cuts B off: illegal
                if o == H:
                    nh, nv = ch | {(x, y)}, cv
                else:
                    nh, nv = ch, cv | {(x, y)}
                nxt[key] = (
                    len(nl) - 1,
                    -sum(x.bit_count() for x in nl),
                    walls + (w,),
                    nm,
                    nl,
                    nh,
                    nv,
                )
        if not nxt:
            break
        ranked = sorted(nxt.values(), key=lambda t: (-t[0], -t[1], t[2]))
        level = ranked[:beam]
        top = level[0]
        if best_d and top[0] < best_d[-1]:
            best_d.append(best_d[-1])
            best_w.append(best_w[-1])
        else:
            best_d.append(top[0])
            best_w.append(top[2])
    return d0, tuple(best_d), tuple(best_w)


# ---------- numba (phase 9, step 6) ----------
py_combo_threat = combo_threat


def _jit_combo_threat(
    m: Masks,
    h: AbstractSet[tuple[int, int]],
    v: AbstractSet[tuple[int, int]],
    cell: Cell,
    player: int,
    k: int,
    beam: int = 4,
    narrow: int = 0,
) -> tuple[int, tuple[int, ...], tuple[tuple[Wall, ...], ...]]:
    """combo_threat on quoridor.kernels.combo (same result; h / v follow from the masks)."""
    if beam < 1:  # degenerate: whatever the reference does (it fails on an empty beam)
        return py_combo_threat(m, h, v, cell, player, k, beam, narrow)
    r = _K.combo(*bitpaths.m8(m), cell[1] * N + cell[0], player, k, beam, narrow).tolist()
    assert r[0] >= 0
    levels = r[1]
    p = 2 + levels
    sets = []
    walls = bitpaths.WALL_OF_CODE
    for _ in range(levels):
        s = r[p]
        sets.append(tuple(walls[c] for c in r[p + 1 : p + 1 + s]))
        p += 1 + s
    return r[0], tuple(r[2 : 2 + levels]), tuple(sets)


_K = bitpaths._K
if _K is not None:
    combo_threat = _jit_combo_threat  # type: ignore[assignment]  # noqa: F811
