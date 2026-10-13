"""Edge-case checklist from the test-engineer instructions (phase 1)."""

import pytest

from quoridor.paths import has_path_to_goal
from quoridor.rules import (
    apply_move,
    is_legal_move,
    is_terminal,
    is_wall_legal,
    legal_moves,
    pawn_moves,
    wall_moves,
    winner,
)
from quoridor.state import GameState, PawnMove, WallMove
from tests.helpers import cells, dests, mk


# --- jumps / diagonals in all four directions --------------------------------
@pytest.mark.parametrize(
    ("me", "opp", "straight", "diags"),
    [
        ("e5", "e6", "e7", ("d6", "f6")),
        ("e5", "e4", "e3", ("d4", "f4")),
        ("e5", "f5", "g5", ("f4", "f6")),
        ("e5", "d5", "c5", ("d4", "d6")),
    ],
)
def test_jump_and_diagonals_all_directions(me, opp, straight, diags):
    free = mk(white=me, black=opp)
    assert cells(straight) <= dests(pawn_moves(free))
    assert not (cells(*diags) & dests(pawn_moves(free)))
    # wall right behind the opponent: straight disappears, both diagonals appear
    dc = ord(opp[0]) - ord(me[0])
    dr = int(opp[1]) - int(me[1])
    oc, orow = ord(opp[0]) - 97, int(opp[1]) - 1
    if dr:
        walled = mk(white=me, black=opp, h={(oc, orow if dr == 1 else orow - 1)})
    else:
        walled = mk(white=me, black=opp, v={(oc if dc == 1 else oc - 1, orow)})
    got = dests(pawn_moves(walled))
    assert cells(straight).isdisjoint(got)
    assert cells(*diags) <= got


def test_black_diagonals_when_wall_behind():
    # black e5 moving down, white e4, wall between e4 and e3
    s = mk(white="e4", black="e5", turn=1, h={(4, 2)})
    assert dests(pawn_moves(s)) == cells("d5", "f5", "e6", "d4", "f4")


@pytest.mark.parametrize(
    ("white", "black", "expected"),
    [
        ("a8", "a9", ("b8", "a7", "b9")),
        ("i8", "i9", ("h8", "i7", "h9")),
        ("e8", "e9", ("d8", "f8", "e7", "d9", "f9")),
    ],
)
def test_adjacent_at_top_edge(white, black, expected):
    assert dests(pawn_moves(mk(white=white, black=black))) == cells(*expected)


def test_adjacent_at_side_edge():
    assert dests(pawn_moves(mk(white="a5", black="b5"))) == cells("a4", "a6", "c5")
    # opponent on the a-file with the mover to its right: no straight jump -> diagonals
    assert dests(pawn_moves(mk(white="b5", black="a5"))) == cells("b4", "b6", "c5", "a4", "a6")


def test_diagonal_cut_by_wall_between_target_and_opponent_at_edge():
    # white h8 jumps nowhere straight (i8 is black? no): black i8, white h8, edge behind
    s = mk(white="h8", black="i8")
    assert dests(pawn_moves(s)) == cells("g8", "h7", "h9", "i7", "i9")
    # wall between i8 and i9 kills the i9 diagonal
    s = mk(white="h8", black="i8", h={(7, 7)})
    assert (8, 8) not in dests(pawn_moves(s))
    assert (8, 6) in dests(pawn_moves(s))


def test_never_move_onto_opponent_or_off_board():
    for w, b in [("e1", "e2"), ("a1", "a2"), ("i9", "h9"), ("e5", "e6")]:
        s = mk(white=w, black=b)
        for m in pawn_moves(s):
            assert m.cell != s.pawns[1]
            assert 0 <= m.col < 9 and 0 <= m.row < 9


# --- walls at the edge -------------------------------------------------------
def test_wall_edge_anchors():
    s = GameState.initial()
    for o in "hv":
        assert is_wall_legal(s, WallMove(7, 7, o))
        assert is_wall_legal(s, WallMove(0, 0, o))
        assert not is_wall_legal(s, WallMove(8, 0, o))
        assert not is_wall_legal(s, WallMove(0, 8, o))
        assert not is_wall_legal(s, WallMove(-1, 3, o))


def test_wall_count_initial_is_128():
    assert len(wall_moves(GameState.initial())) == 128


# --- overlaps and crosses ----------------------------------------------------
@pytest.mark.parametrize("orient", ["h", "v"])
def test_collinear_overlap_matrix(orient):
    s = mk(h={(3, 3)}) if orient == "h" else mk(v={(3, 3)})
    for d, ok in [(-2, True), (-1, False), (0, False), (1, False), (2, True)]:
        x, y = (3 + d, 3) if orient == "h" else (3, 3 + d)
        assert is_wall_legal(s, WallMove(x, y, orient)) is ok, (orient, d)


def test_cross_and_perpendicular_neighbours():
    sh, sv = mk(h={(3, 3)}), mk(v={(3, 3)})
    assert not is_wall_legal(sh, WallMove(3, 3, "v"))
    assert not is_wall_legal(sv, WallMove(3, 3, "h"))
    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)]:
        assert is_wall_legal(sh, WallMove(3 + dx, 3 + dy, "v"))
        assert is_wall_legal(sv, WallMove(3 + dx, 3 + dy, "h"))


# --- single gap / closing the gap --------------------------------------------
def test_single_gap_open_then_closing_wall_rejected():
    # four walls span columns a-h between rows 1 and 2; only column i is open
    s = mk(h={(0, 0), (2, 0), (4, 0), (6, 0)})
    assert has_path_to_goal(s, 0)
    # the wall that would plug column i overlaps (h at x=7) -> illegal anyway
    assert not is_wall_legal(s, WallMove(7, 0, "h"))
    # leave the gap in the middle instead: columns a-d and f-i walled, gap = e
    g = mk(h={(0, 0), (2, 0), (5, 0), (7, 0)})
    assert has_path_to_goal(g, 0)
    for x in (3, 4):  # both h walls that would plug column e overlap existing ones
        assert not is_wall_legal(g, WallMove(x, 0, "h"))


def test_wall_closing_the_only_corridor_is_rejected():
    # white in the e1 pocket: barrier a-d and f-i above row 1, gap at e2.
    g = mk(h={(0, 0), (2, 0), (5, 0), (7, 0)}, white="e1")
    # block e1<->e2 from the other side is impossible (h at 3/4 overlap), so go via
    # vertical walls: seal e1 from d1 and f1 is legal (still e2 open) ...
    s = apply_move(g, WallMove(3, 0, "v")) if is_wall_legal(g, WallMove(3, 0, "v")) else g
    assert has_path_to_goal(s, 0)
    # ... and a wall closing e1/e2 gap above row 2 must be rejected when it cuts white off.
    t = mk(white="a1", black="e9", h={(1, 0), (3, 0), (5, 0), (7, 0)})
    assert has_path_to_goal(t, 0)  # only a1->a2 exit
    assert not is_wall_legal(t, WallMove(0, 0, "h"))  # would close it
    assert is_wall_legal(t, WallMove(0, 1, "h"))  # one row higher it does not matter... a2 exits


def test_closing_check_covers_the_opponent_too():
    # black on a9 with barrier below it except column a; white is to move
    s = mk(white="e1", black="a9", h={(1, 7), (3, 7), (5, 7), (7, 7)}, turn=0)
    assert has_path_to_goal(s, 1)
    assert not is_wall_legal(s, WallMove(0, 7, "h"))


# --- walls in hand -----------------------------------------------------------
def test_no_walls_in_hand():
    s = mk(walls_left=(0, 5))
    moves = legal_moves(s)
    assert moves
    assert all(isinstance(m, PawnMove) for m in moves)
    assert wall_moves(s) == []
    assert not is_legal_move(s, WallMove(0, 0, "h"))
    with pytest.raises(ValueError):
        apply_move(s, WallMove(0, 0, "h"))
    assert any(isinstance(m, WallMove) for m in legal_moves(mk(walls_left=(0, 5), turn=1)))


def test_last_wall_then_none():
    s = apply_move(mk(walls_left=(1, 1)), WallMove(0, 0, "h"))
    assert s.walls_left == (0, 1)
    s = apply_move(s, PawnMove(4, 7))
    assert not any(isinstance(m, WallMove) for m in legal_moves(s))


# --- victory -----------------------------------------------------------------
def test_win_after_straight_jump_white_and_black():
    t = apply_move(mk(white="e7", black="e8"), PawnMove(4, 8))
    assert is_terminal(t) and winner(t) == 0 and legal_moves(t) == []
    t = apply_move(mk(white="e3", black="e2", turn=1), PawnMove(4, 0))
    assert winner(t) == 1 and legal_moves(t) == []


def test_win_after_diagonal_jump_onto_goal_row():
    # black e9 is on white's goal row only for the test geometry; d9 is a goal cell
    t = apply_move(mk(white="e8", black="e9"), PawnMove(3, 8))
    assert winner(t) == 0


def test_wall_move_after_terminal_is_illegal():
    t = mk(white="e9", black="e5")
    assert winner(t) == 0
    assert not is_legal_move(t, WallMove(0, 0, "h"))
    assert not is_legal_move(t, PawnMove(4, 3))
