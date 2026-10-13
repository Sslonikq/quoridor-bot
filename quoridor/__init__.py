"""Quoridor engine: rules, paths, notation."""

from quoridor.notation import format_move, parse_move
from quoridor.paths import has_path_to_goal, shortest_path_length
from quoridor.rules import (
    apply_move,
    is_legal_move,
    is_terminal,
    is_wall_legal,
    legal_moves,
    pawn_moves,
    wall_moves,
    winner,
)
from quoridor.state import BLACK, BOARD_SIZE, WHITE, GameState, PawnMove, WallMove

__all__ = [
    "BLACK",
    "BOARD_SIZE",
    "WHITE",
    "GameState",
    "PawnMove",
    "WallMove",
    "apply_move",
    "format_move",
    "has_path_to_goal",
    "is_legal_move",
    "is_terminal",
    "is_wall_legal",
    "legal_moves",
    "parse_move",
    "pawn_moves",
    "shortest_path_length",
    "wall_moves",
    "winner",
]
