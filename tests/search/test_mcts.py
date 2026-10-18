"""Phase 9, step 5 (report 0076): MCTS with a wall-aware policy (quoridor.mcts)."""

import pytest

from quoridor.bots import BOTS, MCTS_OPTIONS, MCTS_SEARCHER, RandomBot, make_bot
from quoridor.mcts import MCTS, MCTSBot
from quoridor.rules import apply_move, is_legal_move, pawn_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove, WallMove
from tests.search.test_search_speed import positions

PROVEN = 1_000_000 - 2


def mcts(seed: int = 0, **opts) -> MCTS:
    return MCTS(seed, MCTS_SEARCHER, **{**MCTS_OPTIONS, **opts})


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


def test_moves_are_legal_in_the_whole_tree():
    m = mcts()
    for s in positions([71, 72], every=5, max_plies=60, wall_bias=0.7)[:10]:
        m._root = None
        m.search(s, sims=60)
        stack = [m._root]
        while stack:
            node = stack.pop()
            if node is None or node.moves is None:
                continue
            for mv, child, kid in zip(node.moves, node.states, node.kids, strict=True):
                assert is_legal_move(node.state, mv), (node.state, mv)
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


def test_repetition_penalty_hits_returning_moves():
    m = mcts(rep_penalty=0.5)
    s0 = state((4, 3), (4, 6), WHITE)
    hist = [state((3, 3), (4, 7), WHITE), state((4, 3), (4, 7), BLACK)]
    m.set_history(hist)
    r = m.search(s0, sims=50)
    pen = m._penalties(m._root)
    by_move = dict(zip(m._root.moves, pen, strict=True))
    assert by_move[PawnMove(3, 3)] == 0.5 and by_move[PawnMove(5, 3)] == 0.0
    assert r.move != PawnMove(3, 3)
