import pytest

from quoridor.notation import format_move, parse_move
from quoridor.state import PawnMove, WallMove


def test_parse_pawn():
    assert parse_move("e2") == PawnMove(4, 1)
    assert parse_move("a1") == PawnMove(0, 0)
    assert parse_move("i9") == PawnMove(8, 8)


def test_parse_wall():
    # anchor = top-left cell of the 2x2 square; internal (x, y) = (col, row0 - 1)
    assert parse_move("e3h") == WallMove(4, 1, "h")
    assert parse_move("f3v") == WallMove(5, 1, "v")


def test_wall_a4h_a2v():
    # a4h: between ranks 4 and 3, columns a,b -> h wall between rows 2 and 3 (0-based)
    assert parse_move("a4h") == WallMove(0, 2, "h")
    # a2v: between columns a and b, ranks 2 and 1 -> v wall covering rows 0 and 1
    assert parse_move("a2v") == WallMove(0, 0, "v")
    assert format_move(WallMove(0, 2, "h")) == "a4h"
    assert format_move(WallMove(0, 0, "v")) == "a2v"


def test_wall_anchor_extremes():
    assert parse_move("a2h") == WallMove(0, 0, "h")
    assert parse_move("h9v") == WallMove(7, 7, "v")
    assert format_move(WallMove(7, 7, "h")) == "h9h"


@pytest.mark.parametrize("bad", ["a1h", "e1v", "i2h", "i9v", "i5v"])
def test_wall_anchor_out_of_range(bad):
    with pytest.raises(ValueError):
        parse_move(bad)


@pytest.mark.parametrize(
    "w",
    [
        WallMove(8, 0, "h"),
        WallMove(0, 8, "v"),
        WallMove(-1, 0, "h"),
        WallMove(0, 8, "h"),
        WallMove(0, -1, "v"),
        WallMove(8, 8, "v"),
        WallMove(0, 0, "x"),
    ],
)
def test_format_wall_out_of_range(w):
    with pytest.raises(ValueError):
        format_move(w)


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


@pytest.mark.parametrize("bad", ["", "e", "j1", "a0", "a10", "e3x", "i3h", "e1h", "e3hh", "33"])
def test_bad_input(bad):
    with pytest.raises(ValueError):
        parse_move(bad)


def test_format_out_of_range():
    with pytest.raises(ValueError):
        format_move(PawnMove(9, 0))
