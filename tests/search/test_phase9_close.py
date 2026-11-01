"""Phase 9 close (report 0087): mcts3 + the endgame solver, gaps found in the final review."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quoridor.bots import MCTS3_OPTIONS, MCTS3_SEARCHER, RandomBot
from quoridor.mcts import MCTS
from quoridor.notation import parse_move
from quoridor.rules import apply_move_unchecked, is_legal_move, winner
from quoridor.state import BLACK, WHITE, GameState

ROOT = Path(__file__).resolve().parents[2]


def st(white, black, turn, walls, h=(), v=()):
    return GameState((white, black), frozenset(h), frozenset(v), walls, turn)


def _mcts3(core=None) -> MCTS:
    return MCTS(0, MCTS3_SEARCHER, core, **MCTS3_OPTIONS)


@pytest.mark.parametrize("mcts_color", [WHITE, BLACK])
@pytest.mark.parametrize("seed", [1, 2])
def test_mcts3_plays_only_legal_moves_against_random(mcts_color, seed):
    """Whole games with 60 simulations a move (the tree is not kept between moves)."""
    rnd = RandomBot(seed)
    s = GameState.initial()
    for _ in range(300):
        if winner(s) is not None:
            break
        if s.turn == mcts_color:
            mv = _mcts3().search(s, sims=60).move
        else:
            mv = rnd.choose_move(s)
        assert is_legal_move(s, mv), (mv, s)
        s = apply_move_unchecked(s, mv)
    assert sum(s.walls_left) + len(s.h_walls) + len(s.v_walls) == 20


@pytest.mark.parametrize(
    ("walls", "expect_solver"),
    [
        ((2, 2), True),
        ((2, 1), True),
        ((0, 2), True),
        ((2, 0), True),
        ((3, 2), False),
        ((2, 3), False),
        ((3, 3), False),
        ((10, 0), False),
    ],
)
@pytest.mark.parametrize("turn", [WHITE, BLACK])
def test_solver_threshold_is_the_max_of_both_reserves(walls, expect_solver, turn):
    s = st((4, 6), (4, 3), turn, walls)  # pawns far from home, near the middle
    m = _mcts3(core="py")
    r = m.search(s, sims=20)
    assert (m.last_solve is not None) == expect_solver
    assert is_legal_move(s, r.move)
    if not expect_solver:
        assert r.solved is None and r.nodes > 0


def test_solver_result_is_legal_and_proven_next_to_the_threshold():
    # 2 walls: lost / won positions near the goal are proven; with 3 the same pawns are searched
    s2 = st((4, 5), (4, 3), WHITE, (2, 0))  # proven win on ply 5
    r2 = _mcts3(core="py").search(s2, sims=200)
    assert r2.solved is not None and r2.solved.proven and is_legal_move(s2, r2.move)
    s3 = st((4, 5), (4, 3), WHITE, (3, 0))
    r3 = _mcts3(core="py").search(s3, sims=200)
    assert r3.solved is None and is_legal_move(s3, r3.move)


def test_analyzer_mcts3_tree_not_reused_after_a_jump_outside_the_subtree():
    from quoridor.analyzer import engine

    engine.reset_mcts_tree()
    try:
        first = engine.analyze(["e2", "e8", "e3", "e7"], 0.3, bot="mcts")
        assert first["reused"] == 0
        # different branch, not a descendant of the last root
        other = engine.analyze(["d1", "d9"], 0.2, bot="mcts")
        assert other["reused"] == 0 and other["top"]
        # and going back is a fresh tree too, with legal moves
        back = engine.analyze(["e2", "e8", "e3", "e7"], 0.2, bot="mcts")
        assert back["reused"] == 0
        s = engine.replay(["e2", "e8", "e3", "e7"])
        assert all(is_legal_move(s, parse_move(t["name"])) for t in back["top"])
    finally:
        engine.reset_mcts_tree()


def test_analyzer_cancel_in_a_ten_wall_position_keeps_working():
    from quoridor.analyzer import engine

    engine.reset_mcts_tree()
    try:
        moves = ["e2", "e8"]
        res = engine.analyze(moves, 5, lambda: True, None, "mcts")
        assert res["cancelled"] and res["top"] == []
        ok = engine.analyze(moves, 0.3, bot="mcts")  # nothing broken by the cancel
        assert not ok["cancelled"] and ok["top"]
    finally:
        engine.reset_mcts_tree()


@pytest.mark.slow
def test_without_numba_mcts3_and_analyzer_work_in_a_subprocess():
    code = (
        "from quoridor import jit; assert not jit.ENABLED;"
        "from quoridor.analyzer import engine;"
        "r = engine.analyze(['e2','e8'], 0.5, bot='mcts');"
        "assert r['top'] and not r['cancelled'] and r['sims'] > 0;"
        "from quoridor.bots import make_bot; from quoridor.state import GameState;"
        "b = make_bot('mcts3-0.2s', 0); print(b.choose_move(GameState.initial()))"
    )
    env = {**os.environ, "QUORIDOR_NO_NUMBA": "1"}
    p = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert p.returncode == 0, p.stderr[-2000:]
