"""Move <-> string. The ONLY module that knows the site's notation format.

Format: pawn move = destination cell ("e2"); wall = anchor cell + orientation
("e3h", "f3v").

WARNING: which corner of the anchor cell the site attaches a wall to is NOT
CONFIRMED. Current ASSUMPTION: anchor cell (col,row) maps directly to internal
WallMove(x=col, y=row), i.e. the wall sits at the anchor cell's top-right corner:
'h' runs along the cell's top edge and extends one cell to the right; 'v' runs
along its right edge and extends one cell up. If the site differs, change only
_anchor_to_internal / _internal_to_anchor.
"""

from __future__ import annotations

from quoridor.state import BOARD_SIZE, WALL_GRID, H, Move, PawnMove, V, WallMove

ANCHOR_ASSUMPTION = "top-right corner of anchor cell (UNCONFIRMED)"


def _anchor_to_internal(col: int, row: int) -> tuple[int, int]:
    return col, row


def _internal_to_anchor(x: int, y: int) -> tuple[int, int]:
    return x, y


def _cell(s: str) -> tuple[int, int]:
    if len(s) != 2 or not ("a" <= s[0] <= "i") or not ("1" <= s[1] <= "9"):
        raise ValueError(f"bad cell: {s!r}")
    return ord(s[0]) - ord("a"), int(s[1]) - 1


def _name(col: int, row: int) -> str:
    return f"{chr(ord('a') + col)}{row + 1}"


def parse_move(s: str) -> Move:
    s = s.strip().lower()
    if len(s) == 2:
        c, r = _cell(s)
        return PawnMove(c, r)
    if len(s) == 3 and s[2] in (H, V):
        c, r = _cell(s[:2])
        x, y = _anchor_to_internal(c, r)
        if not (0 <= x < WALL_GRID and 0 <= y < WALL_GRID):
            raise ValueError(f"wall anchor out of range: {s!r}")
        return WallMove(x, y, s[2])
    raise ValueError(f"bad move: {s!r}")


def format_move(move: Move) -> str:
    if isinstance(move, PawnMove):
        if not (0 <= move.col < BOARD_SIZE and 0 <= move.row < BOARD_SIZE):
            raise ValueError(f"cell out of board: {move}")
        return _name(move.col, move.row)
    c, r = _internal_to_anchor(move.x, move.y)
    return f"{_name(c, r)}{move.orient}"
