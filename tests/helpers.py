from quoridor.notation import format_move, parse_move
from quoridor.state import Cell, GameState, PawnMove


def mk(white="e1", black="e9", h=(), v=(), turn=0, walls_left=(10, 10)) -> GameState:
    """Build a state. Cells in site-style names; h/v walls as internal (x, y) tuples."""

    return GameState(
        pawns=(cell(white), cell(black)),
        h_walls=frozenset(h),
        v_walls=frozenset(v),
        walls_left=tuple(walls_left),
        turn=turn,
    )


def cell(name: str) -> Cell:
    """Cell name ('e2') -> internal (col, row), via notation.py."""
    m = parse_move(name)
    assert isinstance(m, PawnMove)
    return (m.col, m.row)


def cell_name(c: Cell) -> str:
    """Internal (col, row) -> cell name, via notation.py."""
    return format_move(PawnMove(*c))


def dests(moves):
    return {(m.col, m.row) for m in moves}


def cells(*names):
    return {cell(s) for s in names}
