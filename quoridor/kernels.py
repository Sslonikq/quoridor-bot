"""numba kernels (phase 9, step 6): exact ports of the hot pure-Python code.

Imported only when quoridor.jit.ENABLED (needs numba and numpy). Every kernel returns
exactly what its Python reference returns (tests/paths/test_jit.py compares them):

  distance, shortest_layers, shortest_path   <- quoridor.bitpaths (same names)
  race_cands                                 <- search.Searcher._race_cands (Python part)
  combo                                      <- quoridor.threat.combo_threat
  cand_walls                                 <- search.Searcher.candidate_walls (no threat
                                                branch, children are built by the caller)
  mcts_run, mcts_pv, mcts_count, mcts_compact <- the simulations of quoridor.mcts on a tree
                                                of arrays (section "MCTS tree" below)

Bitboards: a cell (col, row) is bit row * 9 + col of an 81-bit set (as in bitpaths); here
the set is two uint64 words, lo = bits 0..63 and hi = bits 64..80. Wall masks are the
four bitpaths masks (up, down, right, left) as eight words m = (up_lo, up_hi, down_lo,
..., left_hi). The wall sets of the position are not passed: for wall sets reached by
legal play (no overlaps) they follow from the masks (_walls_of), so a kernel needs only
the masks. Walls are coded as (y * 8 + x) * 2 + o (o: 0 = 'h', 1 = 'v'; internal anchor,
see state.py). The Python wrappers that split Python ints into words and decode the
results live in quoridor.bitpaths, quoridor.threat and quoridor.search.
"""

from __future__ import annotations

import math

import numpy as np

from quoridor.jit import njit

_U = np.uint64
ZERO = _U(0)
ONE = _U(1)
S1 = _U(1)
S9 = _U(9)
S55 = _U(55)
S63 = _U(63)
HM = _U((1 << 17) - 1)  # the 17 bits of the hi word
M64 = (1 << 64) - 1
N = 9
NO_DIST = -1


def _split(x: int) -> tuple[int, int]:
    return x & M64, x >> 64


def _tables() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """EW[d, u, 0..1]: in-grid walls blocking the edge from cell u in direction d (up,
    down, right, left; the order of search.edge_walls / threat._WALLS_BY_DIR), -1 = none.
    WB[w, 0..7]: the bits wall w removes from the eight mask words.
    GOAL[p, 0..1]: the goal row of player p as (lo, hi)."""
    ew = np.full((4, 81, 2), -1, dtype=np.int64)
    for c in range(N):
        for r in range(N):
            u = r * N + c
            for d, (dc, dr) in enumerate(((0, 1), (0, -1), (1, 0), (-1, 0))):
                if not (0 <= c + dc < N and 0 <= r + dr < N):
                    continue
                if dr == 1:
                    ws = [(c, r, 0), (c - 1, r, 0)]
                elif dr == -1:
                    ws = [(c, r - 1, 0), (c - 1, r - 1, 0)]
                elif dc == 1:
                    ws = [(c, r, 1), (c, r - 1, 1)]
                else:
                    ws = [(c - 1, r, 1), (c - 1, r - 1, 1)]
                k = 0
                for x, y, o in ws:
                    if 0 <= x < 8 and 0 <= y < 8:
                        ew[d, u, k] = (y * 8 + x) * 2 + o
                        k += 1
    wb = np.zeros((128, 8), dtype=np.uint64)
    for y in range(8):
        for x in range(8):
            i = y * N + x
            for o in (0, 1):
                if o == 0:
                    bits = (3 << i, 3 << (i + N), 0, 0)
                else:
                    bits = (0, 0, (1 << i) | (1 << (i + N)), (2 << i) | (2 << (i + N)))
                w = (y * 8 + x) * 2 + o
                for j, b in enumerate(bits):
                    lo, hi = _split(b)
                    wb[w, 2 * j] = lo
                    wb[w, 2 * j + 1] = hi
    goal = np.zeros((2, 2), dtype=np.uint64)
    row0 = (1 << N) - 1
    for p, g in ((0, row0 << (N * (N - 1))), (1, row0)):
        goal[p, 0], goal[p, 1] = _split(g)
    return ew, wb, goal


EW, WB, GOAL = _tables()


# ---------- bit helpers ----------


@njit(cache=True)
def _bit(lo, hi, i):
    if i < 64:
        return (lo >> _U(i)) & ONE != ZERO
    return (hi >> _U(i - 64)) & ONE != ZERO


@njit(cache=True)
def _pop(x):
    x = x - ((x >> S1) & _U(0x5555555555555555))
    x = (x & _U(0x3333333333333333)) + ((x >> _U(2)) & _U(0x3333333333333333))
    x = (x + (x >> _U(4))) & _U(0x0F0F0F0F0F0F0F0F)
    return np.int64((x * _U(0x0101010101010101)) >> _U(56))


@njit(cache=True)
def _cell(i):
    if i < 64:
        return ONE << _U(i), ZERO
    return ZERO, ONE << _U(i - 64)


@njit(cache=True)
def _nbr(fl, fh, m):
    """Cells one allowed step away from the set (fl, fh) (the bitpaths BFS step)."""
    al = fl & m[0]
    ah = fh & m[1]
    nl = al << S9
    nh = ((ah << S9) | (al >> S55)) & HM
    bl = fl & m[2]
    bh = fh & m[3]
    nl |= (bl >> S9) | (bh << S55)
    nh |= bh >> S9
    cl = fl & m[4]
    ch = fh & m[5]
    nl |= cl << S1
    nh |= ((ch << S1) | (cl >> S63)) & HM
    el = fl & m[6]
    eh = fh & m[7]
    nl |= (el >> S1) | (eh << S63)
    nh |= eh >> S1
    return nl, nh


@njit(cache=True)
def _with_wall(m, w):
    return (
        m[0] & ~WB[w, 0],
        m[1] & ~WB[w, 1],
        m[2] & ~WB[w, 2],
        m[3] & ~WB[w, 3],
        m[4] & ~WB[w, 4],
        m[5] & ~WB[w, 5],
        m[6] & ~WB[w, 6],
        m[7] & ~WB[w, 7],
    )


@njit(cache=True)
def _walls_of(m):
    """(h, v) wall sets of the masks as 64-bit sets (bit y * 8 + x). A wall set without
    overlaps is the unique tiling of every run of blocked edges by pairs, so the scan
    from the left (h: up mask, row by row) / from below (v: right mask) recovers it."""
    hb = ZERO
    vb = ZERO
    for y in range(8):
        x = 0
        while x < 8:
            if not _bit(m[0], m[1], y * N + x):
                hb |= ONE << _U(y * 8 + x)
                x += 2
            else:
                x += 1
    for x in range(8):
        y = 0
        while y < 8:
            if not _bit(m[4], m[5], y * N + x):
                vb |= ONE << _U(y * 8 + x)
                y += 2
            else:
                y += 1
    return hb, vb


@njit(cache=True)
def _has(b, x, y):
    return 0 <= x < 8 and 0 <= y < 8 and (b >> _U(y * 8 + x)) & ONE != ZERO


@njit(cache=True)
def _fits(hb, vb, w):
    """rules.wall_fits / threat.fits on 64-bit wall sets (on-grid wall w)."""
    p = w >> 1
    x = p % 8
    y = p // 8
    if _has(hb, x, y) or _has(vb, x, y):
        return False
    if w & 1 == 0:
        return not (_has(hb, x - 1, y) or _has(hb, x + 1, y))
    return not (_has(vb, x, y - 1) or _has(vb, x, y + 1))


@njit(cache=True)
def _occupied(hb, vb, w):
    p = w >> 1
    return _has(hb, p % 8, p // 8) or _has(vb, p % 8, p // 8)


# ---------- BFS ----------


@njit(cache=True)
def _dist(m, sl, sh, gl, gh):
    if (sl & gl) | (sh & gh) != ZERO:
        return 0
    seenl = sl
    seenh = sh
    fl = sl
    fh = sh
    d = 0
    while (fl | fh) != ZERO:
        d += 1
        nl, nh = _nbr(fl, fh, m)
        fl = nl & ~seenl
        fh = nh & ~seenh
        if (fl & gl) | (fh & gh) != ZERO:
            return d
        seenl |= fl
        seenh |= fh
    return NO_DIST


@njit(cache=True)
def _dist_cell(m, cell, player):
    sl, sh = _cell(cell)
    return _dist(m, sl, sh, GOAL[player, 0], GOAL[player, 1])


@njit(cache=True)
def _layers(m, sl, sh, gl, gh, outl, outh):
    """bitpaths.shortest_layers into outl / outh; returns the number of layers (d + 1),
    0 if the goal is unreachable."""
    outl[0] = sl
    outh[0] = sh
    n = 1
    seenl = sl
    seenh = sh
    fl = sl
    fh = sh
    while (fl & gl) | (fh & gh) == ZERO:
        nl, nh = _nbr(fl, fh, m)
        fl = nl & ~seenl
        fh = nh & ~seenh
        if (fl | fh) == ZERO:
            return 0
        seenl |= fl
        seenh |= fh
        outl[n] = fl
        outh[n] = fh
        n += 1
    ll = fl & gl
    lh = fh & gh
    outl[n - 1] = ll
    outh[n - 1] = lh
    for k in range(n - 2, -1, -1):
        bl, bh = _nbr(ll, lh, m)
        ll = outl[k] & bl
        lh = outh[k] & bh
        outl[k] = ll
        outh[k] = lh
    return n


# ---------- entry points: bitpaths ----------

_M8 = "uint64, uint64, uint64, uint64, uint64, uint64, uint64, uint64"


@njit("int64(" + _M8 + ", uint64, uint64, uint64, uint64)", cache=True)
def distance(m0, m1, m2, m3, m4, m5, m6, m7, sl, sh, gl, gh):
    """bitpaths.distance; -1 if unreachable."""
    return _dist((m0, m1, m2, m3, m4, m5, m6, m7), sl, sh, gl, gh)


@njit("int64(" + _M8 + ", int64, int64)", cache=True)
def player_distance(m0, m1, m2, m3, m4, m5, m6, m7, cell, player):
    """bitpaths.player_distance with the cell as a bit index; -1 if unreachable."""
    return _dist_cell((m0, m1, m2, m3, m4, m5, m6, m7), cell, player)


@njit("uint64[:](" + _M8 + ", uint64, uint64, uint64, uint64)", cache=True)
def shortest_layers(m0, m1, m2, m3, m4, m5, m6, m7, sl, sh, gl, gh):
    """bitpaths.shortest_layers as [lo_0, hi_0, lo_1, hi_1, ...]; empty if unreachable."""
    outl = np.empty(82, dtype=np.uint64)
    outh = np.empty(82, dtype=np.uint64)
    n = _layers((m0, m1, m2, m3, m4, m5, m6, m7), sl, sh, gl, gh, outl, outh)
    out = np.empty(2 * n, dtype=np.uint64)
    for k in range(n):
        out[2 * k] = outl[k]
        out[2 * k + 1] = outh[k]
    return out


@njit(cache=True)
def _path(m, start, player, out):
    """bitpaths.shortest_path (same BFS order: up, down, right, left) into `out` as bit
    indexes; returns its length, 0 if unreachable."""
    goal = N - 1 if player == 0 else 0
    if start // N == goal:
        out[0] = start
        return 1
    parent = np.full(81, -2, dtype=np.int64)
    queue = np.empty(81, dtype=np.int64)
    parent[start] = -1
    queue[0] = start
    head = 0
    tail = 1
    while head < tail:
        i = queue[head]
        head += 1
        for d in range(4):
            if not _bit(m[2 * d], m[2 * d + 1], i):
                continue
            if d == 0:
                j = i + N
            elif d == 1:
                j = i - N
            elif d == 2:
                j = i + 1
            else:
                j = i - 1
            if parent[j] != -2:
                continue
            parent[j] = i
            if j // N == goal:
                n = 0
                while j >= 0:
                    out[n] = j
                    n += 1
                    j = parent[j]
                for a in range(n // 2):
                    t = out[a]
                    out[a] = out[n - 1 - a]
                    out[n - 1 - a] = t
                return n
            queue[tail] = j
            tail += 1
    return 0


@njit("int64[:](" + _M8 + ", int64, int64)", cache=True)
def shortest_path(m0, m1, m2, m3, m4, m5, m6, m7, start, player):
    """bitpaths.shortest_path as bit indexes; empty if unreachable."""
    out = np.empty(81, dtype=np.int64)
    n = _path((m0, m1, m2, m3, m4, m5, m6, m7), start, player, out)
    return out[:n].copy()


# ---------- entry point: Searcher._race_cands ----------


@njit("int64[:](" + _M8 + ", int64, int64)", cache=True)
def race_cands(m0, m1, m2, m3, m4, m5, m6, m7, cell, b):
    """[d_b, n, then n entries nb * 256 + wall], best first: the walls Searcher._race_cands
    keeps (on a layer pair with <= 2 cells each, fit, lengthen B's path), sorted by
    (-nb, (x, y seen from B's side, orient)). d_b = -1: B is cut off."""
    m = (m0, m1, m2, m3, m4, m5, m6, m7)
    sl, sh = _cell(cell)
    gl = GOAL[b, 0]
    gh = GOAL[b, 1]
    ll = np.empty(82, dtype=np.uint64)
    lh = np.empty(82, dtype=np.uint64)
    n = _layers(m, sl, sh, gl, gh, ll, lh)
    if n == 0:
        out = np.empty(2, dtype=np.int64)
        out[0] = -1
        out[1] = 0
        return out
    d_b = n - 1
    hb, vb = _walls_of(m)
    seen = np.zeros(128, dtype=np.bool_)
    keys = np.empty(128, dtype=np.int64)
    vals = np.empty(128, dtype=np.int64)
    cnt = 0
    for k in range(d_b):
        if _pop(ll[k]) + _pop(lh[k]) > 2 or _pop(ll[k + 1]) + _pop(lh[k + 1]) > 2:
            continue
        for u in range(81):
            if not _bit(ll[k], lh[k], u):
                continue
            for d in range(4):
                if not _bit(m[2 * d], m[2 * d + 1], u):
                    continue
                if d == 0:
                    j = u + N
                elif d == 1:
                    j = u - N
                elif d == 2:
                    j = u + 1
                else:
                    j = u - 1
                if not _bit(ll[k + 1], lh[k + 1], j):
                    continue
                for e in range(2):
                    w = EW[d, u, e]
                    if w < 0 or seen[w]:
                        continue
                    seen[w] = True
                    if not _fits(hb, vb, w):
                        continue
                    nb = _dist(_with_wall(m, w), sl, sh, gl, gh)
                    if nb != NO_DIST and nb > d_b:
                        p = w >> 1
                        x = p % 8
                        y = p // 8
                        yb = y if b == 0 else 7 - y
                        keys[cnt] = ((200 - nb) << 8) | (x << 4) | (yb << 1) | (w & 1)
                        vals[cnt] = nb * 256 + w
                        cnt += 1
    order = np.argsort(keys[:cnt])
    out = np.empty(2 + cnt, dtype=np.int64)
    out[0] = d_b
    out[1] = cnt
    for i in range(cnt):
        out[2 + i] = vals[order[i]]
    return out


# ---------- entry point: threat.combo_threat ----------


@njit(cache=True)
def _cmp_code(w):
    """Sort code of wall w with the order of the tuple (x, y, orient), 'h' < 'v'."""
    p = w >> 1
    return (p % 8) * 16 + (p // 8) * 2 + (w & 1)


@njit(cache=True)
def _before(d1, c1, w1, d2, c2, w2, j):
    """threat.combo_threat ranking: entry 1 sorts before entry 2 (key (-dist, cells,
    walls tuple)); w1 / w2: the first j walls as sort codes."""
    if d1 != d2:
        return d1 > d2
    if c1 != c2:
        return c1 < c2
    for i in range(j):
        if w1[i] != w2[i]:
            return w1[i] < w2[i]
    return False


@njit("int64[:](" + _M8 + ", int64, int64, int64, int64, int64)", cache=True)
def combo(m0, m1, m2, m3, m4, m5, m6, m7, cell, player, k, beam, narrow):
    """threat.combo_threat as [d0, L, best_d[0..L-1], then for each of the L sets its
    size s and s walls]; d0 = -1: B is cut off. Same beam, candidate order, duplicate
    rule (the first order of a wall set met is kept) and ranking as the Python code."""
    m = (m0, m1, m2, m3, m4, m5, m6, m7)
    sl, sh = _cell(cell)
    gl = GOAL[player, 0]
    gh = GOAL[player, 1]
    kk = max(k, 1)
    # current level (at most `beam` entries, ranked)
    lv_m = np.empty((beam, 8), dtype=np.uint64)
    lv_ll = np.empty((beam, 82), dtype=np.uint64)
    lv_lh = np.empty((beam, 82), dtype=np.uint64)
    lv_n = np.zeros(beam, dtype=np.int64)
    lv_d = np.zeros(beam, dtype=np.int64)
    lv_c = np.zeros(beam, dtype=np.int64)
    lv_w = np.zeros((beam, kk), dtype=np.int64)  # walls as codes, in placing order
    lv_s = np.zeros((beam, kk), dtype=np.int64)  # the same as sort codes
    # next level: the best `beam` candidates
    nx_m = np.empty((beam, 8), dtype=np.uint64)
    nx_ll = np.empty((beam, 82), dtype=np.uint64)
    nx_lh = np.empty((beam, 82), dtype=np.uint64)
    nx_n = np.zeros(beam, dtype=np.int64)
    nx_d = np.zeros(beam, dtype=np.int64)
    nx_c = np.zeros(beam, dtype=np.int64)
    nx_w = np.zeros((beam, kk), dtype=np.int64)
    nx_s = np.zeros((beam, kk), dtype=np.int64)
    # every wall set of the next level (duplicate check), as 128-bit sets
    cap = beam * 128 + 1
    set_lo = np.empty(cap, dtype=np.uint64)
    set_hi = np.empty(cap, dtype=np.uint64)
    tl = np.empty(82, dtype=np.uint64)
    th = np.empty(82, dtype=np.uint64)
    cw = np.zeros(kk, dtype=np.int64)
    cs = np.zeros(kk, dtype=np.int64)
    seen = np.zeros(128, dtype=np.bool_)
    best_d = np.zeros(kk, dtype=np.int64)
    best_n = np.zeros(kk, dtype=np.int64)
    best_w = np.zeros((kk, kk), dtype=np.int64)

    n0 = _layers(m, sl, sh, gl, gh, lv_ll[0], lv_lh[0])
    if n0 == 0:
        out = np.empty(2, dtype=np.int64)
        out[0] = -1
        out[1] = 0
        return out
    d0 = n0 - 1
    for i in range(8):
        lv_m[0, i] = m[i]
    lv_n[0] = n0
    lv_d[0] = d0
    cells = 0
    for i in range(n0):
        cells += _pop(lv_ll[0, i]) + _pop(lv_lh[0, i])
    lv_c[0] = cells
    lv_cnt = 1
    nb_levels = 0
    for j in range(k):  # level j + 1: sets of j + 1 walls
        nx_cnt = 0
        n_sets = 0
        for e in range(lv_cnt):
            em = (
                lv_m[e, 0],
                lv_m[e, 1],
                lv_m[e, 2],
                lv_m[e, 3],
                lv_m[e, 4],
                lv_m[e, 5],
                lv_m[e, 6],
                lv_m[e, 7],
            )
            hb, vb = _walls_of(em)
            pl = ZERO
            ph = ZERO
            for i in range(j):
                w = lv_w[e, i]
                if w < 64:
                    pl |= ONE << _U(w)
                else:
                    ph |= ONE << _U(w - 64)
            seen[:] = False
            en = lv_n[e]
            for q in range(en - 1):
                al = lv_ll[e, q]
                ah = lv_lh[e, q]
                bl = lv_ll[e, q + 1]
                bh = lv_lh[e, q + 1]
                if narrow != 0 and (_pop(al) + _pop(ah) > narrow or _pop(bl) + _pop(bh) > narrow):
                    continue
                for d in range(4):
                    # us = a & dir & (b shifted back by the step)
                    if d == 0:
                        sbl = (bl >> S9) | (bh << S55)
                        sbh = bh >> S9
                    elif d == 1:
                        sbl = bl << S9
                        sbh = ((bh << S9) | (bl >> S55)) & HM
                    elif d == 2:
                        sbl = (bl >> S1) | (bh << S63)
                        sbh = bh >> S1
                    else:
                        sbl = bl << S1
                        sbh = ((bh << S1) | (bl >> S63)) & HM
                    ul = al & em[2 * d] & sbl
                    uh = ah & em[2 * d + 1] & sbh
                    if (ul | uh) == ZERO:
                        continue
                    for u in range(81):
                        if not _bit(ul, uh, u):
                            continue
                        for t in range(2):
                            w = EW[d, u, t]
                            if w < 0 or seen[w]:
                                continue
                            seen[w] = True
                            if not _fits(hb, vb, w):
                                continue
                            kl = pl
                            kh = ph
                            if w < 64:
                                kl |= ONE << _U(w)
                            else:
                                kh |= ONE << _U(w - 64)
                            dup = False
                            for s in range(n_sets):
                                if set_lo[s] == kl and set_hi[s] == kh:
                                    dup = True
                                    break
                            if dup:
                                continue
                            nm = _with_wall(em, w)
                            nn = _layers(nm, sl, sh, gl, gh, tl, th)
                            if nn == 0:
                                continue  # cuts B off: illegal
                            set_lo[n_sets] = kl
                            set_hi[n_sets] = kh
                            n_sets += 1
                            nd = nn - 1
                            nc = 0
                            for i in range(nn):
                                nc += _pop(tl[i]) + _pop(th[i])
                            for i in range(j):
                                cw[i] = lv_w[e, i]
                                cs[i] = lv_s[e, i]
                            cw[j] = w
                            cs[j] = _cmp_code(w)
                            # insert into the ranked top `beam`
                            pos = nx_cnt
                            while pos > 0 and _before(
                                nd, nc, cs, nx_d[pos - 1], nx_c[pos - 1], nx_s[pos - 1], j + 1
                            ):
                                pos -= 1
                            if pos >= beam:
                                continue
                            last = nx_cnt if nx_cnt < beam else beam - 1
                            for r in range(last, pos, -1):
                                nx_m[r] = nx_m[r - 1]
                                nx_n[r] = nx_n[r - 1]
                                nx_d[r] = nx_d[r - 1]
                                nx_c[r] = nx_c[r - 1]
                                nx_w[r] = nx_w[r - 1]
                                nx_s[r] = nx_s[r - 1]
                                pn = nx_n[r]
                                nx_ll[r, :pn] = nx_ll[r - 1, :pn]
                                nx_lh[r, :pn] = nx_lh[r - 1, :pn]
                            for i in range(8):
                                nx_m[pos, i] = nm[i]
                            nx_n[pos] = nn
                            nx_d[pos] = nd
                            nx_c[pos] = nc
                            nx_w[pos, : j + 1] = cw[: j + 1]
                            nx_s[pos, : j + 1] = cs[: j + 1]
                            nx_ll[pos, :nn] = tl[:nn]
                            nx_lh[pos, :nn] = th[:nn]
                            if nx_cnt < beam:
                                nx_cnt += 1
        if n_sets == 0:
            break
        # best after j + 1 walls (never worse than after j)
        if nb_levels > 0 and nx_d[0] < best_d[nb_levels - 1]:
            best_d[nb_levels] = best_d[nb_levels - 1]
            best_n[nb_levels] = best_n[nb_levels - 1]
            best_w[nb_levels] = best_w[nb_levels - 1]
        else:
            best_d[nb_levels] = nx_d[0]
            best_n[nb_levels] = j + 1
            best_w[nb_levels, : j + 1] = nx_w[0, : j + 1]
        nb_levels += 1
        # the next level becomes the current one
        lv_cnt = nx_cnt
        for e in range(nx_cnt):
            lv_m[e] = nx_m[e]
            lv_n[e] = nx_n[e]
            lv_d[e] = nx_d[e]
            lv_c[e] = nx_c[e]
            lv_w[e] = nx_w[e]
            lv_s[e] = nx_s[e]
            pn = nx_n[e]
            lv_ll[e, :pn] = nx_ll[e, :pn]
            lv_lh[e, :pn] = nx_lh[e, :pn]
    size = 2 + nb_levels
    for i in range(nb_levels):
        size += 1 + best_n[i]
    out = np.empty(size, dtype=np.int64)
    out[0] = d0
    out[1] = nb_levels
    p = 2
    for i in range(nb_levels):
        out[p] = best_d[i]
        p += 1
    for i in range(nb_levels):
        out[p] = best_n[i]
        p += 1
        for t in range(best_n[i]):
            out[p] = best_w[i, t]
            p += 1
    return out


# ---------- entry point: Searcher.candidate_walls ----------


@njit("int64[:](" + _M8 + ", int64, int64, int64, int64)", cache=True)
def cand_walls(m0, m1, m2, m3, m4, m5, m6, m7, my_cell, op_cell, me, limit):
    """Searcher.candidate_walls without the threat branch: [n, then n entries (gain,
    wall, opponent's distance after, our distance after)], best gain first (stable in the
    order of the walls along the opponent's shortest path), at most `limit` walls that
    fit. n = -1: someone is cut off (never for positions of legal play)."""
    m = (m0, m1, m2, m3, m4, m5, m6, m7)
    op = 1 - me
    path = np.empty(81, dtype=np.int64)
    plen = _path(m, op_cell, op, path)
    if plen == 0:
        out = np.empty(1, dtype=np.int64)
        out[0] = 0
        return out
    d_me = _dist_cell(m, my_cell, me)
    if d_me == NO_DIST:
        out = np.empty(1, dtype=np.int64)
        out[0] = -1
        return out
    d_op = plen - 1
    hb, vb = _walls_of(m)
    sl, sh = _cell(op_cell)
    msl, msh = _cell(my_cell)
    seen = np.zeros(128, dtype=np.bool_)
    gains = np.empty(2 * plen, dtype=np.int64)
    walls = np.empty(2 * plen, dtype=np.int64)
    opa = np.empty(2 * plen, dtype=np.int64)
    mea = np.empty(2 * plen, dtype=np.int64)
    cnt = 0
    for i in range(plen - 1):
        a = path[i]
        bb = path[i + 1]
        if bb == a + N:
            d = 0
        elif bb == a - N:
            d = 1
        elif bb == a + 1:
            d = 2
        else:
            d = 3
        for t in range(2):
            w = EW[d, a, t]
            if w < 0 or seen[w]:
                continue
            seen[w] = True
            if _occupied(hb, vb, w):
                continue
            cm = _with_wall(m, w)
            op_after = _dist(cm, sl, sh, GOAL[op, 0], GOAL[op, 1])
            if op_after == NO_DIST or op_after <= d_op:
                continue
            me_after = _dist(cm, msl, msh, GOAL[me, 0], GOAL[me, 1])
            if me_after == NO_DIST:
                continue
            gain = (op_after - d_op) - (me_after - d_me)
            if gain <= 0:
                continue
            # stable insertion by -gain
            pos = cnt
            while pos > 0 and gains[pos - 1] < gain:
                gains[pos] = gains[pos - 1]
                walls[pos] = walls[pos - 1]
                opa[pos] = opa[pos - 1]
                mea[pos] = mea[pos - 1]
                pos -= 1
            gains[pos] = gain
            walls[pos] = w
            opa[pos] = op_after
            mea[pos] = me_after
            cnt += 1
    out = np.empty(1 + 4 * cnt, dtype=np.int64)
    n = 0
    for i in range(cnt):
        if not _fits(hb, vb, walls[i]):
            continue
        out[1 + 4 * n] = gains[i]
        out[2 + 4 * n] = walls[i]
        out[3 + 4 * n] = opa[i]
        out[4 + 4 * n] = mea[i]
        n += 1
        if n >= limit:  # checked after the append, as in the Python code
            break
    out[0] = n
    return out[: 1 + 4 * n]


# ---------- MCTS tree (phase 9, step 3 "MCTS speed") ----------
#
# The whole simulation of quoridor.mcts (selection, leaf eval, expansion, backup) on
# arrays. The tree is node arrays (visits, value sum, proven, first edge, edge count)
# and edge arrays (move code, prior, child node); the position is carried along the
# path from the root, no states are stored. Exact port of the pure-Python MCTS with its
# searcher settings (tests/search/test_mcts.py compares the two trees):
#   _pawn_moves  <- search.pawn_moves_m / rules.pawn_moves_for
#   _eval        <- search.Searcher.evaluate (v1-v3 / v4, threat, reserve, combo and
#                   reserve_combo on the root walls; combo_exact = combo_stock = 0)
#   _race_best   <- the first entry of race_cands (Searcher._race_cands)
#   _cand_walls  <- Searcher.candidate_walls without the threat branch (as cand_walls)
#   _saving      <- Searcher._saving_walls
#   _expand      <- mcts.MCTS._expand_lists of an inner node: pawn moves, then the
#                   candidate (or saving) walls; seeded shuffle, stable sort, softmax
#   _rand        <- mcts._splitmix (the seeded order of equal children)
# Move codes: 0..80 a pawn move to that cell, WALL_MOVE + wall code a wall.

IP_WALL_WEIGHT = 0
IP_V4 = 1
IP_THREAT = 2
IP_RESERVE = 3
IP_COMBO = 4
IP_COMBO_K = 5
IP_COMBO_BEAM = 6
IP_COMBO_NARROW = 7
IP_COMBO_COST = 8
IP_RACE_PLAN = 9
IP_RACE_SOFT = 10
IP_RACE_MIN_GAIN = 11
IP_WALL_LIMIT = 12
IP_EXPAND = 13  # mcts expand_visits: a leaf is expanded on its visit number this (1 or 2)
IP_RESERVE_COMBO = 14  # Searcher.reserve_combo (report 0085)
IP_RESERVE_K = 15  # Searcher.reserve_k
IP_COMBO_LEVELS = 16  # Searcher.combo_levels: levels of the combination memo ct
IP_RESERVE_MIN = 17  # Searcher.reserve_min
IP_SIZE = 18

FP_C_PUCT = 0
FP_SCALE = 1
FP_FPU = 2
FP_PAWN = 3
FP_WALL = 4
FP_GAIN = 5
FP_BEHIND = 6
FP_SAVING = 7
FP_TEMPO = 8
FP_SIZE = 9

WALL_MOVE = 128  # move code of wall w: WALL_MOVE + w
_STEP = 100  # eval.STEP
_RACE = 5_000  # eval.RACE
MAX_PATH = 4096  # deepest selection path (never reached in practice)
MAX_MOVES = 256  # more than any node has (5 pawn moves + saving / candidate walls)

ST_DONE = 0  # the requested simulations ran
ST_FULL = 1  # the tree arrays are full: grow them and call again
ST_PROVEN = 2  # the root is proven
ST_ONE = 3  # one root move and >= 2 simulations: nothing to choose

_GOLDEN = _U(0x9E3779B97F4A7C15)
_MIX1 = _U(0xBF58476D1CE4E5B9)
_MIX2 = _U(0x94D049BB133111EB)
_S27 = _U(27)
_S30 = _U(30)
_S31 = _U(31)


def _pawn_tables() -> tuple[np.ndarray, np.ndarray]:
    """PT[u, k] = (d, n, jump, side1, t1, side2, t2) as search._pawn_table; PTN[u] = k."""
    dirs = ((0, 1), (0, -1), (1, 0), (-1, 0))
    pt = np.full((81, 4, 7), -1, dtype=np.int64)
    ptn = np.zeros(81, dtype=np.int64)
    for u in range(81):
        c, r = u % N, u // N
        k = 0
        for d, (dc, dr) in enumerate(dirs):
            nc, nr = c + dc, r + dr
            if not (0 <= nc < N and 0 <= nr < N):
                continue
            jc, jr = nc + dc, nr + dr
            sides = []
            for sc, sr in ((dr, dc), (-dr, -dc)):
                sides += [dirs.index((sc, sr)), (nr + sr) * N + nc + sc]
            j = jr * N + jc if 0 <= jc < N and 0 <= jr < N else -1
            pt[u, k] = (d, nr * N + nc, j, *sides)
            k += 1
        ptn[u] = k
    return pt, ptn


PT, PTN = _pawn_tables()


@njit(cache=True)
def _rand(rng):
    s = rng[0] + _GOLDEN
    rng[0] = s
    z = (s ^ (s >> _S30)) * _MIX1
    z = (z ^ (z >> _S27)) * _MIX2
    return z ^ (z >> _S31)


@njit(cache=True)
def _pawn_moves(m, u, o, out):
    """search.pawn_moves_m: target cells of the pawn moves from u (other pawn on o), in
    the order of rules.pawn_moves_for; returns their number."""
    cnt = 0
    for k in range(PTN[u]):
        d = PT[u, k, 0]
        if not _bit(m[2 * d], m[2 * d + 1], u):
            continue
        n = PT[u, k, 1]
        if n != o:
            out[cnt] = n
            cnt += 1
        elif _bit(m[2 * d], m[2 * d + 1], n):
            out[cnt] = PT[u, k, 2]
            cnt += 1
        else:
            s1 = PT[u, k, 3]
            if _bit(m[2 * s1], m[2 * s1 + 1], n):
                out[cnt] = PT[u, k, 4]
                cnt += 1
            s2 = PT[u, k, 5]
            if _bit(m[2 * s2], m[2 * s2 + 1], n):
                out[cnt] = PT[u, k, 6]
                cnt += 1
    return cnt


@njit(cache=True)
def _goal(p):
    return N - 1 if p == 0 else 0


@njit(cache=True)
def _wins_now(m, u, o, p, tmp):
    """Player p on u (the other pawn on o) can step onto its goal row
    (search.opponent_winning_moves is not empty)."""
    g = _goal(p)
    if abs(u // N - g) > 2:
        return False
    k = _pawn_moves(m, u, o, tmp)
    for i in range(k):
        if tmp[i] // N == g:
            return True
    return False


@njit(cache=True)
def _add_bit(b, p):
    return b | (ONE << _U(p))


@njit(cache=True)
def _place(m, hb, vb, w):
    """Masks and wall sets after wall w."""
    m = _with_wall(m, w)
    if w & 1 == 0:
        hb = _add_bit(hb, w >> 1)
    else:
        vb = _add_bit(vb, w >> 1)
    return m, hb, vb


CACHE_BITS = 16  # memo of _race_best / _wall_race results: 2**16 slots (~5 MB)
CACHE_WORDS = 9  # key: the eight mask words + one word (kind, cell, player, walls)
_HMUL = _U(0xFF51AFD7ED558CCD)


@njit(cache=True)
def _slot(m, extra, size):
    h = extra * _GOLDEN
    for i in range(8):
        h = (h ^ m[i]) * _HMUL
        h ^= h >> _S31
    return np.int64(h & _U(size - 1))


@njit(cache=True)
def _hit(ck, slot, m, extra):
    if ck[slot, 8] != extra:
        return False
    for i in range(8):
        if ck[slot, i] != m[i]:
            return False
    return True


@njit(cache=True)
def _store(ck, cv, slot, m, extra, a, b, c):
    for i in range(8):
        ck[slot, i] = m[i]
    ck[slot, 8] = extra
    cv[slot, 0] = a
    cv[slot, 1] = b
    cv[slot, 2] = c


@njit(cache=True)
def _race_best_c(m, hb, vb, cell, b, ll, lh, seen, ck, cv):
    """_race_best memoised in (ck, cv) by (masks, cell, b); always-replace slots. The
    wall sets follow from the masks, so the key is exact."""
    extra = _U(1 + (cell << 2) + (b << 9))
    slot = _slot(m, extra, ck.shape[0])
    if _hit(ck, slot, m, extra):
        return cv[slot, 0], cv[slot, 1], cv[slot, 2], cv[slot, 2] >= 0
    d_b, nb, w, found = _race_best(m, hb, vb, cell, b, ll, lh, seen)
    _store(ck, cv, slot, m, extra, d_b, nb, w)
    return d_b, nb, w, found


@njit(cache=True)
def _race_best(m, hb, vb, cell, b, ll, lh, seen):
    """(d_b, nb, wall, found): B's distance and the first entry of race_cands (largest new
    distance, then (x, y seen from B's side, orient)), without building the list."""
    sl, sh = _cell(cell)
    gl = GOAL[b, 0]
    gh = GOAL[b, 1]
    n = _layers(m, sl, sh, gl, gh, ll, lh)
    d_b = n - 1
    best_key = np.int64(1) << 62
    best_nb = -1
    best_w = -1
    seen[:] = False
    for k in range(d_b):
        if _pop(ll[k]) + _pop(lh[k]) > 2 or _pop(ll[k + 1]) + _pop(lh[k + 1]) > 2:
            continue
        for u in range(81):
            if not _bit(ll[k], lh[k], u):
                continue
            for d in range(4):
                if not _bit(m[2 * d], m[2 * d + 1], u):
                    continue
                if d == 0:
                    j = u + N
                elif d == 1:
                    j = u - N
                elif d == 2:
                    j = u + 1
                else:
                    j = u - 1
                if not _bit(ll[k + 1], lh[k + 1], j):
                    continue
                for e in range(2):
                    w = EW[d, u, e]
                    if w < 0 or seen[w]:
                        continue
                    seen[w] = True
                    if not _fits(hb, vb, w):
                        continue
                    nb = _dist(_with_wall(m, w), sl, sh, gl, gh)
                    if nb != NO_DIST and nb > d_b:
                        p = w >> 1
                        x = p % 8
                        y = p // 8
                        yb = y if b == 0 else 7 - y
                        key = ((200 - nb) << 8) | (x << 4) | (yb << 1) | (w & 1)
                        if key < best_key:
                            best_key = key
                            best_nb = nb
                            best_w = w
    return d_b, best_nb, best_w, best_w >= 0


@njit(cache=True)
def _wall_race(m, hb, vb, bcell, b, n, min_gain, ll, lh, seen, ck, cv):
    """(walls used, B's distance after them): Searcher.wall_race's greedy plan, memoised
    by (masks, B's cell, b, n) like the Python one."""
    extra = _U(2 + (bcell << 2) + (b << 9) + (n << 10) + (min_gain << 20))
    slot = _slot(m, extra, ck.shape[0])
    if _hit(ck, slot, m, extra):
        return cv[slot, 0], cv[slot, 1]
    m0 = m
    used = 0
    while True:
        d_b, nb, w, found = _race_best_c(m, hb, vb, bcell, b, ll, lh, seen, ck, cv)
        if used >= n or not found or nb - d_b < min_gain:
            break
        m, hb, vb = _place(m, hb, vb, w)
        used += 1
    _store(ck, cv, slot, m0, extra, used, d_b, 0)
    return used, d_b


@njit(cache=True)
def _combo_fill(ct, rm, cell, player, ip):
    """Searcher.combo_of on the root walls rm (combo_levels walls), memoised in
    ct[player, cell] = [computed, d0, levels, best_d...]."""
    if ct[player, cell, 0] == 0:
        r = combo(
            rm[0], rm[1], rm[2], rm[3], rm[4], rm[5], rm[6], rm[7],
            cell, player, ip[IP_COMBO_LEVELS], ip[IP_COMBO_BEAM], ip[IP_COMBO_NARROW],
        )  # fmt: skip
        ct[player, cell, 1] = r[0]
        ct[player, cell, 2] = r[1]
        for i in range(r[1]):
            ct[player, cell, 3 + i] = r[2 + i]
        ct[player, cell, 0] = 1


@njit(cache=True)
def _potential(ct, rm, cell, player, walls, d_now, ip):
    """Searcher.potential on the root walls rm (combo_exact = 0)."""
    _combo_fill(ct, rm, cell, player, ip)
    d0 = ct[player, cell, 1]
    levels = ct[player, cell, 2]
    cost = ip[IP_COMBO_COST]
    best = 0
    for j in range(min(walls, levels, ip[IP_COMBO_K])):
        val = (ct[player, cell, 3 + j] - d0) * _STEP - (j + 1) * cost
        if val > best:
            best = val
    if best <= 0:
        return 0
    best -= (d_now - d0) * _STEP
    return best if best > 0 else 0


@njit(cache=True)
def _reserve_threat(own, other, reserve):
    """eval._reserve_threat (RESERVE_SHARE = 4, 2, 1 quarters)."""
    if own == 0:
        return reserve * 4 * other // 4
    if own == 1:
        return reserve * 2 * other // 4
    if own == 2:
        return reserve * other // 4
    return 0


@njit(cache=True)
def _eval(m, hb, vb, cme, cop, wm, wo, me, d_me, d_op, ip, ct, rm, ll, lh, seen, ck, cv):
    """Searcher.evaluate for the side to move `me` (pawn on cme, the opponent on cop)."""
    op = 1 - me
    ww = ip[IP_WALL_WEIGHT]
    score = _STEP * (d_op - d_me) + ww * (wm - wo)
    if ip[IP_V4] == 0:  # eval.evaluate_dists
        if wo == 0 and d_me <= d_op:
            score += _RACE
        elif wm == 0 and d_op < d_me:
            score -= _RACE
        return score
    e_me = d_me
    e_op = d_op
    if (wm == 0) != (wo == 0) and ((d_op < d_me) if wm else (d_me <= d_op)):
        mg = ip[IP_RACE_MIN_GAIN]
        if wo:  # only the opponent (A) has walls: its plan against us (B)
            used, db = _wall_race(m, hb, vb, cme, me, wo, mg, ll, lh, seen, ck, cv)
            e_op = d_op + used
            e_me = db
        else:
            used, db = _wall_race(m, hb, vb, cop, op, wm, mg, ll, lh, seen, ck, cv)
            e_me = d_me + used
            e_op = db
    # eval.evaluate_v4
    plan = ip[IP_RACE_PLAN]
    soft = ip[IP_RACE_SOFT]
    reserve = ip[IP_RESERVE]
    if reserve:
        score += _reserve_threat(wo, wm, reserve) - _reserve_threat(wm, wo, reserve)
    if wo == 0:
        if d_me <= d_op:
            score += _RACE
        elif wm == 0:
            score -= _RACE
        else:
            score += plan * ((e_op - d_op) - (e_me - d_me))
            if e_me <= e_op:
                score += soft
    elif wm == 0:
        if d_op < d_me:
            score -= _RACE
        else:
            score -= plan * ((e_me - d_me) - (e_op - d_op))
            if e_op < e_me:
                score -= soft
    # the terms Searcher.evaluate adds after evaluate_v4 (only while both have walls)
    if ip[IP_THREAT] and wm and wo:
        d1, nb1, _, f1 = _race_best_c(m, hb, vb, cop, op, ll, lh, seen, ck, cv)
        g_me = nb1 - d1 if f1 else 0
        d2, nb2, _, f2 = _race_best_c(m, hb, vb, cme, me, ll, lh, seen, ck, cv)
        g_op = nb2 - d2 if f2 else 0
        score += ip[IP_THREAT] * (g_me - g_op)
    if ip[IP_COMBO] and wm and wo:
        mine = _potential(ct, rm, cop, op, wm, d_op, ip)
        theirs = _potential(ct, rm, cme, me, wo, d_me, ip)
        score += ip[IP_COMBO] * (mine - theirs) // _STEP
    rc = ip[IP_RESERVE_COMBO]
    if rc:  # Searcher._reserve_combo_term (it replaces the flat reserve term of a side)
        lo = ip[IP_RESERVE_MIN]
        if wo >= lo and wm < 3:
            g = _combo_gain(ct, rm, cme, me, wo, d_me, ip)
            score += _reserve_threat(wm, wo, reserve) - rc * _reserve_share(wm) * g // 4
        if wm >= lo and wo < 3:
            g = _combo_gain(ct, rm, cop, op, wm, d_op, ip)
            score += rc * _reserve_share(wo) * g // 4 - _reserve_threat(wo, wm, reserve)
    return score


@njit(cache=True)
def _reserve_share(own):
    """eval.RESERVE_SHARE (quarters) for own = 0, 1, 2."""
    if own == 0:
        return 4
    if own == 1:
        return 2
    return 1


@njit(cache=True)
def _combo_gain(ct, rm, cell, player, walls, d_now, ip):
    """Searcher.combo_gain on the root walls rm."""
    _combo_fill(ct, rm, cell, player, ip)
    j = min(walls, ct[player, cell, 2], ip[IP_RESERVE_K])
    if j <= 0:
        return 0
    g = ct[player, cell, 2 + j] - d_now
    return g if g > 0 else 0


@njit(cache=True)
def _path_s(m, start, player, out, parent, queue):
    """_path with caller-owned scratch arrays (no allocation)."""
    goal = N - 1 if player == 0 else 0
    if start // N == goal:
        out[0] = start
        return 1
    parent[:] = -2
    parent[start] = -1
    queue[0] = start
    head = 0
    tail = 1
    while head < tail:
        i = queue[head]
        head += 1
        for d in range(4):
            if not _bit(m[2 * d], m[2 * d + 1], i):
                continue
            if d == 0:
                j = i + N
            elif d == 1:
                j = i - N
            elif d == 2:
                j = i + 1
            else:
                j = i - 1
            if parent[j] != -2:
                continue
            parent[j] = i
            if j // N == goal:
                n = 0
                while j >= 0:
                    out[n] = j
                    n += 1
                    j = parent[j]
                for a in range(n // 2):
                    t = out[a]
                    out[a] = out[n - 1 - a]
                    out[n - 1 - a] = t
                return n
            queue[tail] = j
            tail += 1
    return 0


@njit(cache=True)
def _dir(a, b):
    if b == a + N:
        return 0
    if b == a - N:
        return 1
    if b == a + 1:
        return 2
    return 3


@njit(cache=True)
def _cand_walls(m, hb, vb, cme, cop, me, d_me, limit, gains, walls, path, parent, queue, seen):
    """cand_walls into gains / walls (best gain first, stable); returns their number."""
    op = 1 - me
    plen = _path_s(m, cop, op, path, parent, queue)
    if plen == 0:
        return 0
    d_op = plen - 1
    sl, sh = _cell(cop)
    msl, msh = _cell(cme)
    seen[:] = False
    cnt = 0
    for i in range(plen - 1):
        a = path[i]
        d = _dir(a, path[i + 1])
        for t in range(2):
            w = EW[d, a, t]
            if w < 0 or seen[w]:
                continue
            seen[w] = True
            if _occupied(hb, vb, w):
                continue
            cm = _with_wall(m, w)
            op_after = _dist(cm, sl, sh, GOAL[op, 0], GOAL[op, 1])
            if op_after == NO_DIST or op_after <= d_op:
                continue
            me_after = _dist(cm, msl, msh, GOAL[me, 0], GOAL[me, 1])
            if me_after == NO_DIST:
                continue
            gain = (op_after - d_op) - (me_after - d_me)
            if gain <= 0:
                continue
            pos = cnt
            while pos > 0 and gains[pos - 1] < gain:
                gains[pos] = gains[pos - 1]
                walls[pos] = walls[pos - 1]
                pos -= 1
            gains[pos] = gain
            walls[pos] = w
            cnt += 1
    n = 0
    for i in range(cnt):
        if not _fits(hb, vb, walls[i]):
            continue
        gains[n] = gains[i]
        walls[n] = walls[i]
        n += 1
        if n >= limit:  # checked after the append, as in the Python code
            break
    return n


@njit(cache=True)
def _saving(m, hb, vb, cme, cop, me, d_me, d_op, gains, walls, seen, tmp, tmp2):
    """Searcher._saving_walls into gains / walls (best gain first, stable)."""
    op = 1 - me
    g = _goal(op)
    k = _pawn_moves(m, cop, cme, tmp)
    ea = np.empty(16, dtype=np.int64)
    eb = np.empty(16, dtype=np.int64)
    ne = 0
    for i in range(k):
        t = tmp[i]
        if t // N != g:
            continue
        if abs(t % N - cop % N) + abs(t // N - cop // N) == 1:
            ea[ne] = cop
            eb[ne] = t
            ne += 1
        else:  # straight jump or diagonal over our pawn
            ea[ne] = cop
            eb[ne] = cme
            ea[ne + 1] = cme
            eb[ne + 1] = t
            ne += 2
    seen[:] = False
    cnt = 0
    for q in range(ne):
        a = ea[q]
        d = _dir(a, eb[q])
        for t in range(2):
            w = EW[d, a, t]
            if w < 0 or seen[w]:
                continue
            seen[w] = True
            if not _fits(hb, vb, w):
                continue
            cm = _with_wall(m, w)
            if _wins_now(cm, cop, cme, op, tmp2):
                continue  # the opponent still wins at once
            op_after = _dist_cell(cm, cop, op)
            me_after = _dist_cell(cm, cme, me)
            if op_after == NO_DIST or me_after == NO_DIST:
                continue
            gain = (op_after - d_op) - (me_after - d_me)
            pos = cnt
            while pos > 0 and gains[pos - 1] < gain:
                gains[pos] = gains[pos - 1]
                walls[pos] = walls[pos - 1]
                pos -= 1
            gains[pos] = gain
            walls[pos] = w
            cnt += 1
    return cnt


@njit(cache=True)
def _finish(node, cnt, mv, lg, order, ex, rng, ne0, nne, emv, epr, ekid, ctr):
    """mcts.MCTS._finish_expand: seeded shuffle, stable sort by logit (best first),
    softmax priors, the edges written after the last ones."""
    for i in range(cnt):
        order[i] = i
    for i in range(cnt - 1, 0, -1):
        j = np.int64(_rand(rng) % _U(i + 1))
        t = order[i]
        order[i] = order[j]
        order[j] = t
    for i in range(1, cnt):
        x = order[i]
        k = i - 1
        while k >= 0 and lg[order[k]] < lg[x]:
            order[k + 1] = order[k]
            k -= 1
        order[k + 1] = x
    start = ctr[1]
    ne0[node] = start
    nne[node] = cnt
    if cnt == 0:
        return
    top = lg[order[0]]
    tot = 0.0
    for i in range(cnt):
        e = math.exp(lg[order[i]] - top)
        ex[i] = e
        tot += e
    for i in range(cnt):
        emv[start + i] = mv[order[i]]
        epr[start + i] = ex[i] / tot
        ekid[start + i] = -1
    ctr[1] = start + cnt


@njit(cache=True)
def _select(node, nn, nw, npv, ne0, nne, epr, ekid, c_puct, fpu_c, pen, use_pen):
    """mcts.MCTS._select: PUCT with FPU; children proven won for the opponent are
    skipped; -1 if every child is."""
    n = nn[node]
    sq = c_puct * math.sqrt(n)
    fpu = nw[node] / n - fpu_c if n else 0.0
    best_i = -1
    best = -1.0e300
    e0 = ne0[node]
    for i in range(nne[node]):
        e = e0 + i
        k = ekid[e]
        if k < 0 or nn[k] == 0:
            q = fpu
            kn = 0
        else:
            if npv[k] == 1:
                continue
            q = -nw[k] / nn[k]
            kn = nn[k]
        if use_pen:
            q -= pen[i]
        u = q + sq * epr[e] / (1 + kn)
        if u > best:
            best = u
            best_i = i
    return best_i


@njit(cache=True)
def _expand(node, m, hb, vb, cme, cop, wm, me, d_me, d_op, k, pawns, fp, ip, mv, lg, order, ex,
            gains, walls, path, parent, queue, seen, tmp, tmp2, rng, ne0, nne, emv, epr, ekid,
            ctr):  # fmt: skip
    """mcts.MCTS._expand_lists of an inner node (k pawn moves in `pawns`): pawn moves, then
    the candidate (or saving) walls; the edges are written by _finish."""
    op = 1 - me
    saving = wm > 0 and _wins_now(m, cop, cme, op, tmp)
    cnt = 0
    for t in range(k):
        d = _dist_cell(m, pawns[t], me)
        mv[cnt] = pawns[t]
        lg[cnt] = fp[FP_PAWN] * (d_me - d)
        cnt += 1
    base = fp[FP_WALL] + (fp[FP_BEHIND] if d_me > d_op else 0.0)
    if saving:
        base += fp[FP_SAVING]
    if wm > 0:
        if saving:
            nwl = _saving(m, hb, vb, cme, cop, me, d_me, d_op, gains, walls, seen, tmp, tmp2)
        else:
            nwl = _cand_walls(m, hb, vb, cme, cop, me, d_me, ip[IP_WALL_LIMIT], gains, walls,
                              path, parent, queue, seen)  # fmt: skip
        for t in range(nwl):
            mv[cnt] = WALL_MOVE + walls[t]
            lg[cnt] = base + fp[FP_GAIN] * gains[t]
            cnt += 1
    _finish(node, cnt, mv, lg, order, ex, rng, ne0, nne, emv, epr, ekid, ctr)


@njit(cache=True)
def mcts_run(
    nn, nw, npv, ne0, nne, emv, epr, ekid, ctr, rng,
    root, rm, rhb, rvb, rpawn, rwl, rturn, fp, ip, pen, use_pen, ct, ck, cv, nsims, done0,
):  # fmt: skip
    """Up to nsims simulations of mcts.MCTS._simulate from node `root` (position: masks rm,
    wall sets rhb / rvb, pawns rpawn, walls left rwl, side to move rturn). ctr = [nodes,
    edges, deepest path]; rng = [splitmix state]; pen = root penalties (use_pen); ct = the
    combination memo of the root walls; ck / cv = the
    _race_best / _wall_race memo (kept between searches); done0 = simulations of this search so far.
    Returns (simulations run, ST_*)."""
    pawns = np.empty(8, dtype=np.int64)
    tmp = np.empty(8, dtype=np.int64)
    tmp2 = np.empty(8, dtype=np.int64)
    mv = np.empty(MAX_MOVES, dtype=np.int64)
    lg = np.empty(MAX_MOVES, dtype=np.float64)
    order = np.empty(MAX_MOVES, dtype=np.int64)
    ex = np.empty(MAX_MOVES, dtype=np.float64)
    gains = np.empty(MAX_MOVES, dtype=np.int64)
    walls = np.empty(MAX_MOVES, dtype=np.int64)
    path = np.empty(82, dtype=np.int64)
    parent = np.empty(81, dtype=np.int64)
    queue = np.empty(81, dtype=np.int64)
    seen = np.zeros(128, dtype=np.bool_)
    ll = np.empty(82, dtype=np.uint64)
    lh = np.empty(82, dtype=np.uint64)
    stack = np.empty(MAX_PATH, dtype=np.int64)
    ncap = nn.size
    ecap = emv.size
    c_puct = fp[FP_C_PUCT]
    fpu_c = fp[FP_FPU]
    lazy = 1 if ip[IP_EXPAND] >= 2 else 0
    status = ST_DONE
    it = 0
    while it < nsims:
        if npv[root] != 0:
            status = ST_PROVEN
            break
        if nne[root] == 1 and done0 + it >= 2:
            status = ST_ONE
            break
        if ctr[0] + 2 > ncap or ctr[1] + MAX_MOVES > ecap:
            status = ST_FULL
            break
        m = (rm[0], rm[1], rm[2], rm[3], rm[4], rm[5], rm[6], rm[7])
        hb = rhb
        vb = rvb
        p0 = rpawn[0]
        p1 = rpawn[1]
        w0 = rwl[0]
        w1 = rwl[1]
        turn = rturn
        node = root
        stack[0] = root
        plen = 1
        while npv[node] == 0:
            if ne0[node] < 0:
                if lazy == 0 or nn[node] == 0:
                    break  # a new leaf
                # expand_visits = 2: the second visit of a leaf expands it, then descends
                me = turn
                cme = p0 if me == 0 else p1
                cop = p1 if me == 0 else p0
                k = _pawn_moves(m, cme, cop, pawns)
                d_me = _dist_cell(m, cme, me)
                d_op = _dist_cell(m, cop, 1 - me)
                wm = w0 if me == 0 else w1
                _expand(node, m, hb, vb, cme, cop, wm, me, d_me, d_op, k,
                        pawns, fp, ip, mv, lg, order, ex, gains, walls, path, parent, queue,
                        seen, tmp, tmp2, rng, ne0, nne, emv, epr, ekid, ctr)  # fmt: skip
            i = _select(node, nn, nw, npv, ne0, nne, epr, ekid, c_puct, fpu_c, pen,
                        use_pen and node == root)  # fmt: skip
            if i < 0:  # every child loses: proven lost
                npv[node] = -1
                break
            e = ne0[node] + i
            code = emv[e]
            if code < WALL_MOVE:
                if turn == 0:
                    p0 = code
                else:
                    p1 = code
            else:
                m, hb, vb = _place(m, hb, vb, code - WALL_MOVE)
                if turn == 0:
                    w0 -= 1
                else:
                    w1 -= 1
            turn = 1 - turn
            kid = ekid[e]
            if kid < 0:
                kid = ctr[0]
                ctr[0] = kid + 1
                nn[kid] = 0
                nw[kid] = 0.0
                npv[kid] = 0
                ne0[kid] = -1
                nne[kid] = 0
                ekid[e] = kid
            node = kid
            stack[plen] = node
            plen += 1
            if nn[kid] == 0 or plen >= MAX_PATH:
                break
        if plen - 1 > ctr[2]:
            ctr[2] = plen - 1
        leaf = node
        if npv[leaf] != 0:
            v = float(npv[leaf])
        elif ne0[leaf] >= 0:  # stopped at MAX_PATH on an expanded node: its mean value
            v = nw[leaf] / nn[leaf]
        else:
            me = turn
            cme = p0 if me == 0 else p1
            cop = p1 if me == 0 else p0
            wm = w0 if me == 0 else w1
            wo = w1 if me == 0 else w0
            if cop // N == _goal(1 - me):  # rules.winner: the previous mover arrived
                npv[leaf] = -1
                v = -1.0
            else:
                k = _pawn_moves(m, cme, cop, pawns)
                g = _goal(me)
                win = False
                for t in range(k):
                    if pawns[t] // N == g:
                        win = True
                        break
                if win:
                    npv[leaf] = 1
                    v = 1.0
                else:
                    d_me = _dist_cell(m, cme, me)
                    d_op = _dist_cell(m, cop, 1 - me)
                    sc = _eval(m, hb, vb, cme, cop, wm, wo, me, d_me, d_op, ip, ct, rm,
                               ll, lh, seen, ck, cv)  # fmt: skip
                    v = math.tanh((sc + fp[FP_TEMPO]) / fp[FP_SCALE])
                    if lazy == 0 or nn[leaf] > 0:
                        _expand(leaf, m, hb, vb, cme, cop, wm, me, d_me, d_op, k, pawns, fp, ip,
                                mv, lg, order, ex, gains, walls, path, parent, queue, seen,
                                tmp, tmp2, rng, ne0, nne, emv, epr, ekid, ctr)  # fmt: skip
        # backup (values for each node's side to move) and proof propagation
        child_proven = npv[leaf]
        for t in range(plen - 1, -1, -1):
            nd = stack[t]
            nn[nd] += 1
            nw[nd] += v
            v = -v
            if t != plen - 1 and npv[nd] == 0 and child_proven != 0:
                if child_proven == -1:
                    npv[nd] = 1
                else:
                    every = True
                    e0 = ne0[nd]
                    for i in range(nne[nd]):
                        kk = ekid[e0 + i]
                        if kk < 0 or npv[kk] != 1:
                            every = False
                            break
                    if every:
                        npv[nd] = -1
            child_proven = npv[nd]
        it += 1
    return it, status


@njit(cache=True)
def mcts_pv(node, nn, ne0, nne, ekid):
    """mcts.MCTS._pv_len: length of the most visited line from `node` (at least 1)."""
    d = 0
    while ne0[node] >= 0 and nn[node] > 1:
        best = -1
        bn = -1
        e0 = ne0[node]
        for i in range(nne[node]):
            k = ekid[e0 + i]
            if k >= 0 and (best < 0 or nn[k] > bn):
                best = k
                bn = nn[k]
        if best < 0:
            break
        d += 1
        node = best
    return max(1, d)


@njit(cache=True)
def mcts_count(root, ne0, nne, ekid):
    """(nodes, edges) of the subtree of `root`."""
    stack = np.empty(1024, dtype=np.int64)
    stack[0] = root
    top = 1
    nodes = 0
    edges = 0
    while top > 0:
        top -= 1
        x = stack[top]
        nodes += 1
        if ne0[x] < 0:
            continue
        edges += nne[x]
        for i in range(nne[x]):
            k = ekid[ne0[x] + i]
            if k >= 0:
                if top >= stack.size:
                    bigger = np.empty(stack.size * 2, dtype=np.int64)
                    bigger[:top] = stack[:top]
                    stack = bigger
                stack[top] = k
                top += 1
    return nodes, edges


@njit(cache=True)
def mcts_compact(
    root, nn, nw, npv, ne0, nne, emv, epr, ekid, dn, dw, dp, de0, dne, dmv, dpr, dkid,
):  # fmt: skip
    """Copy the subtree of `root` into the d* arrays (root -> node 0, breadth first);
    returns (nodes, edges)."""
    queue = np.empty(dn.size, dtype=np.int64)
    queue[0] = root
    head = 0
    tail = 1
    edges = 0
    while head < tail:
        x = queue[head]
        y = head
        head += 1
        dn[y] = nn[x]
        dw[y] = nw[x]
        dp[y] = npv[x]
        if ne0[x] < 0:
            de0[y] = -1
            dne[y] = 0
            continue
        de0[y] = edges
        dne[y] = nne[x]
        e0 = ne0[x]
        for i in range(nne[x]):
            dmv[edges + i] = emv[e0 + i]
            dpr[edges + i] = epr[e0 + i]
            k = ekid[e0 + i]
            if k >= 0:
                queue[tail] = k
                dkid[edges + i] = tail
                tail += 1
            else:
                dkid[edges + i] = -1
        edges += nne[x]
    return tail, edges


# ---------- endgame solver (phase 9, step 5) ----------
#
# Exact port of quoridor.endgame._PyCore (see that module): depth-limited negamax over all
# legal moves with node-relative mate values, exact cuts, the TT (keys of
# quoridor.endgame.tt_key, always replace) and the same move order, so the two cores
# visit the same nodes. eg_search is the root call; ctr = [nodes, limit, aborted, best
# move of the root]; mvbuf / kbuf hold the ordered moves of each ply, lay the shortest
# path layers of the node being expanded (used before descending only). No recursion: a
# self-recursive njit function crashes when numba loads it from the disk cache.

EG_MATE = 1000
EG_INF = 30000
EG_LOWER = 1
EG_UPPER = 2
EG_K_TT = 100_000
EG_K_STEP_AHEAD = 6_000
EG_K_STEP_BEHIND = 2_500
EG_K_OTHER_PAWN = 500
EG_K_WALL_GAIN = 2_000
EG_K_WALL_NONE = -1_000
EG_K_WALL_QUIET = -2_000
_EG_CV = _U(0x5851F42D4C957F2D)
_EG_CS = _U(0x2545F4914F6CDD1D)


@njit(cache=True)
def _eg_mix(x):
    z = x + _GOLDEN
    z = (z ^ (z >> _S30)) * _MIX1
    z = (z ^ (z >> _S27)) * _MIX2
    return z ^ (z >> _S31)


@njit(cache=True)
def _eg_key(hb, vb, u0, u1, w0, w1, p):
    small = _U(u0 | (u1 << 7) | (w0 << 14) | (w1 << 18) | (p << 22))
    k = _eg_mix(hb) ^ _eg_mix(vb ^ _EG_CV) ^ _eg_mix(small ^ _EG_CS)
    if k == ZERO:
        k = ONE
    return k


@njit(cache=True)
def _eg_adj(s):
    if s > 0:
        return -s + 1
    if s < 0:
        return -s - 1
    return 0


@njit(cache=True)
def _eg_inv(v):
    if v >= EG_INF:
        return -EG_INF
    if v <= -EG_INF:
        return EG_INF
    if v > 0:
        return -(v + 1)
    if v < 0:
        return 1 - v
    return 0


@njit(cache=True)
def _eg_can(d, k, man, t0):
    """endgame.can_reach."""
    if k <= 0:
        return False
    need = man - 1 - 2 * t0
    i0 = 0 if need <= 0 else (need + 3) // 4
    j = k - i0 if k > i0 else 0
    return d <= k + j


@njit(cache=True)
def _shr(lo, hi, s):
    return (lo >> _U(s)) | (hi << _U(64 - s)), hi >> _U(s)


@njit(cache=True)
def _eg_edges(ll, lh, n):
    """endgame._edges on n layers: (ev_lo, ev_hi, eh_lo, eh_hi)."""
    evl = ZERO
    evh = ZERO
    ehl = ZERO
    ehh = ZERO
    for k in range(n - 1):
        al = ll[k]
        ah = lh[k]
        bl = ll[k + 1]
        bh = lh[k + 1]
        b9l, b9h = _shr(bl, bh, 9)
        a9l, a9h = _shr(al, ah, 9)
        evl |= (al & b9l) | (bl & a9l)
        evh |= (ah & b9h) | (bh & a9h)
        b1l, b1h = _shr(bl, bh, 1)
        a1l, a1h = _shr(al, ah, 1)
        ehl |= (al & b1l) | (bl & a1l)
        ehh |= (ah & b1h) | (bh & a1h)
    return evl, evh, ehl, ehh


@njit(cache=True)
def _eg_touch(el, eh, i, w):
    """Wall w (first cell i) blocks an edge of the set (el, eh): h walls test the
    vertical edges at i and i + 1, v walls the horizontal ones at i and i + 9."""
    j = i + 1 if w & 1 == 0 else i + 9
    return _bit(el, eh, i) or _bit(el, eh, j)


_F_U0 = 0
_F_U1 = 1
_F_W0 = 2
_F_W1 = 3
_F_P = 4
_F_R = 5
_F_ALPHA = 6
_F_BETA = 7
_F_A0 = 8
_F_BEST = 9
_F_BM = 10
_F_I = 11
_F_CNT = 12
_F_IDX = 13
EG_FIELDS = 14


@njit(cache=True)
def _eg_enter(ply, sm, si, sk, shift, tk, tv, td, tf, tm, ctr, mvbuf, kbuf, lay):
    """The part of endgame._PyCore._search before the move loop for the node on `ply`
    (position in sm[ply] / si[ply], window in si[ply]): (True, value) if the node is
    decided here, else (False, 0) with its ordered moves in mvbuf[ply] and the loop
    fields of si[ply] set."""
    ctr[0] += 1
    if ctr[0] > ctr[1]:
        ctr[2] = 1
        return True, 0
    row = si[ply]
    r = row[_F_R]
    if r <= 0:
        return True, 0
    srow = sm[ply]
    m = (srow[0], srow[1], srow[2], srow[3], srow[4], srow[5], srow[6], srow[7])
    hb = srow[8]
    vb = srow[9]
    u0 = row[_F_U0]
    u1 = row[_F_U1]
    w0 = row[_F_W0]
    w1 = row[_F_W1]
    p = row[_F_P]
    alpha = row[_F_ALPHA]
    beta = row[_F_BETA]
    if p == 0:
        u = u0
        o = u1
        goal = 8
    else:
        u = u1
        o = u0
        goal = 0
    mv = mvbuf[ply]
    kb = kbuf[ply]
    npm = _pawn_moves(m, u, o, mv)
    for i in range(npm):
        if mv[i] // N == goal:
            return True, EG_MATE - 1
    if r == 1:
        return True, 0
    q = 1 - p
    mywalls = w0 if p == 0 else w1
    sl, sh = _cell(u)
    n_me = _layers(m, sl, sh, GOAL[p, 0], GOAL[p, 1], lay[0], lay[1])
    d_me = n_me - 1
    ol, oh = _cell(o)
    if mywalls > 0:
        n_op = _layers(m, ol, oh, GOAL[q, 0], GOAL[q, 1], lay[2], lay[3])
        d_op = n_op - 1
    else:
        n_op = 0
        d_op = _dist(m, ol, oh, GOAL[q, 0], GOAL[q, 1])
    man = abs(u % N - o % N) + abs(u // N - o // N)
    can_me = _eg_can(d_me, (r + 1) // 2, man, 0)
    can_op = _eg_can(d_op, r // 2, man, 1)
    if not can_me and not can_op:
        return True, 0
    hi = EG_MATE - 3 if can_me else 0
    lo_b = -(EG_MATE - 2) if can_op else 0
    if hi <= alpha:
        return True, hi
    if lo_b >= beta:
        return True, lo_b
    if alpha < lo_b:
        alpha = lo_b
    if beta > hi:
        beta = hi
    key = _eg_key(hb, vb, u0, u1, w0, w1, p)
    idx = np.int64(key >> _U(shift))
    ttm = -1
    if tk[idx] == key:
        t_v = np.int64(tv[idx])
        t_d = np.int64(td[idx])
        t_f = tf[idx]
        ttm = np.int64(tm[idx])
        if t_f == 0:
            if t_v != 0:
                if EG_MATE - abs(t_v) <= r:
                    return True, t_v
                return True, 0
            if r <= t_d:
                return True, 0
        elif t_f == EG_LOWER:
            if (t_v <= 0 and r <= t_d) or (t_v > 0 and r >= t_d):
                if t_v >= beta:
                    return True, t_v
                if t_v > alpha:
                    alpha = t_v
        elif (t_v >= 0 and r <= t_d) or (t_v < 0 and r >= t_d):
            if t_v <= alpha:
                return True, t_v
            if t_v < beta:
                beta = t_v
    # moves: the pawn targets are already in mv[0:npm]
    step = EG_K_STEP_AHEAD if d_me <= d_op else EG_K_STEP_BEHIND
    for i in range(npm):
        c = mv[i]
        if d_me >= 1 and _bit(lay[0][1], lay[1][1], c):
            k = step + 100
        elif d_me >= 2 and _bit(lay[0][2], lay[1][2], c):
            k = step + 200
        else:
            k = EG_K_OTHER_PAWN
        kb[i] = EG_K_TT if c == ttm else k
    cnt = npm
    if mywalls > 0:
        evpl, evph, ehpl, ehph = _eg_edges(lay[0], lay[1], n_me)
        evol, evoh, ehol, ehoh = _eg_edges(lay[2], lay[3], n_op)
        for w in range(128):
            if not _fits(hb, vb, w):
                continue
            pp = w >> 1
            i = (pp // 8) * N + pp % 8
            if w & 1 == 0:
                tp = _eg_touch(evpl, evph, i, w)
                to = _eg_touch(evol, evoh, i, w)
            else:
                tp = _eg_touch(ehpl, ehph, i, w)
                to = _eg_touch(ehol, ehoh, i, w)
            if tp or to:
                m2 = _with_wall(m, w)
                d1 = d_me
                if tp:
                    d1 = _dist(m2, sl, sh, GOAL[p, 0], GOAL[p, 1])
                    if d1 < 0:
                        continue
                d2 = d_op
                if to:
                    d2 = _dist(m2, ol, oh, GOAL[q, 0], GOAL[q, 1])
                    if d2 < 0:
                        continue
                g = (d2 - d_op) - (d1 - d_me)
                if g > 0:
                    k = EG_K_WALL_GAIN + 1000 * g
                else:
                    k = EG_K_WALL_NONE + 100 * g
            else:
                k = EG_K_WALL_QUIET
            code = WALL_MOVE + w
            mv[cnt] = code
            kb[cnt] = EG_K_TT if code == ttm else k
            cnt += 1
    if cnt == 0:
        return True, 0
    # stable insertion sort, keys descending
    for i in range(1, cnt):
        kk = kb[i]
        cc = mv[i]
        j = i - 1
        while j >= 0 and kb[j] < kk:
            kb[j + 1] = kb[j]
            mv[j + 1] = mv[j]
            j -= 1
        kb[j + 1] = kk
        mv[j + 1] = cc
    row[_F_ALPHA] = alpha
    row[_F_BETA] = beta
    row[_F_A0] = alpha
    row[_F_BEST] = -EG_INF
    row[_F_BM] = -1
    row[_F_I] = 0
    row[_F_CNT] = cnt
    row[_F_IDX] = idx
    sk[ply] = key
    return False, 0


@njit(cache=True)
def _eg_child(ply, sm, si, c):
    """Position of move c of the node on `ply` into ply + 1 (window from the parent's)."""
    a = si[ply]
    b = si[ply + 1]
    sa = sm[ply]
    sb = sm[ply + 1]
    for k in range(10):
        sb[k] = sa[k]
    b[_F_U0] = a[_F_U0]
    b[_F_U1] = a[_F_U1]
    b[_F_W0] = a[_F_W0]
    b[_F_W1] = a[_F_W1]
    p = a[_F_P]
    b[_F_P] = 1 - p
    b[_F_R] = a[_F_R] - 1
    b[_F_ALPHA] = _eg_inv(a[_F_BETA])
    b[_F_BETA] = _eg_inv(a[_F_ALPHA])
    if c < WALL_MOVE:
        if p == 0:
            b[_F_U0] = c
        else:
            b[_F_U1] = c
        return
    w = c - WALL_MOVE
    for k in range(8):
        sb[k] = sa[k] & ~WB[w, k]
    if w & 1 == 0:
        sb[8] = _add_bit(sa[8], w >> 1)
    else:
        sb[9] = _add_bit(sa[9], w >> 1)
    if p == 0:
        b[_F_W0] -= 1
    else:
        b[_F_W1] -= 1


@njit(cache=True)
def eg_search(m0, m1, m2, m3, m4, m5, m6, m7, hb, vb, u0, u1, w0, w1, p, r, limit, shift,
              tk, tv, td, tf, tm, ctr, mvbuf, kbuf, lay, sm, si, sk):  # fmt: skip
    """endgame._PyCore.root without recursion (numba cannot load a cached self-recursive
    function): the value of a depth-r search, the same nodes in the same order.
    ctr = [nodes, limit, aborted, best move of the root (-1: none)]; sm / si / sk: the
    per-ply node stack (EG_FIELDS integer fields, masks + wall sets, TT key)."""
    ctr[0] = 0
    ctr[1] = limit
    ctr[2] = 0
    ctr[3] = -1
    s0 = sm[0]
    s0[0] = m0
    s0[1] = m1
    s0[2] = m2
    s0[3] = m3
    s0[4] = m4
    s0[5] = m5
    s0[6] = m6
    s0[7] = m7
    s0[8] = hb
    s0[9] = vb
    r0 = si[0]
    r0[_F_U0] = u0
    r0[_F_U1] = u1
    r0[_F_W0] = w0
    r0[_F_W1] = w1
    r0[_F_P] = p
    r0[_F_R] = r
    r0[_F_ALPHA] = -EG_INF
    r0[_F_BETA] = EG_INF
    ply = 0
    value = 0
    entering = True
    while True:
        if entering:
            done, value = _eg_enter(ply, sm, si, sk, shift, tk, tv, td, tf, tm, ctr, mvbuf,
                                    kbuf, lay)  # fmt: skip
            if ctr[2] != 0:
                return 0
            if not done:
                entering = False
                row = si[ply]
                row[_F_I] = 1
                _eg_child(ply, sm, si, mvbuf[ply][0])
                ply += 1
                entering = True
                continue
        else:
            # value of the child just finished, back on its parent
            row = si[ply]
            v = _eg_adj(value)
            cut = False
            if v > row[_F_BEST]:
                row[_F_BEST] = v
                row[_F_BM] = mvbuf[ply][row[_F_I] - 1]
                if v > row[_F_ALPHA]:
                    row[_F_ALPHA] = v
                    if v >= row[_F_BETA]:
                        cut = True
            if not cut and row[_F_I] < row[_F_CNT]:
                i = row[_F_I]
                row[_F_I] = i + 1
                _eg_child(ply, sm, si, mvbuf[ply][i])
                ply += 1
                entering = True
                continue
            best = row[_F_BEST]
            if best >= row[_F_BETA]:
                flag = EG_LOWER
            elif best <= row[_F_A0]:
                flag = EG_UPPER
            else:
                flag = 0
            idx = row[_F_IDX]
            tk[idx] = sk[ply]
            tv[idx] = best
            td[idx] = row[_F_R]
            tf[idx] = flag
            tm[idx] = row[_F_BM]
            value = best
        # `value` is the value of the node on `ply`: return it to the parent
        if ply == 0:
            break
        ply -= 1
        entering = False
    if ctr[2] == 0:
        key = _eg_key(hb, vb, u0, u1, w0, w1, p)
        idx = np.int64(key >> _U(shift))
        if tk[idx] == key:
            ctr[3] = tm[idx]
    return value
