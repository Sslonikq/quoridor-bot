from quoridor.paths import has_path_to_goal, shortest_path_length
from quoridor.state import GameState
from tests.helpers import mk


def test_initial_lengths():
    s = GameState.initial()
    assert shortest_path_length(s, 0) == 8
    assert shortest_path_length(s, 1) == 8


def test_on_goal_row_is_zero():
    assert shortest_path_length(mk(white="c9"), 0) == 0


def test_wall_adds_detour():
    s = mk(white="e1", h={(4, 0)})  # blocks e1->e2 (cols e,f)
    assert shortest_path_length(s, 0) == 9  # step to d1 then up


def test_pawns_are_ignored():
    s = mk(white="e1", black="e2")
    assert shortest_path_length(s, 0) == 8


def test_no_path_returns_none():
    # white boxed in on a1: wall above a1/b1 and wall right of b1 and b1 up
    s = mk(white="a1", h={(0, 0)}, v={(1, 0)})
    # a1,b1 reachable; up blocked at a,b by h(0,0); c1 blocked by v(1,0) -> no path
    assert shortest_path_length(s, 0) is None
    assert not has_path_to_goal(s, 0)
    assert has_path_to_goal(s, 1)


def test_vertical_wall_detour_horizontal_cost():
    # snake: long vertical wall from v walls along column boundary d|e rows 1-6
    s = mk(white="e1", black="i9", v={(3, 0), (3, 2), (3, 4)})
    assert shortest_path_length(s, 0) == 8  # straight up column e still free
