from quoridor.state import GameState


def mk(white="e1", black="e9", h=(), v=(), turn=0, walls_left=(10, 10)) -> GameState:
    """Build a state. Cells in site-style names; h/v walls as internal (x, y) tuples."""

    def cell(s):
        return (ord(s[0]) - 97, int(s[1]) - 1)

    return GameState(
        pawns=(cell(white), cell(black)),
        h_walls=frozenset(h),
        v_walls=frozenset(v),
        walls_left=tuple(walls_left),
        turn=turn,
    )


def dests(moves):
    return {(m.col, m.row) for m in moves}


def cells(*names):
    return {(ord(s[0]) - 97, int(s[1]) - 1) for s in names}
