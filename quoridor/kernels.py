"""numba kernels (phase 9, step 6): exact ports of the hot pure-Python code.

Imported only when quoridor.jit.ENABLED (needs numba and numpy). Every kernel returns
exactly what its Python reference returns (tests/paths/test_jit.py compares them):

  distance, shortest_layers, shortest_path   <- quoridor.bitpaths (same names)
  race_cands                                 <- search.Searcher._race_cands (Python part)
  combo                                      <- quoridor.threat.combo_threat
  cand_walls                                 <- search.Searcher.candidate_walls (no threat
                                                branch, children are built by the caller)

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
