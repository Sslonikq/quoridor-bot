"""Helpers producing reachable positions via seeded random play."""

from __future__ import annotations

import random

from quoridor.rules import apply_move, is_terminal, legal_moves
from quoridor.state import GameState, WallMove


def random_game(seed: int, max_plies: int = 120, wall_bias: float = 0.6):
    """Yield every position of a random game (including the initial one)."""
    rng = random.Random(seed)
    s = GameState.initial()
    yield s
    for _ in range(max_plies):
        if is_terminal(s):
            return
        moves = legal_moves(s)
        walls = [m for m in moves if isinstance(m, WallMove)]
        pawns = [m for m in moves if not isinstance(m, WallMove)]
        pool = walls if walls and (not pawns or rng.random() < wall_bias) else pawns
        s = apply_move(s, rng.choice(pool))
        yield s
