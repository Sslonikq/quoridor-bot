"""Exact endgame solver (quoridor.endgame, phase 9 step 5, report 0084): values against a
brute force over rules.legal_moves, the two cores node for node, legality, known endgames
(game04 and positions from self-play), the MCTS root hook and the analyzer text."""

from __future__ import annotations

import json
import random
from functools import cache
from pathlib import Path

import pytest

from quoridor import endgame as eg
from quoridor import jit
from quoridor.bots import MCTS2_OPTIONS, MCTS_SEARCHER, make_bot
from quoridor.endgame import EndgameSolver, adj, can_reach, inv, mate_text
from quoridor.mcts import MCTS, MCTSCancelled
from quoridor.paths import has_path_to_goal
from quoridor.rules import apply_move_unchecked, is_legal_move, legal_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove

ENDGAMES = Path(__file__).parent.parent / "fixtures" / "endgames.json"
needs_jit = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")
CORES = ["py", pytest.param("jit", marks=needs_jit)]


def st(white, black, turn, walls=(0, 0), h=(), v=()):
    return GameState((white, black), frozenset(h), frozenset(v), walls, turn)


@cache
def brute(s: GameState, r: int) -> int:
    """Depth-limited value (node-relative mate units) by plain minimax over legal_moves."""
    if r <= 0:
        return 0
    best = -eg.INF
    for mv in legal_moves(s):
        c = apply_move_unchecked(s, mv)
        if winner(c) is not None:
            return eg.MATE - 1
        best = max(best, adj(brute(c, r - 1)))
    return 0 if best == -eg.INF else best


def depth_value(s: GameState, r: int, core: str = "py") -> int:
    """The solver's value of one depth-r search (fresh TT)."""
    impl = EndgameSolver(tt_bits=12, core=core)._core()
    v, _, _, aborted = impl.root(*eg._encode(s), *s.walls_left, s.turn, r, 1 << 40)
    assert not aborted
    return v


def random_state(rng: random.Random, walls_left: tuple[int, int], n_walls: int) -> GameState:
    while True:
        w = (rng.randrange(9), rng.randrange(1, 8))
        b = (rng.randrange(9), rng.randrange(1, 8))
        if w == b:
            continue
        s = st(w, b, rng.randrange(2), walls_left)
        h, v = set(), set()
        for _ in range(n_walls):
            mv = WallMove(rng.randrange(8), rng.randrange(8), rng.choice("hv"))
            t = GameState(s.pawns, frozenset(h), frozenset(v), (1, 1), s.turn)
            if is_legal_move(t, mv):
                (h if mv.orient == "h" else v).add((mv.x, mv.y))
        s = GameState(s.pawns, frozenset(h), frozenset(v), walls_left, s.turn)
        if has_path_to_goal(s, WHITE) and has_path_to_goal(s, BLACK):
            return s


# ---------- helpers ----------


def test_adj_inv_round_trip():
    for v in (0, 1, 5, 997, 998, 999, -998, -10, -2):
        assert adj(inv(v)) == v
    assert adj(eg.MATE - 1) == -(eg.MATE - 2)  # child wins now: we lose on ply 2
    assert adj(-(eg.MATE - 2)) == eg.MATE - 3
    assert inv(eg.INF) == -eg.INF and inv(-eg.INF) == eg.INF


def test_can_reach_counts_jumps_only_when_pawns_can_meet():
    assert can_reach(3, 3, 10, 0) and not can_reach(4, 3, 10, 0)  # far apart: no jumps
    assert can_reach(4, 3, 1, 0)  # adjacent: the first move may be a jump
    assert not can_reach(1, 0, 1, 0)


def test_mate_text():
    assert mate_text(1) == "выигрыш за 1 ход"
    assert mate_text(5) == "выигрыш за 3 хода"
    assert mate_text(9) == "выигрыш за 5 ходов"
    assert mate_text(-2) == "проигрыш: соперник выигрывает за 1 ход"


# ---------- exactness: brute force ----------


@pytest.mark.parametrize("seed", range(12))
def test_depth_values_equal_brute_force_without_walls(seed):
    rng = random.Random(seed)
    s = random_state(rng, (0, 0), rng.randrange(0, 7))
    for r in range(1, 9):
        assert depth_value(s, r) == brute(s, r), (s, r)


@pytest.mark.parametrize("seed", range(10))
def test_depth_values_equal_brute_force_with_walls(seed):
    rng = random.Random(100 + seed)
    s = random_state(rng, (0, 0), rng.randrange(0, 6))
    owner = 1 - s.turn  # the wall owner moves second: 5 x ~100 x 5 lines on depth 3
    left = (1, 0) if owner == WHITE else (0, 1)
    s = GameState(s.pawns, s.h_walls, s.v_walls, left, s.turn)
    for r in (1, 2, 3):
        assert depth_value(s, r) == brute(s, r), (s, r)
    s2 = GameState(s.pawns, s.h_walls, s.v_walls, (1, 1), s.turn)
    for r in (1, 2):
        assert depth_value(s2, r) == brute(s2, r), (s2, r)


def test_solver_near_the_goal_with_walls_equals_brute_force():
    # white two steps from the goal, black one wall: can black stop it?
    for s in (
        st((4, 6), (4, 3), WHITE, (0, 1)),
        st((4, 7), (0, 1), BLACK, (0, 1)),  # black must wall now (h at the top)
        st((0, 7), (8, 1), BLACK, (1, 1)),
    ):
        res = EndgameSolver(tt_bits=14, core="py").solve(s, max_nodes=200_000, max_depth=4)
        want = 0
        for r in range(1, 5):
            want = brute(s, r)
            if want:
                break
        got = 0
        if res.plies:
            got = eg.MATE - res.plies if res.plies > 0 else -(eg.MATE + res.plies)
        assert got == want, s


@pytest.mark.parametrize("seed", range(6))
def test_shortest_win_and_longest_defence_without_walls(seed):
    rng = random.Random(500 + seed)
    s = random_state(rng, (0, 0), rng.randrange(0, 5))
    res = EndgameSolver(tt_bits=14, core="py").solve(s, max_nodes=2_000_000, max_depth=14)
    want = 0
    for r in range(1, 15):
        want = brute(s, r)
        if want:
            break
    if want == 0:
        assert res.plies == 0
        return
    n = eg.MATE - abs(want)
    assert res.plies == (n if want > 0 else -n)
    # the move keeps the value: the child is worth exactly one ply less
    child = apply_move_unchecked(s, res.move)
    if winner(child) is None:
        assert adj(brute(child, n - 1)) == want


# ---------- the two cores ----------


@needs_jit
@pytest.mark.parametrize("seed", range(16))
def test_jit_equals_py_node_for_node(seed):
    rng = random.Random(900 + seed)
    walls = (rng.randrange(3), rng.randrange(3))
    s = random_state(rng, walls, rng.randrange(0, 8))
    budget = 6_000
    a = EndgameSolver(tt_bits=14, core="py").solve(s, max_nodes=budget)
    b = EndgameSolver(tt_bits=14, core="jit").solve(s, max_nodes=budget)
    assert (a.move, a.plies, a.depth, a.nodes) == (b.move, b.plies, b.depth, b.nodes)
    a = EndgameSolver(tt_bits=14, core="py").solve(s, max_nodes=budget, chunk=700)
    b = EndgameSolver(tt_bits=14, core="jit").solve(s, max_nodes=budget, chunk=700)
    assert (a.move, a.plies, a.depth, a.nodes) == (b.move, b.plies, b.depth, b.nodes)


@needs_jit
def test_jit_equals_py_with_a_kept_table():
    """The TT is kept between calls (bots solve every move): same sequence, same results."""
    rng = random.Random(77)
    a, b = EndgameSolver(tt_bits=10, core="py"), EndgameSolver(tt_bits=10, core="jit")
    for _ in range(8):
        s = random_state(rng, (rng.randrange(2), rng.randrange(2)), rng.randrange(4))
        x, y = a.solve(s, max_nodes=3_000), b.solve(s, max_nodes=3_000)
        assert (x.move, x.plies, x.depth, x.nodes) == (y.move, y.plies, y.depth, y.nodes)


# ---------- legality and API ----------


@pytest.mark.parametrize("core", CORES)
def test_moves_are_legal(core):
    rng = random.Random(4242)
    solver = EndgameSolver(tt_bits=14, core=core)
    proven = 0
    for _ in range(25):
        s = random_state(rng, (rng.randrange(3), rng.randrange(3)), rng.randrange(8))
        res = solver.solve(s, max_nodes=4_000)
        if res.proven:
            proven += 1
            assert is_legal_move(s, res.move)
        else:
            assert res.move is None and res.score == 0
    assert proven >= 5


def test_api_errors_and_budget():
    with pytest.raises(ValueError):
        EndgameSolver(core="nope")
    won = st((4, 8), (4, 4), BLACK)
    with pytest.raises(ValueError):
        EndgameSolver(core="py").solve(won)
    s = st((4, 1), (4, 7), WHITE)  # needs depth 14: not within 50 nodes
    res = EndgameSolver(core="py", tt_bits=10).solve(s, max_nodes=50)
    assert not res.proven and res.nodes <= 50
    with pytest.raises(eg.SolverCancelled):
        EndgameSolver(core="py", tt_bits=10).solve(s, should_stop=lambda: True)
    res = EndgameSolver(core="py", tt_bits=10).solve(s, time_limit=0.0)
    assert not res.proven


def test_win_now_and_score_scale():
    res = EndgameSolver(core="py", tt_bits=10).solve(st((4, 7), (0, 4), WHITE, (2, 2)))
    assert res.move == PawnMove(4, 8) and res.plies == 1 and res.score == 1_000_000 - 1
    assert res.moves_to_end == 1
    res = EndgameSolver(core="py", tt_bits=12).solve(st((4, 1), (4, 7), WHITE))
    assert res.plies == -14 and res.score == -(1_000_000 - 14) and res.moves_to_end == 7
    assert eg.plies_of_score(res.score) == -14


# ---------- known endgames (fixture) ----------


def _endgames() -> list[dict]:
    return json.loads(ENDGAMES.read_text("utf-8"))


def _cases(cheap: bool):
    out = []
    for e in _endgames():
        if cheap == (e["nodes"] <= 40_000):
            slow = [pytest.mark.slow] if e["nodes"] > 300_000 else []
            out.append(pytest.param(e, id=e["id"], marks=slow))
    return out


def _check_known(e: dict, core: str) -> None:
    from quoridor.analyzer.engine import replay
    from quoridor.notation import format_move

    s = replay(e["moves"])
    assert max(s.walls_left) <= 2
    res = EndgameSolver(core=core).solve(s, max_nodes=4 * e["nodes"] + 10_000)
    assert res.plies == e["plies"], e["id"]
    assert is_legal_move(s, res.move)
    if "move" in e:
        assert format_move(res.move) == e["move"]


@pytest.mark.parametrize("e", _cases(True))
def test_known_endgames_cheap(e):
    _check_known(e, "py")


@needs_jit
@pytest.mark.parametrize("e", _cases(False))
def test_known_endgames_jit(e):
    _check_known(e, "jit")


def test_fixture_has_real_and_selfplay_endgames():
    ids = [e["id"] for e in _endgames()]
    assert any(i.startswith("game04") for i in ids) and any(i.startswith("self") for i in ids)
    assert any(e["plies"] > 0 for e in _endgames()) and any(e["plies"] < 0 for e in _endgames())


# ---------- MCTS root hook ----------


def _mcts(**opts) -> MCTS:
    return MCTS(0, MCTS_SEARCHER, None, **{**MCTS2_OPTIONS, **opts})


def test_mcts2_has_the_solver():
    assert MCTS2_OPTIONS["solver"] == 2
    assert make_bot("mcts2-3s", 0).mcts.solver == 2
    assert make_bot("mcts", 0).mcts.solver == -1  # mcts is unchanged


def _fixture_state(pid: str) -> GameState:
    from quoridor.analyzer.engine import replay

    return replay(next(e for e in _endgames() if e["id"] == pid)["moves"])


def test_mcts_plays_the_proven_result():
    s = _fixture_state("self5040-p45")  # walls (2, 1), lost on ply 8, ~5k nodes
    m = _mcts()
    r = m.search(s, sims=200)
    assert r.solved is not None and r.solved.proven
    assert r.move == r.solved.move and r.score == r.solved.score
    assert m.root_children() == []
    off = _mcts(solver=-1).search(s, sims=200)
    assert off.solved is None and off.nodes == 200
    many = _mcts().search(st((4, 1), (4, 7), WHITE, (3, 1)), sims=50)
    assert many.solved is None  # 3 walls: above the threshold


def test_mcts_falls_back_when_not_proven():
    # 2 walls each, early: not provable in 2 nodes per simulation
    s = st((4, 2), (4, 6), WHITE, (2, 2))
    m = _mcts(solver_nodes=2)
    r = m.search(s, sims=100)
    assert r.solved is None and r.nodes == 100
    assert m.last_solve is not None and not m.last_solve.proven


def test_mcts_solver_cancel():
    s = st((4, 2), (4, 6), WHITE, (2, 2))
    with pytest.raises(MCTSCancelled):
        _mcts().search(s, None, 5.0, should_stop=lambda: True)


@pytest.mark.parametrize("core", CORES)
def test_mcts_cores_agree_with_the_solver(core):
    s = st((3, 2), (5, 6), BLACK, (1, 2), h=[(3, 4)], v=[(5, 2)])
    r = MCTS(0, MCTS_SEARCHER, core, **MCTS2_OPTIONS).search(s, sims=300)
    ref = EndgameSolver(core="py", tt_bits=20).solve(s, max_nodes=300 * 200)
    assert (r.solved is not None) == ref.proven
    if ref.proven:
        assert r.move == ref.move and r.solved.plies == ref.plies


# ---------- analyzer ----------


def test_analyzer_shows_the_mate_distance():
    from quoridor.analyzer import engine

    engine.reset_mcts_tree()
    # game04 after 51 plies: no walls left, black to move loses on ply 26
    moves = next(e for e in _endgames() if e["id"] == "game04-p51")["moves"]
    res = engine.analyze(moves, 1.0, bot="mcts")
    top = res["top"][0]
    assert top["mate"] and top["mate_in"] == -26 and res["eval"] > 0  # white's view
    assert top["why"] == "проигрыш: соперник выигрывает за 13 ходов"
    assert res["eval_text"] == top["why"]
    engine.reset_mcts_tree()
