import pytest

from quoridor.notation import format_move, parse_move
from quoridor.state import PawnMove, WallMove


def test_parse_pawn():
    assert parse_move("e2") == PawnMove(4, 1)
    assert parse_move("a1") == PawnMove(0, 0)
    assert parse_move("i9") == PawnMove(8, 8)


def test_parse_wall():
    # ASSUMPTION (unconfirmed): anchor cell == internal (x, y)
    assert parse_move("e3h") == WallMove(4, 2, "h")
    assert parse_move("f3v") == WallMove(5, 2, "v")


def test_roundtrip_all_pawn_cells():
    for c in range(9):
        for r in range(9):
            m = PawnMove(c, r)
            assert parse_move(format_move(m)) == m


def test_roundtrip_all_walls():
    for o in "hv":
        for x in range(8):
            for y in range(8):
                m = WallMove(x, y, o)
                assert parse_move(format_move(m)) == m


def test_case_and_whitespace():
    assert parse_move(" E2 ") == PawnMove(4, 1)


@pytest.mark.parametrize("bad", ["", "e", "j1", "a0", "a10", "e3x", "i3h", "e9h", "e3hh", "33"])
def test_bad_input(bad):
    with pytest.raises(ValueError):
        parse_move(bad)


def test_format_out_of_range():
    with pytest.raises(ValueError):
        format_move(PawnMove(9, 0))
