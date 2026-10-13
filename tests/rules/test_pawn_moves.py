from quoridor.rules import pawn_moves
from quoridor.state import GameState
from tests.helpers import cells, dests, mk


def test_initial_state():
    s = GameState.initial()
    assert s.pawns == ((4, 0), (4, 8))
    assert s.walls_left == (10, 10)
    assert s.turn == 0


def test_start_steps_white():
    assert dests(pawn_moves(GameState.initial())) == cells("d1", "f1", "e2")


def test_corner_steps():
    assert dests(pawn_moves(mk(white="a1"))) == cells("b1", "a2")


def test_wall_blocks_step():
    # h wall at (4,0): between rows 1-2, columns e,f -> blocks e1->e2
    s = mk(h={(4, 0)})
    assert dests(pawn_moves(s)) == cells("d1", "f1")
    # also covers column f via the second half
    s = mk(white="f1", h={(4, 0)})
    assert dests(pawn_moves(s)) == cells("e1", "g1")
    # but not column g
    s = mk(white="g1", h={(4, 0)})
    assert "g2" in {f"{chr(97 + c)}{r + 1}" for c, r in dests(pawn_moves(s))}


def test_vertical_wall_blocks_sideways_both_rows():
    # v wall at (3,0): between d and e, rows 1-2
    s = mk(v={(3, 0)})
    assert dests(pawn_moves(s)) == cells("f1", "e2")
    s = mk(white="e2", v={(3, 0)})
    assert dests(pawn_moves(s)) == cells("f2", "e1", "e3")
    s = mk(white="e3", v={(3, 0)})
    assert "d3" in {f"{chr(97 + c)}{r + 1}" for c, r in dests(pawn_moves(s))}


def test_black_moves_down_blocked_by_wall_below():
    s = mk(black="e5", white="a1", turn=1, h={(3, 4)})  # between rows 5-6? no: rows y=4 -> 5/6
    # h at (3,4) lies above row 5 (index 4): covers d,e -> blocks e5->e6 only
    assert dests(pawn_moves(s)) == cells("d5", "f5", "e4")
    s = mk(black="e5", white="a1", turn=1, h={(3, 3)})  # between rows 4 and 5
    assert dests(pawn_moves(s)) == cells("d5", "f5", "e6")


def test_straight_jump():
    s = mk(white="e4", black="e5")
    assert dests(pawn_moves(s)) == cells("d4", "f4", "e3", "e6")


def test_straight_jump_black():
    s = mk(white="e4", black="e5", turn=1)
    assert dests(pawn_moves(s)) == cells("d5", "f5", "e6", "e3")


def test_no_jump_through_wall_between_pawns():
    s = mk(white="e4", black="e5", h={(4, 3)})  # wall between e4 and e5
    assert dests(pawn_moves(s)) == cells("d4", "f4", "e3")


def test_diagonal_when_wall_behind_opponent():
    s = mk(white="e4", black="e5", h={(4, 4)})  # wall between e5 and e6
    assert dests(pawn_moves(s)) == cells("d4", "f4", "e3", "d5", "f5")


def test_diagonal_when_edge_behind_opponent():
    s = mk(white="e8", black="e9")
    assert dests(pawn_moves(s)) == cells("d8", "f8", "e7", "d9", "f9")


def test_diagonal_one_side_blocked_by_wall():
    # wall behind opponent plus v wall between d5 and e5 (rows 5-6 -> y=4)
    s = mk(white="e4", black="e5", h={(4, 4)}, v={(3, 4)})
    assert dests(pawn_moves(s)) == cells("d4", "f4", "e3", "f5")


def test_diagonal_both_blocked_no_diagonals():
    s = mk(white="e4", black="e5", h={(4, 4)}, v={(3, 4), (4, 4)})
    assert dests(pawn_moves(s)) == cells("d4", "f4", "e3")


def test_diagonal_blocked_at_edge_of_board():
    # opponent on a9... white on a8, black a9: edge behind, only b9 diagonal
    s = mk(white="a8", black="a9")
    assert dests(pawn_moves(s)) == cells("b8", "a7", "b9")


def test_sideways_jump():
    s = mk(white="d1", black="e1")
    assert dests(pawn_moves(s)) == cells("c1", "d2", "f1")


def test_sideways_diagonal_when_wall_behind():
    s = mk(white="d5", black="e5", v={(4, 4)})  # wall between e5 and f5
    assert dests(pawn_moves(s)) == cells("c5", "d4", "d6", "e4", "e6")


def test_no_diagonal_when_straight_jump_available():
    s = mk(white="e4", black="e5")
    assert not ({(3, 4), (5, 4)} & dests(pawn_moves(s)))
