"""Phase 14 (report 0126): mcts4 = mcts3 + walls off the shortest paths in the tree
(quoridor.threat.wall_scan at the root and at widened nodes of depth 1..widen) and a visit
floor for wall children. The jit core must build exactly the tree of the Python core."""

from collections import Counter

import pytest

from quoridor import jit, threat
from quoridor.bitpaths import GOAL_MASK, bit, build_masks, distance
from quoridor.bots import (
    BOTS,
    MCTS3_OPTIONS,
    MCTS3_SEARCHER,
    MCTS4_OPTIONS,
    MCTS4_SEARCHER,
    make_bot,
)
from quoridor.mcts import MCTS, MCTS_DEFAULTS, MCTSBot
from quoridor.notation import format_move
from quoridor.rules import apply_move, is_legal_move, pawn_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from quoridor.threat import wall_scan
from tests.positions.loader import load_positions, state_at
from tests.search.test_search_speed import positions

needs_jit = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")
CORES = ["py", pytest.param("jit", marks=needs_jit)]
# small thresholds so that a few hundred simulations widen nodes and hit the floors
SMALL = {**MCTS4_OPTIONS, "widen_visits": 12, "floor_prior": 0.3}


def mcts(seed: int = 0, core: str | None = None, **opts) -> MCTS:
    return MCTS(seed, MCTS4_SEARCHER, core, **{**SMALL, **opts})


def state(white, black, turn, walls=(10, 10), h=(), v=()):
    return GameState((white, black), frozenset(h), frozenset(v), walls, turn)


def scan_of(s: GameState, k: int = 3) -> threat.ScanResult:
    me = s.turn
    m = build_masks(s.h_walls, s.v_walls)
    return wall_scan(
        m, s.h_walls, s.v_walls, s.pawns[me], s.pawns[1 - me], me, s.walls_left, k, 3, 3
    )


def names(ranked) -> list[str]:
    return [format_move(WallMove(*w)) for _, w in ranked]


@pytest.fixture(scope="module")
def sample():
    return positions([91, 92, 93], every=3, max_plies=90, wall_bias=0.75)


# ---------- registry ----------


def test_registry_and_settings():
    assert {"mcts4", "mcts4-clock", "mcts4-0.2s", "mcts4-3s"} <= set(BOTS)
    b = make_bot("mcts4-3s", 0)
    m = b.mcts
    assert b.move_time == 3.0 and make_bot("mcts4-0.2s", 0).move_time == 0.2
    assert make_bot("mcts4", 0).sims is not None and make_bot("mcts4-clock", 0).game_time
    assert m.scan == MCTS4_OPTIONS["scan"] > 0 and m.widen == MCTS4_OPTIONS["widen"] > 0
    assert m.floor_prior == MCTS4_OPTIONS["floor_prior"] > 0
    assert MCTS4_SEARCHER == MCTS3_SEARCHER  # the leaf eval is mcts3's
    assert {k: v for k, v in MCTS4_OPTIONS.items() if k in MCTS3_OPTIONS} == MCTS3_OPTIONS
    # mcts3 is untouched: every mcts4 option at its (off) default
    m3 = make_bot("mcts3-3s", 0).mcts
    assert m3.scan == 0 and m3.widen == 0 and m3.floor == 0 and m3.floor_prior == 0
    for key in ("scan", "widen", "floor", "floor_prior", "floor_depth", "scan_logit"):
        assert not MCTS_DEFAULTS[key]


def test_widen_needs_scan():
    assert MCTS(0, MCTS4_SEARCHER, None, widen=2).widen == 0


# ---------- threat.wall_scan ----------


def test_scan_lists_only_legal_walls_with_the_promised_terms(sample):
    for s in sample[::3]:
        if winner(s) is not None or s.walls_left[s.turn] == 0:
            continue
        r = scan_of(s)
        for ranked in (r.attack, r.defend):
            for _, (x, y, o) in ranked:
                assert is_legal_move(s, WallMove(x, y, o)), (s, x, y, o)
        assert all(key[0] >= 0 for key, _ in r.attack)
        assert all(key[0] >= 1 for key, _ in r.defend)
        for ranked in (r.attack, r.defend):  # best first
            keys = [(-k[0], -k[1], w) for k, w in ranked]
            assert keys == sorted(keys)
        if s.walls_left[1 - s.turn] == 0:
            assert r.defend == ()


def test_scan_skips_walls_that_hurt_us_more(sample):
    for s in sample[::4]:
        if winner(s) is not None or s.walls_left[s.turn] == 0:
            continue
        me = s.turn
        m = build_masks(s.h_walls, s.v_walls)
        d_me = distance(m, bit(s.pawns[me]), GOAL_MASK[me])
        d_op = distance(m, bit(s.pawns[1 - me]), GOAL_MASK[1 - me])
        r = scan_of(s)
        for _, (x, y, o) in r.attack + r.defend:
            c = apply_move(s, WallMove(x, y, o))
            cm = build_masks(c.h_walls, c.v_walls)
            me_after = distance(cm, bit(c.pawns[me]), GOAL_MASK[me])
            op_after = distance(cm, bit(c.pawns[1 - me]), GOAL_MASK[1 - me])
            assert me_after - d_me <= op_after - d_op


def test_scan_without_walls_is_empty():
    s = state((4, 3), (4, 5), WHITE, walls=(0, 5))
    assert scan_of(s) == threat.ScanResult((), ())


@needs_jit
def test_scan_on_the_python_beam_equals_the_jit_beam(sample, monkeypatch):
    got = [scan_of(s) for s in sample[::3] if winner(s) is None and s.walls_left[s.turn]]
    monkeypatch.setattr(threat, "combo_threat", threat.py_combo_threat)
    ref = [scan_of(s) for s in sample[::3] if winner(s) is None and s.walls_left[s.turn]]
    assert got == ref and len(got) >= 5


def test_scan_finds_the_walls_of_the_live_games():
    """Reports 0123 / 0124: walls off the shortest paths that decided live games."""
    # game09 / game12, 6... a7h: the first wall of black's cage (with d5h)
    assert "a7h" in names(scan_of(state_at("game09", 11)).attack)
    # game08 8... d9v: takes the square white's best combination against black needs
    assert "d9v" in names(scan_of(state_at("game08", 15)).defend)[:3]
    # game11 9. h8v: breaks black's cage h8h-f8h-d8h
    assert "h8v" in names(scan_of(state_at("game11", 16)).defend)[:3]
    # game09 6. d5v (better than d6v): the best defence, d6v is no defence at all
    d = names(scan_of(state_at("game09", 10)).defend)
    assert d[0] == "d5v" and "d6v" not in d


def test_scan_min_drops_small_defences():
    """g03-m16 (phase 9 set): black has one wall; the 2-step defences against it were
    mirages at the root of mcts4 with scan_min 1 (report 0126)."""
    p = next(x for x in load_positions() if x.id == "g03-m16")
    s = state_at(p.game, p.ply)
    m = MCTS(0, MCTS4_SEARCHER, None, **{**MCTS4_OPTIONS, "scan_min": 1})
    small = m._scan(s, m.s.masks(s)).defend
    assert small and max(k[0] for k, _ in small) < 3
    m3 = mcts()
    assert m3.scan_min == 3 and m3._scan(s, m3.s.masks(s)).defend == ()


# ---------- the search ----------


@pytest.mark.parametrize("core", CORES)
def test_root_gets_the_scan_walls_with_their_bonus(core):
    s = state_at("game08", 15)  # d9v is not a candidate or a root extra of mcts3
    m3 = MCTS(0, MCTS3_SEARCHER, core, **MCTS3_OPTIONS)
    m3.search(s, sims=50)
    assert "d9v" not in [format_move(c.move) for c in m3.root_children()]
    m4 = mcts(core=core)
    m4.search(s, sims=50)
    assert "d9v" in [format_move(c.move) for c in m4.root_children()]


@pytest.mark.parametrize("core", CORES)
def test_widened_nodes_keep_their_children_and_get_the_scan_walls(core):
    m = mcts(core=core)
    s = state_at("game09", 10)
    m.search(s, sims=600)
    wide = 0
    for st, moves in m._iter_tree():
        for mv in moves:
            assert is_legal_move(st, mv)
        assert len(set(moves)) == len(moves)  # no move twice after widening
    if core == "py":
        stack = [(m._root, 0)]
        while stack:
            node, depth = stack.pop()
            if node is None or node.moves is None:
                continue
            if node.wide:
                wide += 1
                assert 1 <= depth <= m.widen and node.n >= m.widen_visits
                r = m._scan(node.state, m.s.masks(node.state))
                top = [WallMove(*w) for _, w in r.attack[: m.scan] + r.defend[: m.scan]]
                assert set(top) <= set(node.moves)
            stack.extend((k, depth + 1) for k in node.kids)
    else:
        wide = int(m._tree.nwd[: int(m._tree.ctr[0])].sum())
    assert wide >= 1


@pytest.mark.parametrize("core", CORES)
def test_visit_floor_of_the_root_walls(core):
    m = mcts(core=core, floor_prior=0.5)
    r = m.search(state_at("game08", 15), sims=2000)
    n = r.visits
    total = 0
    for i, c in enumerate(m.root_children()):
        if isinstance(c.move, WallMove) and c.proven != -1:
            prior = m._root.priors[i] if core == "py" else float(m._tree.epr[m._tree.ne0[0] + i])
            # the floor is checked before each simulation: a child lags it by a few visits
            assert c.visits >= 0.5 * prior * n - 3, (c, prior)
            total += 1
    assert total >= 5


@pytest.mark.parametrize("core", CORES)
def test_reused_root_gets_the_scan_walls_and_keeps_the_statistics(core):
    m = mcts(core=core)
    s0 = state_at("game08", 14)
    m.search(s0, sims=400)
    kids = {c.move: c.visits for c in m.root_children()}
    reply = max(kids, key=kids.get)
    s1 = apply_move(s0, reply)
    child_visits = kids[reply]
    m.search(s1, sims=1)
    moves = [c.move for c in m.root_children()]
    r = m._scan(s1, m.s.masks(s1))
    for _, w in r.attack[: m.scan] + r.defend[: m.scan]:
        assert WallMove(*w) in moves
    assert sum(c.visits for c in m.root_children()) >= child_visits - 1


# ---------- jit == py ----------


def _snapshot(m: MCTS, r) -> tuple:
    tree = Counter((st, tuple(moves)) for st, moves in m._iter_tree())
    return (r.move, r.score, r.depth, r.nodes, r.visits, r.max_depth), m.root_children(), tree


@needs_jit
def test_jit_equals_python_on_random_positions():
    ps = positions([93, 94], every=5, max_plies=90, wall_bias=0.75)
    assert len(ps) >= 20
    for i, s in enumerate(ps):
        out = []
        for core in ("jit", "py"):
            m = mcts(i, core)
            out.append(_snapshot(m, m.search(s, sims=300)))
        assert out[0] == out[1], (i, s)


@needs_jit
def test_jit_equals_python_with_flat_floor_and_deeper_widening():
    opts = {"floor": 0.02, "floor_walls": 0, "widen": 2, "floor_depth": 1}
    for i, s in enumerate([state_at("game09", 10), state_at("game11", 15), GameState.initial()]):
        out = []
        for core in ("jit", "py"):
            m = mcts(i, core, **opts)
            out.append(_snapshot(m, m.search(s, sims=400)))
        assert out[0] == out[1], (i, s)


@needs_jit
def test_jit_equals_python_in_whole_games_with_reuse():
    def game(core):
        a = MCTSBot(4, sims=150, searcher_settings=MCTS4_SEARCHER, core=core, **SMALL)
        b = MCTSBot(5, sims=120, searcher_settings=MCTS4_SEARCHER, core=core, **SMALL)
        s = GameState.initial()
        out = []
        for _ in range(30):
            if winner(s) is not None:
                break
            bot = a if s.turn == WHITE else b
            mv = bot.choose_move(s)
            out.append((mv, bot.last.score, bot.last.visits, bot.last.depth))
            s = apply_move(s, mv)
        return out

    jg, pg = game("jit"), game("py")
    assert jg == pg
    assert any(v > 150 for _, _, v, _ in jg)  # some trees were reused


@pytest.mark.parametrize("core", CORES)
def test_special_positions(core):
    # white wins next move: only saving walls, no scan walls on top
    s = state((4, 7), (0, 4), BLACK)
    r = mcts(core=core).search(s, sims=200)
    assert isinstance(r.move, WallMove)
    after = apply_move(s, r.move)
    assert not any(p.row == 8 for p in pawn_moves(after))
    # no walls: pawn moves only
    s = state((4, 3), (4, 5), WHITE, walls=(0, 6))
    r = mcts(core=core).search(s, sims=200)
    assert isinstance(r.move, PawnMove)
