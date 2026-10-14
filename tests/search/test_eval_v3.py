"""Phase 6, v3: the evaluation with the v3 wall stock weight (WALL_V3), report 0037.

* the evaluation is colour-symmetric: a position and its mirror (colours swapped, board
  flipped top to bottom) get the same score for the side to move, for v1 and v3 weights;
* terminal / forced positions are handled the same way as before;
* v3 does not spend a wall that wins only one step more than a pawn move would, where v1
  does (the point of the higher weight);
* registry entries and determinism of the fixed-depth v3.
"""

import pytest

from quoridor.bots import BOTS, V3_DEPTH, V3_WALL_WEIGHT, make_bot
from quoridor.eval import RACE, STEP, WALL, WALL_V3, WIN, evaluate, evaluate_dists
from quoridor.rules import apply_move, is_legal_move, pawn_moves
from quoridor.search import V2_TT_BITS, Searcher
from quoridor.state import BOARD_SIZE, WALL_GRID, GameState, PawnMove, WallMove
from tests.helpers import mk
from tests.search.test_search_speed import positions


def mirror(s: GameState) -> GameState:
    """Colours swapped and the board flipped top to bottom (row r -> 8 - r).

    A wall between rows y and y+1 lands between rows 7-y and 8-y, i.e. anchor y -> 7 - y,
    for both orientations (a vertical wall over rows y, y+1 covers rows 7-y, 8-y).
    """
    (wc, wr), (bc, br) = s.pawns
    top = BOARD_SIZE - 1
    return GameState(
        pawns=((bc, top - br), (wc, top - wr)),
        h_walls=frozenset((x, WALL_GRID - 1 - y) for x, y in s.h_walls),
        v_walls=frozenset((x, WALL_GRID - 1 - y) for x, y in s.v_walls),
        walls_left=(s.walls_left[1], s.walls_left[0]),
        turn=1 - s.turn,
    )


@pytest.fixture(scope="module")
def sample():
    return positions([41, 42, 43], every=2, max_plies=80, wall_bias=0.6)


def test_mirror_is_an_involution_and_keeps_legality(sample):
    for s in sample[:20]:
        assert mirror(mirror(s)) == s
        m = mirror(s)
        # every legal move of s has a legal mirrored counterpart
        for mv in [PawnMove(c, r) for c in range(9) for r in range(9)]:
            mm = PawnMove(mv.col, BOARD_SIZE - 1 - mv.row)
            assert is_legal_move(s, mv) == is_legal_move(m, mm)


@pytest.mark.parametrize("ww", [WALL, WALL_V3])
def test_eval_is_colour_symmetric(sample, ww):
    assert len(sample) > 50
    for s in sample:
        assert evaluate(s, wall_weight=ww) == evaluate(mirror(s), wall_weight=ww)
        sr = Searcher(0, wall_weight=ww)
        assert sr.evaluate(s) == sr.evaluate(mirror(s)) == evaluate(s, wall_weight=ww)


def test_search_score_is_colour_symmetric(sample):
    for s in sample[::6]:
        a = Searcher(0, wall_weight=WALL_V3, tt_bits=V2_TT_BITS).search(s, max_depth=3)
        b = Searcher(0, wall_weight=WALL_V3, tt_bits=V2_TT_BITS).search(mirror(s), max_depth=3)
        assert a.score == b.score


def test_v3_eval_terms():
    assert evaluate_dists(5, 5, 3, 1, wall_weight=WALL_V3) == 2 * WALL_V3
    assert evaluate_dists(5, 5, 3, 0, wall_weight=WALL_V3) == RACE + 3 * WALL_V3
    assert evaluate_dists(5, 4, 0, 3, wall_weight=WALL_V3) == -STEP - RACE - 3 * WALL_V3
    s = mk(white="e5", black="e9", walls_left=(4, 6))  # white 4 steps, black 8
    assert evaluate(s, wall_weight=WALL_V3) == 4 * STEP - 2 * WALL_V3
    assert WALL_V3 > STEP  # the point of v3: a wall in stock is worth more than one step


def test_v3_terminal_and_forced_positions():
    v3 = Searcher(0, wall_weight=WALL_V3, tt_bits=V2_TT_BITS)
    win_now = mk(white="e8", black="a6", turn=0)
    r = v3.search(win_now, max_depth=3)
    assert r.move == PawnMove(4, 8) and r.score == WIN - 1
    over = mk(white="e9", black="a5", turn=1)  # white already on its goal row
    with pytest.raises(ValueError):
        v3.search(over, max_depth=3)
    # black threatens to win next move: v3 must block (or lose); with walls it blocks
    threat = mk(white="a5", black="e2", turn=0, walls_left=(1, 0))
    r = v3.search(threat, max_depth=3)
    assert isinstance(r.move, WallMove)
    after = apply_move(threat, r.move)
    assert all(m.row != 0 for m in pawn_moves(after))  # black cannot win at once


# Found by scanning seeded games: at depth 1 v1 (WALL=25) places c8h, whose net gain is
# two steps (opponent +2, us 0); v3 values the wall at 125 and steps instead.
GAIN2 = GameState(
    pawns=((4, 0), (3, 8)),
    h_walls=frozenset({(4, 6), (0, 4), (4, 2)}),
    v_walls=frozenset({(1, 0), (7, 1)}),
    walls_left=(8, 7),
    turn=0,
)


def test_v3_keeps_a_wall_that_v1_spends():
    v1 = Searcher(0).search(GAIN2, max_depth=1)
    v3 = Searcher(0, wall_weight=WALL_V3).search(GAIN2, max_depth=1)
    assert isinstance(v1.move, WallMove)
    assert isinstance(v3.move, PawnMove)


def test_registry_has_v3_bots():
    assert {"v3", "v3-clock", "v3-0.2s", "v1-frozen-0.2s"} <= set(BOTS)
    bot = make_bot("v3", 0)
    assert bot.depth == V3_DEPTH
    assert bot._searcher.tt_bits == V2_TT_BITS
    assert bot._searcher.wall_weight == V3_WALL_WEIGHT == WALL_V3
    clock = make_bot("v3-clock", 0)
    assert clock.game_time is not None and clock._searcher.wall_weight == WALL_V3
    fast = make_bot("v3-0.2s", 0)
    assert fast.move_time is not None and fast._searcher.wall_weight == WALL_V3
    # v1 and the frozen baselines keep the old weight
    assert make_bot("v1", 0)._searcher.wall_weight == WALL
    assert make_bot("v1-frozen-0.2s", 0)._searcher.wall_weight == WALL


def test_v3_is_deterministic_at_fixed_depth(sample):
    ps = sample[::10]
    a = [make_bot("v3", 5).choose_move(s) for s in ps]
    b = [make_bot("v3", 5).choose_move(s) for s in ps]
    assert a == b
    assert all(is_legal_move(s, m) for s, m in zip(ps, a, strict=True))
