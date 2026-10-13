"""Move <-> string. The ONLY module that knows the site's notation format.

Format: pawn move = destination cell ("e2"); wall = anchor cell + orientation
("e3h", "f3v").

Wall anchor (confirmed on the site): the TOP-LEFT cell of the 2x2 square the
wall belongs to (ranks counted from 1 at the bottom).
  'h': lies between the anchor's rank and the rank below, covers the anchor's
       column and the column to its right. "a4h" = between ranks 4 and 3,
       covering columns a, b.
  'v': lies between the anchor's column and the column to its right, covers the
       anchor's rank and the rank below. "a2v" = between columns a and b,
       covering ranks 2 and 1.
Valid anchors: columns a-h, ranks 2-9. Internally (state.py) the wall is
WallMove(x=col, y=row0 - 1), where row0 is the 0-based rank of the anchor.
"""

from __future__ import annotations

from quoridor.state import BOARD_SIZE, WALL_GRID, H, Move, PawnMove, V, WallMove

ANCHOR_ASSUMPTION = "top-left cell of the 2x2 square (confirmed): internal (x, y) = (col, row0 - 1)"


def _anchor_to_internal(col: int, row: int) -> tuple[int, int]:
    return col, row - 1


def _internal_to_anchor(x: int, y: int) -> tuple[int, int]:
    return x, y + 1


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
    if move.orient not in (H, V) or not (0 <= move.x < WALL_GRID and 0 <= move.y < WALL_GRID):
        raise ValueError(f"wall out of range: {move}")
    c, r = _internal_to_anchor(move.x, move.y)
    return f"{_name(c, r)}{move.orient}"
