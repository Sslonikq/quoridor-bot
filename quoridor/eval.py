"""Static evaluation. Pure: the score is from the point of view of the side to move.

Terms (in centi-steps, 100 = one step of shortest path):
  * path race: STEP * (opponent distance - own distance);
  * wall stock: WALL * (own walls left - opponent walls left);
  * walls-out races: when one side has no walls left, the race is (almost) decided by
    the distances alone, so a clear race gets a large bonus RACE (jumps are ignored,
    which is why it is not a proven WIN).
Proven wins/losses (WIN) are found by the search, not here.
"""

from __future__ import annotations

from collections.abc import Callable

from quoridor.paths import shortest_path_length
from quoridor.state import GameState

WIN = 1_000_000  # mate score; the search uses WIN - ply
STEP = 100
WALL = 25
RACE = 5_000

DistFn = Callable[[GameState, int], int | None]


def evaluate_dists(
    d_me: int, d_op: int, walls_me: int, walls_op: int, *, wall_weight: int = WALL
) -> int:
    """Score for the side to move from both distances and wall counts."""
    score = STEP * (d_op - d_me) + wall_weight * (walls_me - walls_op)
    # Side to move is half a step ahead in a pure race: it wins iff d_me <= d_op.
    if walls_op == 0 and d_me <= d_op:
        score += RACE  # opponent cannot slow us down any more
    elif walls_me == 0 and d_op < d_me:
        score -= RACE  # we cannot slow the opponent down
    return score


def evaluate(
    state: GameState, dist: DistFn = shortest_path_length, *, wall_weight: int = WALL
) -> int:
    """Static score of `state` for `state.turn`. Positions must be non-terminal and legal."""
    me = state.turn
    d_me = dist(state, me)
    d_op = dist(state, 1 - me)
    assert d_me is not None and d_op is not None, "legal positions always have paths"
    return evaluate_dists(
        d_me, d_op, state.walls_left[me], state.walls_left[1 - me], wall_weight=wall_weight
    )
