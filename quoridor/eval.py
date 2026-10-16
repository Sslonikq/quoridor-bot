"""Static evaluation. Pure: the score is from the point of view of the side to move.

Terms (in centi-steps, 100 = one step of shortest path):
  * path race: STEP * (opponent distance - own distance);
  * wall stock: wall_weight * (own walls left - opponent walls left); WALL (25) is the
    v1/v2 weight, WALL_V3 (125) the v3 one: a wall in stock is worth more than a step, so
    a wall that wins only one step is not spent (equal-time matches, report 0037);
  * walls-out races: when one side has no walls left, the race is (almost) decided by
    the distances alone, so a clear race gets a large bonus RACE (jumps are ignored,
    which is why it is not a proven WIN).
  * v4 (evaluate_v4): the v3 terms plus a wall race when only one side has walls left:
    the net gain of that side's greedy wall plan and a bonus if the plan wins the race;
    while both have walls, THREAT_V4 per step of (the most our best single wall adds to
    the opponent's path - the most the opponent's adds to ours), computed in
    search.Searcher.evaluate (it needs the memoised wall candidates). Option `reserve`
    (report 0066, reserve_term): the opponent's walls in stock are a threat to our path
    when we have 0-2 walls left to answer them.
Proven wins/losses (WIN) are found by the search, not here.
"""

from __future__ import annotations

from collections.abc import Callable

from quoridor.paths import shortest_path_length
from quoridor.state import GameState

WIN = 1_000_000  # mate score; the search uses WIN - ply
STEP = 100
WALL = 25  # v1 / v2 (phase 4)
WALL_V3 = 125  # v3: tuned at equal time (0.2 s/move), see report 0037
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


PLAN_V4 = 50  # v4: per step of the greedy wall plan's net gain (one side out of walls)
SOFT_V4 = 425  # v4: bonus for a race won only thanks to that plan
THREAT_V4 = 60  # v4: per step of (our best single wall's gain - the opponent's), both have walls
RACE_MIN_GAIN_V4 = 2  # v4: the greedy plan takes a wall only if its gain is at least this
RESERVE_V4 = 50  # v4 (report 0066): per opponent wall while we have 0 walls (reserve_term)


RESERVE_SHARE = (4, 2, 1)  # quarters of `reserve` per opponent wall, with 0 / 1 / 2 walls of ours


def reserve_term(walls_me: int, walls_op: int, reserve: int) -> int:
    """The opponent's walls in stock as a threat to our path when we are (almost) out of
    walls ourselves, and ours symmetrically (report 0066).

    With few walls of our own we cannot answer the opponent's walls (no blocking wall,
    no counter-fence), so every opponent wall is worth `reserve` more with 0 walls of
    ours, half of it with 1 and a quarter with 2 (RESERVE_SHARE); 3+: nothing. So our
    last walls are not spent cheaply while the opponent holds a big stock (game06,
    moves 17...-20...), and the side with the big stock keeps it too.
    """

    def threat(own: int, other: int) -> int:
        return reserve * RESERVE_SHARE[own] * other // 4 if own < len(RESERVE_SHARE) else 0

    return threat(walls_op, walls_me) - threat(walls_me, walls_op)


def evaluate_v4(
    d_me: int,
    d_op: int,
    walls_me: int,
    walls_op: int,
    e_me: int,
    e_op: int,
    *,
    plan: int = PLAN_V4,
    soft: int = SOFT_V4,
    wall_weight: int = WALL_V3,
    reserve: int = 0,
) -> int:
    """v4 score for the side to move.

    Path race, wall stock (wall_weight per wall) and RACE as in v3. When exactly one side (A) has
    walls left, it cannot be slowed down any more, and its walls are worth what they can
    still do: e_me / e_op are the moves both sides need after A's greedy wall plan
    (search.Searcher.wall_race); otherwise e_* = d_*. The plan's net gain is worth `plan`
    per step on top of the wall stock, and a race that A wins only thanks to the plan
    gets `soft` (a pure race gets RACE, as in v3). A's walls left over by the plan keep
    the stock weight: devaluing them lost matches (report 0061). Weights: logistic fit on self-play
    results, then matches (report 0061).
    """
    score = STEP * (d_op - d_me) + wall_weight * (walls_me - walls_op)
    if reserve:
        score += reserve_term(walls_me, walls_op, reserve)
    if walls_op == 0:
        if d_me <= d_op:
            return score + RACE  # opponent cannot slow us down any more
        if walls_me == 0:  # nobody has walls: a pure race we lose
            return score - RACE
        # we are A
        score += plan * ((e_op - d_op) - (e_me - d_me))
        if e_me <= e_op:
            score += soft
        return score
    if walls_me == 0:
        if d_op < d_me:
            return score - RACE  # we cannot slow the opponent down
        # the opponent is A
        score -= plan * ((e_me - d_me) - (e_op - d_op))
        if e_op < e_me:
            score -= soft
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
