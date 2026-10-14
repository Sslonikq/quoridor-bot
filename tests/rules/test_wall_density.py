"""is_wall_legal: dense boards (equivalence with naive/oracle) and documented precondition."""

import random
from dataclasses import replace

import pytest

from quoridor.paths import has_path_to_goal
from quoridor.rules import is_wall_legal
from quoridor.state import BLACK, WHITE, GameState, WallMove
from tests.helpers import mk
from tests.oracle import ref_wall_legal
from tests.rules.test_phase5_speed import ALL_WALLS, naive_wall_legal


def _dense_states(seed: int):
    """Random pawns, walls_left raised, walls placed until no legal wall remains."""
    rng = random.Random(seed)
    while True:
        pawns = (
            (rng.randrange(9), rng.randrange(8)),
            (rng.randrange(9), rng.randrange(1, 9)),
        )
        if pawns[0] != pawns[1]:
            break
    s = replace(GameState.initial(), pawns=pawns, walls_left=(99, 99))
    while True:
        yield s
        legal = [w for w in ALL_WALLS if is_wall_legal(s, w)]
        if not legal:
            return
        w = rng.choice(legal)
        h = s.h_walls | {(w.x, w.y)} if w.orient == "h" else s.h_walls
        v = s.v_walls | {(w.x, w.y)} if w.orient == "v" else s.v_walls
        s = replace(s, h_walls=h, v_walls=v, turn=1 - s.turn)


@pytest.mark.parametrize("seed", range(6))
def test_dense_boards_equal_naive_and_oracle(seed):
    n_states = 0
    max_walls = 0
    for s in _dense_states(seed):
        n_states += 1
        max_walls = max(max_walls, len(s.h_walls) + len(s.v_walls))
        for w in ALL_WALLS:
            fast = is_wall_legal(s, w)
            assert fast == naive_wall_legal(s, w), (s, w)
            assert fast == ref_wall_legal(s, w), (s, w)
    # really dense: far more walls than the 20 of a normal game
    assert max_walls >= 25, max_walls
    assert n_states >= 25


def test_precondition_cut_off_state_is_documented_behaviour():
    # white is sealed into {d1, e1}: the state violates the precondition of is_wall_legal
    s = mk(white="e1", black="e9", h={(3, 0)}, v={(2, 0), (4, 0)})
    assert not has_path_to_goal(s, WHITE)
    assert has_path_to_goal(s, BLACK)
    far = WallMove(0, 6, "h")
    # documented: the fast check does not notice a pre-existing cut far from the new wall,
    # while the naive full-BFS version rejects every wall in such a state
    assert is_wall_legal(s, far)
    assert not naive_wall_legal(s, far)
