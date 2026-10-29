"""Phase 9 (report 0085): mcts3 = mcts2 + reserve_combo in the leaf eval (the reserve term on
the opponent's best wall combination instead of its wall count)."""

import pytest

from quoridor import jit
from quoridor.bitpaths import build_masks
from quoridor.bots import (
    BOTS,
    MCTS2_OPTIONS,
    MCTS3_OPTIONS,
    MCTS3_RESERVE_COMBO,
    MCTS3_SEARCHER,
    MCTS_SEARCHER,
    make_bot,
)
from quoridor.eval import RESERVE_SHARE
from quoridor.mcts import MCTS
from quoridor.search import RESERVE_K, Searcher
from quoridor.threat import combo_threat
from tests.positions.loader import load_positions, state_at
from tests.search.test_search_speed import positions

needs_jit = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")
BY_ID = {p.id: p for p in load_positions()}


def pos(pid: str):
    p = BY_ID[pid]
    return state_at(p.game, p.ply)


def searcher(**over) -> Searcher:
    return Searcher(0, **{**MCTS_SEARCHER, **over})


def evaluate(se: Searcher, s) -> int:
    se._reset(s)
    try:
        return se.evaluate(s)
    finally:
        se._free()


@pytest.fixture(scope="module")
def sample():
    return positions([81, 82, 83], every=3, max_plies=90, wall_bias=0.75)


def test_registry_and_settings():
    assert {"mcts3", "mcts3-clock", "mcts3-0.2s", "mcts3-3s"} <= set(BOTS)
    b = make_bot("mcts3-3s", 0)
    s = b.mcts.s
    assert b.move_time == 3.0
    assert s.reserve_combo == MCTS3_RESERVE_COMBO and s.reserve_k == RESERVE_K
    assert s.reserve == MCTS3_SEARCHER["reserve"] and s.reserve_min == MCTS3_SEARCHER["reserve_min"]
    assert s.combo_levels == max(s.combo_k, RESERVE_K)
    assert MCTS3_OPTIONS == MCTS2_OPTIONS  # only the leaf eval differs from mcts2
    assert {k: v for k, v in MCTS3_SEARCHER.items() if not k.startswith("reserve")} == {
        k: v for k, v in MCTS_SEARCHER.items() if not k.startswith("reserve")
    }
    assert make_bot("mcts3", 0).sims is not None and make_bot("mcts3-clock", 0).game_time
    # mcts2 is untouched: the flat reserve, no reserve_combo, combo levels = combo_k
    m2 = make_bot("mcts2-3s", 0).mcts.s
    assert m2.reserve == MCTS_SEARCHER["reserve"] and m2.reserve_combo == 0
    assert m2.combo_levels == m2.combo_k


def test_off_by_default_and_deeper_memo_keeps_the_potential(sample):
    """reserve_combo = 0 changes nothing; the deeper combination memo of reserve_combo
    (combo_levels = reserve_k) keeps the potential of the first combo_k levels."""
    base = searcher()
    deep = searcher(reserve_combo=1, reserve_k=7)
    assert base.combo_levels == base.combo_k < deep.combo_levels == 7
    for s in sample:
        assert evaluate(searcher(reserve_combo=0), s) == evaluate(base, s)
        for se in (base, deep):
            se._reset(s)
        for p in (0, 1):
            d = base.dist(s, p)
            for walls in (1, 2, 3, 10):
                assert base.potential(s.pawns[p], p, walls, d) == deep.potential(
                    s.pawns[p], p, walls, d
                ), (s, p, walls)
        base._free()
        deep._free()


@pytest.mark.parametrize("lo", [0, 3])
def test_combo_gain_and_the_term(sample, lo):
    """combo_gain = the best combination of min(walls, reserve_k) walls on the root walls;
    while the opponent has >= reserve_min walls, the side with 0-2 walls loses
    reserve_combo * RESERVE_SHARE[own] / 4 per step of it instead of the flat reserve."""
    r, k, flat_r = 40, 5, 50
    seen_lo = seen_hi = False
    for s in sample:
        se = searcher(reserve=flat_r, reserve_combo=r, reserve_k=k, reserve_min=lo)
        flat = searcher(reserve=flat_r)
        se._reset(s)
        m = build_masks(s.h_walls, s.v_walls)
        gains = []
        for p in (0, 1):
            other = s.walls_left[1 - p]
            d0, ds, _ = combo_threat(
                m,
                s.h_walls,
                s.v_walls,
                s.pawns[p],
                p,
                se.combo_levels,
                se.combo_beam,
                se.combo_narrow,
            )
            j = min(other, len(ds), k)
            want = ds[j - 1] - d0 if j else 0
            assert se.combo_gain(s.pawns[p], p, other, d0) == want
            gains.append(want)
        me = s.turn
        wm, wo = s.walls_left[me], s.walls_left[1 - me]
        term = 0
        if wo >= max(1, lo) and wm < 3:
            term += flat_r * RESERVE_SHARE[wm] * wo // 4 - r * RESERVE_SHARE[wm] * gains[me] // 4
            seen_hi |= wo >= 3
            seen_lo |= wo < 3
        if wm >= max(1, lo) and wo < 3:
            term += (
                r * RESERVE_SHARE[wo] * gains[1 - me] // 4 - flat_r * RESERVE_SHARE[wo] * wm // 4
            )
        se._free()
        assert evaluate(se, s) - evaluate(flat, s) == term, s
    assert seen_hi and (seen_lo or lo)


def test_game07_cage_and_game03_corridor():
    """The point of reserve_combo: seven walls that can cage white (game07, move 25) weigh
    more than the flat reserve says; seven walls against black's one-cell corridor
    (game03, move 17) weigh less."""
    flat = searcher()
    combo = Searcher(0, **MCTS3_SEARCHER)
    g07 = pos("g07-m25")  # white to move, 2 walls against 7
    assert evaluate(combo, g07) < evaluate(flat, g07) - 200
    g03 = pos("g03-m17")  # white to move, 7 walls against 0
    assert evaluate(combo, g03) < evaluate(flat, g03) - 100


@needs_jit
def test_jit_equals_python_with_reserve_combo(sample):
    from collections import Counter

    def snap(m, r):
        tree = Counter((st, tuple(mv)) for st, mv in m._iter_tree())
        return (r.move, r.score, r.depth, r.nodes, r.visits, r.max_depth), m.root_children(), tree

    cases = list(sample[::3]) + [pos("g07-m25"), pos("g07-m27"), pos("g03-m16"), pos("g06-p37")]
    for i, s in enumerate(cases):
        out = []
        for core in ("jit", "py"):
            m = MCTS(i, MCTS3_SEARCHER, core, **MCTS3_OPTIONS)
            out.append(snap(m, m.search(s, sims=250)))
        assert out[0] == out[1], (i, s)


@pytest.mark.slow
@needs_jit
@pytest.mark.parametrize("pid", ["g07-m25", "g07-m27"])
def test_game07_moves_25_and_27_not_positive(pid):
    """Phase 9 criterion: game07 moves 24-30, white's evaluation <= 0 or no move into the
    cage. From d7 / e7 white has no pawn move out of the cage, mcts2 gave +2.0 / +1.2 at
    10 s; mcts3 is <= 0 already at 400 000 simulations (deterministic, no clock)."""
    p = BY_ID[pid]
    m = MCTS(0, MCTS3_SEARCHER, **MCTS3_OPTIONS)
    m.set_history([state_at(p.game, i) for i in range(p.ply)])
    r = m.search(pos(pid), sims=400_000)
    assert r.score <= 0
