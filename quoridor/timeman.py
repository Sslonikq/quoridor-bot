"""Time budget per move for a sudden-death clock (default 10 minutes per game). Pure."""

from __future__ import annotations

GAME_TIME = 600.0  # seconds per player per game
RESERVE = 30.0  # never planned to be spent: safety margin for overhead / slow moves
EXPECTED_MOVES = 35  # own moves in a typical game
MIN_MOVES_LEFT = 10  # never plan for fewer remaining moves than this
MIN_TIME = 0.05
MAX_TIME = 15.0


def move_budget(
    remaining: float,
    moves_played: int,
    *,
    reserve: float = RESERVE,
    expected_moves: int = EXPECTED_MOVES,
    min_time: float = MIN_TIME,
    max_time: float = MAX_TIME,
) -> float:
    """Seconds to spend on the next move.

    remaining: time left on our clock; moves_played: our moves made so far.
    The usable time (remaining - reserve) is spread over the expected remaining moves,
    with more weight in the middlegame (moves 4..20) and less in the opening. Never more
    than a third of the usable time, never more than max_time. Inside the reserve only
    a tiny slice is spent, so the clock does not run out.
    """
    if remaining <= 0:
        return 0.0
    usable = remaining - reserve
    if usable <= 0:
        return max(0.0, min(min_time, remaining / 50))
    moves_left = max(MIN_MOVES_LEFT, expected_moves - moves_played)
    t = usable / moves_left
    if moves_played < 3:
        t *= 0.5  # opening: positions are simple, little to gain
    elif moves_played <= 20:
        t *= 1.5  # middlegame: walls fly, deeper search pays
    t = min(t, max_time, usable / 3)
    return max(t, min(min_time, usable))
