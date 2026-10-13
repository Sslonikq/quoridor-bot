import pytest

from quoridor.rules import apply_move, is_wall_legal, wall_moves
from quoridor.state import GameState, WallMove
from tests.helpers import mk

H, V = "h", "v"


def test_initial_wall_count():
    assert len(wall_moves(GameState.initial())) == 128


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (8, 0), (0, 8), (9, 9)])
@pytest.mark.parametrize("o", [H, V])
def test_out_of_board(x, y, o):
    assert not is_wall_legal(GameState.initial(), WallMove(x, y, o))


def test_edge_walls_inside_ok():
    s = GameState.initial()
    for w in (WallMove(0, 0, H), WallMove(7, 7, H), WallMove(0, 7, V), WallMove(7, 0, V)):
        assert is_wall_legal(s, w)


def test_bad_orientation():
    assert not is_wall_legal(GameState.initial(), WallMove(0, 0, "x"))


def test_same_wall_twice():
    s = mk(h={(3, 3)})
    assert not is_wall_legal(s, WallMove(3, 3, H))


def test_partial_overlap_horizontal():
    s = mk(h={(3, 3)})
    assert not is_wall_legal(s, WallMove(2, 3, H))
    assert not is_wall_legal(s, WallMove(4, 3, H))
    assert is_wall_legal(s, WallMove(1, 3, H))
    assert is_wall_legal(s, WallMove(5, 3, H))
    assert is_wall_legal(s, WallMove(3, 4, H))  # different row


def test_partial_overlap_vertical():
    s = mk(v={(3, 3)})
    assert not is_wall_legal(s, WallMove(3, 2, V))
    assert not is_wall_legal(s, WallMove(3, 4, V))
    assert is_wall_legal(s, WallMove(3, 1, V))
    assert is_wall_legal(s, WallMove(3, 5, V))
    assert is_wall_legal(s, WallMove(4, 3, V))


def test_cross_same_center():
    assert not is_wall_legal(mk(h={(3, 3)}), WallMove(3, 3, V))
    assert not is_wall_legal(mk(v={(3, 3)}), WallMove(3, 3, H))


def test_t_junctions_allowed():
    s = mk(h={(3, 3)})
    assert is_wall_legal(s, WallMove(4, 3, V))
    assert is_wall_legal(s, WallMove(2, 3, V))
    assert is_wall_legal(s, WallMove(3, 2, V))
    assert is_wall_legal(s, WallMove(3, 4, V))


def test_cannot_fully_block_white():
    # white at a1; h walls above a1,b1 | c1,d1 | ... 4 walls cover a-h, plus one for i
    # build a closed box around a1: h at (0,0) blocks a1/b1 upward, v at (1,0) blocks b1->c1
    s = mk(white="a1", black="e9", h={(0, 0)})
    assert not is_wall_legal(s, WallMove(1, 0, V))
    assert is_wall_legal(s, WallMove(1, 1, V))  # not enclosing


def test_cannot_fully_block_black():
    # v wall right of h9/h8 confines black (i9) to column i, rows 8-9
    s = mk(white="e1", black="i9", turn=1, v={(7, 7)})
    assert not is_wall_legal(s, WallMove(7, 6, H))  # would close i8 -> i7
    assert is_wall_legal(s, WallMove(5, 6, H))


def test_barrier_with_single_gap_leaves_gap_open():
    # h walls across boundary rows 1|2 covering columns a..h; column i is the only gap.
    # The grid has no wall at x=8, so the gap at column i can never be closed from above.
    s = mk(h={(0, 0), (2, 0), (4, 0), (6, 0)})
    assert not is_wall_legal(s, WallMove(7, 0, H))  # overlaps (6,0)
    # a wall above the barrier does not cut the path through column i
    assert is_wall_legal(s, WallMove(7, 1, V))


def test_wall_closing_last_gap_illegal():
    # white a1; barrier above row 1 columns a..h; gap only via i1. v wall between h1/i1
    # (x=7,y=0) would seal white completely along row 1.
    s = mk(white="a1", h={(0, 0), (2, 0), (4, 0), (6, 0)})
    assert not is_wall_legal(s, WallMove(7, 0, V))


def test_no_walls_left():
    s = mk(walls_left=(0, 10))
    assert not is_wall_legal(s, WallMove(3, 3, H))
    assert wall_moves(s) == []


def test_apply_wall_updates_counter_and_turn():
    s = GameState.initial()
    n = apply_move(s, WallMove(3, 3, H))
    assert n.walls_left == (9, 10)
    assert n.turn == 1
    assert (3, 3) in n.h_walls
    assert s.walls_left == (10, 10) and not s.h_walls  # immutability


def test_black_uses_own_counter():
    s = mk(turn=1)
    n = apply_move(s, WallMove(3, 3, V))
    assert n.walls_left == (10, 9)
    assert n.turn == 0


def test_wall_sealing_row_to_gap_illegal_with_white_at_e1():
    s = mk(h={(0, 0), (2, 0), (4, 0), (6, 0)})
    assert not is_wall_legal(s, WallMove(7, 0, V))
