"""Immutable game state.

Internal coordinates (independent of the site notation):
  cell  = (col, row), both 0..8; col 0 = 'a', row 0 = rank '1'.
  wall  = WallMove(x, y, orient) with x, y in 0..7. The wall is anchored at the
          top-right corner of the BOTTOM-LEFT cell (x, y) of a 2x2 square, i.e. at the
          grid point shared by cells (x,y), (x+1,y), (x,y+1), (x+1,y+1).
    'h': lies between rows y and y+1, covering columns x and x+1.
    'v': lies between columns x and x+1, covering rows y and y+1.
This anchor is NOT the site-notation anchor (top-left); conversion lives only in notation.py.
"""

from __future__ import annotations

from dataclasses import dataclass

BOARD_SIZE = 9
WALL_GRID = BOARD_SIZE - 1
WALLS_PER_PLAYER = 10
WHITE = 0
BLACK = 1
H = "h"
V = "v"

Cell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class PawnMove:
    col: int
    row: int

    @property
    def cell(self) -> Cell:
        return (self.col, self.row)


@dataclass(frozen=True, slots=True)
class WallMove:
    x: int
    y: int
    orient: str  # 'h' or 'v'


Move = PawnMove | WallMove


def goal_row(player: int) -> int:
    return BOARD_SIZE - 1 if player == WHITE else 0


def in_board(col: int, row: int) -> bool:
    return 0 <= col < BOARD_SIZE and 0 <= row < BOARD_SIZE


@dataclass(frozen=True, slots=True)
class GameState:
    pawns: tuple[Cell, Cell]
    h_walls: frozenset[tuple[int, int]]
    v_walls: frozenset[tuple[int, int]]
    walls_left: tuple[int, int]
    turn: int

    @staticmethod
    def initial() -> GameState:
        return GameState(
            pawns=((4, 0), (4, 8)),
            h_walls=frozenset(),
            v_walls=frozenset(),
            walls_left=(WALLS_PER_PLAYER, WALLS_PER_PLAYER),
            turn=WHITE,
        )

    def blocked(self, col: int, row: int, dc: int, dr: int) -> bool:
        """True if a wall lies on the edge between (col,row) and (col+dc,row+dr).

        (dc, dr) must be one orthogonal unit step.
        """
        if dr == 1:
            return (col, row) in self.h_walls or (col - 1, row) in self.h_walls
        if dr == -1:
            return (col, row - 1) in self.h_walls or (col - 1, row - 1) in self.h_walls
        if dc == 1:
            return (col, row) in self.v_walls or (col, row - 1) in self.v_walls
        return (col - 1, row) in self.v_walls or (col - 1, row - 1) in self.v_walls

    def walls(self) -> frozenset[WallMove]:
        return frozenset(
            [WallMove(x, y, H) for x, y in self.h_walls]
            + [WallMove(x, y, V) for x, y in self.v_walls]
        )
