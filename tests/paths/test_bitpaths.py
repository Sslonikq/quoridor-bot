"""quoridor.bitpaths must agree exactly with the reference quoridor.paths."""

import random

import pytest

from quoridor import bitpaths, paths
from quoridor.bitpaths import add_wall, build_masks, player_distance
from quoridor.rules import wall_fits
from quoridor.state import BLACK, BOARD_SIZE, WALL_GRID, WHITE, GameState, H, V, WallMove
from tests.helpers import mk
from tests.playouts import random_game


def masks_of(s: GameState):
    return build_masks(s.h_walls, s.v_walls)


def all_cells():
    return [(c, r) for c in range(BOARD_SIZE) for r in range(BOARD_SIZE)]


def with_pawn(s: GameState, player: int, cell) -> GameState:
    pawns = list(s.pawns)
    pawns[player] = cell
    return GameState((pawns[0], pawns[1]), s.h_walls, s.v_walls, s.walls_left, s.turn)


def test_empty_board_masks_match_blocked():
    m = masks_of(GameState.initial())
    assert m == bitpaths.EMPTY_MASKS
    assert player_distance(m, (4, 0), WHITE) == 8
    assert player_distance(m, (4, 8), BLACK) == 8
    assert player_distance(m, (2, 8), WHITE) == 0


@pytest.mark.parametrize("orient", [H, V])
def test_single_wall_masks_match_state_blocked(orient):
    for x in range(WALL_GRID):
        for y in range(WALL_GRID):
            s = mk(h={(x, y)} if orient == H else (), v={(x, y)} if orient == V else ())
            up, down, right, left = masks_of(s)
            assert add_wall(bitpaths.EMPTY_MASKS, x, y, orient) == (up, down, right, left)
            for c, r in all_cells():
                i = r * BOARD_SIZE + c
                for ok, (dc, dr) in (
                    (up, (0, 1)),
                    (down, (0, -1)),
                    (right, (1, 0)),
                    (left, (-1, 0)),
                ):
                    nc, nr = c + dc, r + dr
                    expect = 0 <= nc < 9 and 0 <= nr < 9 and not s.blocked(c, r, dc, dr)
                    assert bool((ok >> i) & 1) == expect, (x, y, orient, c, r, dc, dr)


def test_boxed_pawn_has_no_distance():
    s = mk(white="a1", h={(0, 0)}, v={(1, 0)})
    m = masks_of(s)
    assert player_distance(m, (0, 0), WHITE) is None
    assert bitpaths.shortest_path(m, (0, 0), WHITE) is None
    assert player_distance(m, (4, 8), BLACK) == paths.shortest_path_length(s, BLACK)


@pytest.mark.parametrize("seed", range(8))
def test_distance_and_path_equal_reference_on_random_games(seed):
    rng = random.Random(seed)
    for s in random_game(seed, max_plies=140, wall_bias=0.8):
        m = masks_of(s)
        for player in (WHITE, BLACK):
            cells = [s.pawns[player]] + rng.sample(all_cells(), 6)
            for c in cells:
                t = with_pawn(s, player, c)
                assert player_distance(m, c, player) == paths.shortest_path_length(t, player)
                # the exact same path (search wall candidates depend on it)
                assert bitpaths.shortest_path(m, c, player) == paths.shortest_path(t, player)


@pytest.mark.parametrize("seed", range(4))
def test_add_wall_equals_rebuilt_masks(seed):
    for s in random_game(seed, max_plies=100, wall_bias=0.8):
        m = masks_of(s)
        for o in (H, V):
            for x in range(WALL_GRID):
                for y in range(WALL_GRID):
                    w = WallMove(x, y, o)
                    if not wall_fits(s, w):
                        continue
                    h = s.h_walls | {(x, y)} if o == H else s.h_walls
                    v = s.v_walls | {(x, y)} if o == V else s.v_walls
                    assert add_wall(m, x, y, o) == build_masks(h, v)
