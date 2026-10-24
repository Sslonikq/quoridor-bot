"""MCTS with a wall-aware policy (quoridor.mcts): phase 9, step 5 (report 0076) and the
array core on numba (report 0082), which must build exactly the tree of the Python core."""

from collections import Counter

import pytest

from quoridor import jit
from quoridor import mcts as mcts_mod
from quoridor.bots import BOTS, MCTS_OPTIONS, MCTS_SEARCHER, RandomBot, make_bot
from quoridor.mcts import MCTS, MCTSBot, MCTSCancelled
from quoridor.rules import apply_move, is_legal_move, pawn_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from tests.search.test_search_speed import positions

PROVEN = 1_000_000 - 2


CORES = ["py", pytest.param("jit", marks=pytest.mark.skipif(not jit.ENABLED, reason="numba off"))]
needs_jit = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")


def mcts(seed: int = 0, core: str | None = None, **opts) -> MCTS:
    return MCTS(seed, MCTS_SEARCHER, core, **{**MCTS_OPTIONS, **opts})


def state(white, black, turn, walls=(10, 10), h=(), v=()):
    return GameState((white, black), frozenset(h), frozenset(v), walls, turn)


def test_registry():
    assert {"mcts", "mcts-clock", "mcts-0.2s", "mcts-3s"} <= set(BOTS)
    assert make_bot("mcts", 0).sims is not None
    assert make_bot("mcts-3s", 0).move_time == 3.0
    assert make_bot("mcts-0.2s", 0).move_time == 0.2


def test_bad_arguments():
    with pytest.raises(TypeError):
        MCTS(0, MCTS_SEARCHER, nope=1)
    with pytest.raises(ValueError):
        MCTSBot(0)
    with pytest.raises(ValueError):
        mcts().search(GameState.initial())
    won = state((4, 8), (4, 4), BLACK)
    with pytest.raises(ValueError):
        mcts().search(won, sims=10)


def test_wins_in_one():
    s = state((4, 7), (0, 4), WHITE)
    r = mcts().search(s, sims=50)
    assert r.move == PawnMove(4, 8) and r.score == PROVEN and r.display_score == PROVEN


def test_wins_in_one_by_a_jump():
    s = state((4, 1), (4, 2), BLACK)  # black jumps over white on e2 to e1
    r = mcts().search(s, sims=30)
    assert r.move == PawnMove(4, 0) and r.score == PROVEN


def test_stops_an_immediate_win():
    s = state((4, 7), (0, 4), BLACK)  # white steps to e9 next move unless blocked
    r = mcts().search(s, sims=300)
    assert isinstance(r.move, WallMove)
    after = apply_move(s, r.move)
    assert not any(p.row == 8 for p in pawn_moves(after))


def test_proven_loss_is_reported():
    # black (no walls) must walk 4 steps, white wins next move: every move loses
    s = state((4, 7), (0, 4), BLACK, walls=(10, 0))
    r = mcts().search(s, sims=200)
    assert r.score == -PROVEN


@pytest.mark.parametrize("core", CORES)
def test_moves_are_legal_in_the_whole_tree(core):
    m = mcts(core=core)
    for s in positions([71, 72], every=5, max_plies=60, wall_bias=0.7)[:10]:
        m.search(s, sims=60)
        count = 0
        for st, moves in m._iter_tree():
            count += 1
            for mv in moves:
                assert is_legal_move(st, mv), (st, mv)
        assert count >= 2
    if core == "py":  # the stored child states are the applied moves
        stack = [m._root]
        while stack:
            node = stack.pop()
            if node is None or node.moves is None:
                continue
            for mv, child, kid in zip(node.moves, node.states, node.kids, strict=True):
                assert child == apply_move(node.state, mv)
                stack.append(kid)


def test_plays_legal_games():
    for color in (WHITE, BLACK):
        bot = MCTSBot(1, sims=40, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)
        rnd = RandomBot(2)
        s = GameState.initial()
        for _ in range(60):
            if winner(s) is not None:
                break
            mover = bot if s.turn == color else rnd
            s = apply_move(s, mover.choose_move(s))  # raises on an illegal move


def test_deterministic_for_a_seed():
    def game(seed):
        a = MCTSBot(seed, sims=80, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)
        b = MCTSBot(seed + 1, sims=80, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)
        s = GameState.initial()
        out = []
        for _ in range(16):
            if winner(s) is not None:
                break
            mv = (a if s.turn == WHITE else b).choose_move(s)
            out.append((mv, (a if s.turn == WHITE else b).last.score))
            s = apply_move(s, mv)
        return out

    assert game(5) == game(5)


def test_tree_is_reused_after_the_opponent_move():
    bot = MCTSBot(0, sims=200, searcher_settings=MCTS_SEARCHER, **MCTS_OPTIONS)
    s = GameState.initial()
    s = apply_move(s, bot.choose_move(s))
    s = apply_move(s, PawnMove(4, 7))  # black's natural reply
    bot.choose_move(s)
    assert bot.last.visits > bot.last.nodes == 200


@pytest.mark.parametrize("core", CORES)
def test_repetition_penalty_hits_returning_moves(core):
    m = mcts(core=core, rep_penalty=0.5)
    s0 = state((4, 3), (4, 6), WHITE)
    hist = [state((3, 3), (4, 7), WHITE), state((4, 3), (4, 7), BLACK)]
    m.set_history(hist)
    r = m.search(s0, sims=50)
    by_move = {c.move: c.penalty for c in m.root_children()}
    assert by_move[PawnMove(3, 3)] == 0.5 and by_move[PawnMove(5, 3)] == 0.0
    assert r.move != PawnMove(3, 3)


# ---------- public API (report 0082) ----------


@pytest.mark.parametrize("core", CORES)
def test_root_children(core):
    m = mcts(core=core)
    r = m.search(GameState.initial(), sims=300)
    kids = m.root_children()
    assert sum(c.visits for c in kids) == r.visits - 1  # the root's own first visit
    best = max(kids, key=lambda c: c.visits)
    assert best.move == r.move and best.score == r.score and best.depth == r.depth
    assert all((c.value is None) == (c.visits == 0) for c in kids)
    assert m.pv_length() == r.depth
    # a proven win in one: nothing to list
    m.search(state((4, 7), (0, 4), WHITE), sims=10)
    assert m.root_children() == []


@pytest.mark.parametrize("core", CORES)
def test_should_stop_and_progress(core):
    m = mcts(core=core)
    calls = []
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 3

    with pytest.raises(MCTSCancelled) as e:
        m.search(GameState.initial(), time_limit=5.0, should_stop=stop)
    assert e.value.sims >= 1
    r = m.search(
        GameState.initial(),
        time_limit=0.3,
        on_progress=lambda line, sims: calls.append((line, sims)),
        progress_every=0.05,
    )
    assert calls and all(line >= 1 and 0 < sims <= r.nodes for line, sims in calls)
    assert [s for _, s in calls] == sorted(s for _, s in calls)


def test_core_choice():
    with pytest.raises(ValueError):
        MCTS(0, MCTS_SEARCHER, "gpu")
    assert mcts(leaf_depth=1).core == "py"  # not in the jit core: the reference runs
    assert mcts(core="py").core == "py"
    assert mcts().core == ("jit" if jit.ENABLED else "py")
    if jit.ENABLED:
        with pytest.raises(ValueError):
            mcts(core="jit", rollout=2)
        with pytest.raises(ValueError):
            MCTS(0, {**MCTS_SEARCHER, "combo_exact": 1}, "jit")
    else:
        with pytest.raises(ValueError):
            mcts(core="jit")


# ---------- the jit core builds the tree of the Python core ----------


def _snapshot(m: MCTS, r) -> tuple:
    tree = Counter((st, tuple(moves)) for st, moves in m._iter_tree())
    return (r.move, r.score, r.depth, r.nodes, r.visits, r.max_depth), m.root_children(), tree


def _both(s, sims, seed=0, history=(), **opts):
    out = []
    for core in ("jit", "py"):
        m = mcts(seed, core, **opts)
        m.set_history(list(history))
        out.append(_snapshot(m, m.search(s, sims=sims)))
    return out


@needs_jit
def test_jit_equals_python_on_random_positions():
    ps = positions([71, 72, 73, 74], every=4, max_plies=90, wall_bias=0.75)
    assert len(ps) >= 60
    for i, s in enumerate(ps):
        a, b = _both(s, 250, seed=i)
        assert a == b, (i, s)


@needs_jit
def test_jit_equals_python_in_special_positions():
    cases = [
        state((4, 7), (0, 4), BLACK),  # saving walls (white wins next move)
        state((4, 7), (0, 4), BLACK, walls=(10, 0)),  # proven loss
        state((4, 3), (4, 5), WHITE, walls=(0, 6)),  # only black has walls: wall race
        state((4, 3), (4, 5), BLACK, walls=(3, 0)),  # only white has walls
        state((4, 4), (4, 5), WHITE, walls=(2, 1), h=[(3, 5), (5, 5)], v=[(3, 3)]),  # jumps
        state((0, 1), (8, 7), WHITE, walls=(1, 1)),
    ]
    for s in cases:
        for opts in ({}, {"rep_penalty": 0.0, "fpu": 0.0}):
            a, b = _both(s, 400, seed=3, **opts)
            assert a == b, (s, opts)


@needs_jit
def test_jit_equals_python_with_history_reuse_and_growth(monkeypatch):
    """Whole games of two bots per core: history (rep_penalty), tree reuse and (tiny
    initial arrays) growth on the jit side; every move and result is the same."""
    monkeypatch.setattr(mcts_mod, "_NODES0", 64)
    monkeypatch.setattr(mcts_mod, "_EDGES0", 512)

    def game(core):
        a = MCTSBot(4, sims=150, searcher_settings=MCTS_SEARCHER, core=core, **MCTS_OPTIONS)
        b = MCTSBot(5, sims=120, searcher_settings=MCTS_SEARCHER, core=core, **MCTS_OPTIONS)
        s = GameState.initial()
        out = []
        for _ in range(40):
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
