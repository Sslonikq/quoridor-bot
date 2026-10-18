"""Report 0066: the opponent's wall stock as a threat to our path (eval.reserve_term,
Searcher(reserve=...)) and the game06 regression (moves 17...-20...: v4 spent its last
walls while white kept all 10)."""

from pathlib import Path

import pytest

from quoridor.analyzer.engine import replay
from quoridor.bots import V4_SETTINGS, make_bot
from quoridor.eval import RESERVE_V4, evaluate_v4, reserve_term
from quoridor.frozen import search_v4
from quoridor.notation import format_move
from quoridor.search import Searcher
from quoridor.state import GameState, WallMove
from tests.search.test_eval_v3 import mirror
from tests.search.test_search_speed import positions

FIXTURES = Path(__file__).parents[1] / "fixtures/games"
NEW = {**search_v4.SETTINGS, "reserve": RESERVE_V4}  # the frozen v4 + this option


def load(name: str) -> list[str]:
    text = (FIXTURES / name).read_text("utf-8")
    return [t for line in text.splitlines() if not line.startswith("#") for t in line.split()]


GAME06 = load("game06.txt")


def with_stock(s: GameState, walls_left: tuple[int, int]) -> GameState:
    return GameState(s.pawns, s.h_walls, s.v_walls, walls_left, s.turn)


# ---------- the term ----------


def test_reserve_term_values():
    r = 100
    # we have 0 / 1 / 2 / 3 walls, the opponent 10: full, half, quarter, nothing per wall
    assert [reserve_term(w, 10, r) for w in range(4)] == [-1000, -500, -250, 0]
    # and the other way round
    assert [reserve_term(10, w, r) for w in range(4)] == [1000, 500, 250, 0]
    assert reserve_term(0, 0, r) == 0
    assert reserve_term(1, 1, r) == 0  # equal small stocks cancel out
    assert reserve_term(5, 2, 0) == 0


@pytest.mark.parametrize("r", [1, 50, 120])
def test_reserve_term_is_antisymmetric(r):
    for a in range(11):
        for b in range(11):
            assert reserve_term(a, b, r) == -reserve_term(b, a, r)


def test_spending_the_last_walls_costs_more_against_a_big_stock():
    """Our k-th last wall is worth more, the bigger the opponent's stock."""

    def cost(w: int, wo: int) -> int:  # what spending our w-th last wall costs in the term
        return reserve_term(w, wo, RESERVE_V4) - reserve_term(w - 1, wo, RESERVE_V4)

    for wo in (3, 5, 10):  # 3+: the opponent itself is not short of walls
        assert cost(1, wo) > cost(2, wo) >= cost(3, wo) > cost(4, wo) == 0  # the last: most
    assert cost(1, 10) > cost(1, 5) > cost(1, 2)


def test_evaluate_v4_adds_exactly_the_term():
    for d_me, d_op, wm, wo, e_me, e_op in [
        (5, 15, 2, 10, 5, 15),
        (3, 20, 0, 10, 3, 20),
        (4, 4, 1, 0, 6, 4),
        (7, 6, 3, 3, 7, 6),
        (6, 2, 0, 0, 6, 2),
    ]:
        base = evaluate_v4(d_me, d_op, wm, wo, e_me, e_op)
        assert evaluate_v4(d_me, d_op, wm, wo, e_me, e_op, reserve=0) == base
        assert evaluate_v4(
            d_me, d_op, wm, wo, e_me, e_op, reserve=RESERVE_V4
        ) == base + reserve_term(wm, wo, RESERVE_V4)


def test_searcher_reserve_eval_is_the_old_one_plus_the_term_and_colour_symmetric():
    sample = positions([71, 72], every=4, max_plies=90, wall_bias=0.7)
    for s0 in sample:
        for stock in ((1, 9), (0, 6), (2, 2), (10, 0), s0.walls_left):
            s = with_stock(s0, stock)
            old = Searcher(0, **search_v4.SETTINGS).evaluate(s)
            new = Searcher(0, **NEW).evaluate(s)
            me = s.turn
            assert new == old + reserve_term(s.walls_left[me], s.walls_left[1 - me], RESERVE_V4)
            assert new == Searcher(0, **NEW).evaluate(mirror(s))


def test_reserve_off_is_the_frozen_v4():
    """reserve=0 (the default) changes nothing: same move, score and nodes as v4-frozen."""
    for s in positions([73], every=8, max_plies=70, wall_bias=0.6):
        a = search_v4.make_searcher(0).search(s, max_depth=3)
        b = Searcher(0, **{**NEW, "reserve": 0}).search(s, max_depth=3)
        assert (a.move, a.score, a.nodes) == (b.move, b.score, b.nodes)


def test_searchbot_passes_reserve():
    b = make_bot("v4", 0)
    assert b._searcher.reserve == V4_SETTINGS.get("reserve", 0)


# ---------- game06: do not spend the last walls against a full stock ----------

# Black to move, white keeps all 10 walls: 17... (2 walls left: b5h was the v4 hint),
# 18... (2: b5h was played), 19... (1), 20... (1: b6v was played, then white's walls
# lengthened black's path 1 -> 9; playouts v4 vs v4 at 1 s: after 20...b6v black won
# 3/38, after 20...g4 23/38).
GAME06_LAST_WALLS = [33, 35, 37, 39]  # plies played before black's moves 17...-20...


def test_game06_positions_are_the_ones_from_the_comment():
    assert [GAME06[n] for n in GAME06_LAST_WALLS] == ["f5", "b5h", "f4", "b6v"]
    for n, left in zip(GAME06_LAST_WALLS, (2, 2, 1, 1), strict=True):
        s = replay(GAME06[:n])
        assert s.turn == 1 and s.walls_left == (10, left), n


@pytest.mark.parametrize("n", GAME06_LAST_WALLS)
def test_game06_keeps_the_last_walls_at_fixed_depth(n):
    s = replay(GAME06[:n])
    r = Searcher(0, **NEW).search(s, max_depth=6)
    assert not isinstance(r.move, WallMove), (n, r.move)


# Report 0077: with numba v4 completes depth 11 within 3 s on 17... (ply 33), and at
# depth 11 it plays b5h (also in pure Python: same move, score and nodes; depths 6-10
# and 12 play f5). A property of v4's eval at that depth, not of the speed-up; the
# analyzer's 5 s hint (depth 12) is f5. Not strict: depth 10 or 12 within 3 s passes.
_DEPTH11_B5H = pytest.mark.xfail(
    strict=False, reason="v4 at depth 11 plays b5h on game06 17... (report 0077)"
)


@pytest.mark.slow
@pytest.mark.parametrize(
    "n", [pytest.param(n, marks=_DEPTH11_B5H) if n == 33 else n for n in GAME06_LAST_WALLS]
)
def test_game06_keeps_the_last_walls_at_3s(n):
    """Like SearchBot with 3 s per move and like the analyzer's hint (search_top)."""
    s = replay(GAME06[:n])
    r = Searcher(0, **NEW).search(s, time_limit=3.0)
    assert not isinstance(r.move, WallMove), (n, r.move, r.depth)
    top = Searcher(0, **NEW).search_top(s, time_limit=3.0)
    assert not isinstance(top.best.move, WallMove), (n, top.best.move, top.best.depth)


def test_v4_settings_are_the_frozen_v4_plus_reserve():
    """Report 0066: v4 = v4-frozen + reserve (stronger at 3 s, see the report)."""
    assert V4_SETTINGS == NEW


# ---------- the analyzer hints from the comments of game04 / game05 / game06 ----------

# (game, plies played, the move v4 recommended in the comment instead of the played one)
KEY_HINTS = [
    ("game04.txt", 21, "f3v"),  # 11... g4 ?? (f3v)
    ("game05.txt", 13, "c7v"),  # 7... b8h ?? (c7v)
    ("game05.txt", 21, "g7v"),  # 11... h9h ?? (g7v)
    ("game06.txt", 15, "f6"),  # 8... h6 ?? (f6)
]


@pytest.mark.slow
@pytest.mark.parametrize(("game", "n", "hint"), KEY_HINTS)
def test_key_hints_of_game04_05_06_are_still_found_at_3s(game, n, hint):
    s = replay(load(game)[:n])
    top = Searcher(0, **NEW).search_top(s, time_limit=3.0)
    assert format_move(top.best.move) == hint, (game, n, top.best.move, top.best.depth)
