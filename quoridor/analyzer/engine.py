"""Analyzer logic without HTTP: replay a move list, describe a position, rank root moves.

The client never sends a GameState: only a list of moves in the site notation, which is
replayed here from the initial position with rules.apply_move (illegal moves are rejected).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from quoridor.bots import V3_WALL_WEIGHT
from quoridor.eval import WIN
from quoridor.notation import format_move, parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move, apply_move_unchecked, legal_moves, pawn_moves, winner
from quoridor.search import V2_TT_BITS, Searcher, is_win_score
from quoridor.state import GameState, Move, PawnMove, goal_row

MAX_MOVES = 400  # more than any real game (arena limit is 300 plies)
DEFAULT_TIME = 2.0
MIN_TIME = 0.1
MAX_TIME = 10.0
TOP_N = 3


class MoveListError(ValueError):
    """The move list is malformed or contains an illegal move."""

    def __init__(self, message: str, number: int | None = None) -> None:
        super().__init__(message)
        self.number = number  # 1-based index of the bad move (None: not about one move)


def replay(moves: object) -> GameState:
    """Play `moves` (list of notation strings) from the initial position."""
    if not isinstance(moves, list):
        raise MoveListError("moves must be a list of strings")
    if len(moves) > MAX_MOVES:
        raise MoveListError(f"too many moves (max {MAX_MOVES})")
    state = GameState.initial()
    for i, raw in enumerate(moves, 1):
        if not isinstance(raw, str):
            raise MoveListError(f"move {i}: not a string", i)
        try:
            move = parse_move(raw)
        except ValueError:
            raise MoveListError(f"move {i}: cannot parse {raw!r}", i) from None
        try:
            state = apply_move(state, move)
        except ValueError:
            raise MoveListError(f"move {i}: {raw!r} is illegal", i) from None
    return state


def move_json(move: Move) -> dict:
    """A move as JSON: its site name plus geometry, so that the client never parses names.

    Pawn: {"name", "k": "p", "c", "r"} (0-based column / row of the destination).
    Wall: {"name", "k": "w", "x", "y", "o"} (internal wall-grid coordinates of state.py,
    o = "h" | "v"). The name comes from notation.format_move; tests check that the geometry
    equals notation.parse_move(name).
    """
    name = format_move(move)
    if isinstance(move, PawnMove):
        return {"name": name, "k": "p", "c": move.col, "r": move.row}
    return {"name": name, "k": "w", "x": move.x, "y": move.y, "o": move.orient}


def describe(state: GameState, moves: list[str] | None = None) -> dict:
    """JSON-ready position: pawns, walls, stock, turn, winner, distances, legal moves.

    `pawns` are {"name", "k": "p", "c", "r"}; `walls` and `legal` are move_json objects;
    `last` is the geometry of the last move of `moves` (None for the initial position).
    """
    walls = sorted((move_json(w) for w in state.walls()), key=lambda m: m["name"])
    legal = [move_json(m) for m in legal_moves(state)]
    return {
        "pawns": [move_json(PawnMove(*p)) for p in state.pawns],
        "last": move_json(parse_move(moves[-1])) if moves else None,
        "walls": walls,
        "walls_left": list(state.walls_left),
        "turn": state.turn,
        "winner": winner(state),
        "dist": [shortest_path_length(state, p) for p in (0, 1)],
        "legal": legal,
    }


def _why(state: GameState, move: Move, child: GameState, score: int) -> str:
    me, op = state.turn, 1 - state.turn
    if isinstance(move, PawnMove) and move.row == goal_row(me):
        return "выигрывает на месте"
    if is_win_score(score):
        return "форсированный выигрыш" if score > 0 else "проигрыш при лучшей игре"
    my0, my1 = shortest_path_length(state, me), shortest_path_length(child, me)
    op0, op1 = shortest_path_length(state, op), shortest_path_length(child, op)
    assert None not in (my0, my1, op0, op1)
    if isinstance(move, PawnMove):
        if my1 < my0:
            return f"путь {my0}→{my1}"
        return f"путь не короче ({my1})"
    if op1 > op0:
        extra = f", свой {my0}→{my1}" if my1 != my0 else ""
        return f"соперник {op0}→{op1}{extra}"
    return "запас ходов, выигрыша в пути нет"


class _Cancelled(Exception):
    pass


class _CancellableSearcher(Searcher):
    """v3 searcher that aborts as soon as `cancelled()` turns true (checked at every leaf)."""

    def __init__(self, cancelled: Callable[[], bool]) -> None:
        super().__init__(0, tt_bits=V2_TT_BITS, wall_weight=V3_WALL_WEIGHT)
        self._cancelled = cancelled

    def evaluate(self, state: GameState) -> int:
        if self._cancelled():
            raise _Cancelled
        return super().evaluate(state)


@dataclass(frozen=True)
class RankedMove:
    move: Move
    score: int  # centi-steps for the side to move (WIN-ish for proven results)


def _score_white(state: GameState, score: int) -> int:
    return score if state.turn == 0 else -score


def rank_moves(
    state: GameState,
    budget: float,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[list[RankedMove], int]:
    """Rank root moves by v3 search. Returns (moves best first, depth from the root).

    The depth is counted from the position `state` itself: iteration k searches each
    child to k plies, so the root move plus that is k + 1 plies (a win on the spot: 1).

    Each iteration k searches every root move (pruned wall set + pawn moves) to depth k
    with a full window, so the scores are comparable; an iteration that does not finish
    inside the budget is dropped. Iteration 1 always completes. Raises ValueError on a
    terminal position.
    """
    if winner(state) is not None:
        raise ValueError("terminal position")
    t0 = time.perf_counter()
    goal = goal_row(state.turn)
    wins = [m for m in pawn_moves(state) if m.row == goal]
    if wins:
        return [RankedMove(m, WIN - 1) for m in wins[:TOP_N]], 1
    searcher = _CancellableSearcher(cancelled)
    root = searcher.ordered_moves(state)
    if not root:
        root = [(m, apply_move_unchecked(state, m)) for m in legal_moves(state)]
    ranked: list[RankedMove] = []
    done = 0
    depth = 1
    while True:
        scores: list[RankedMove] = []
        complete = True
        for mv, child in root:
            if cancelled():
                return ranked, done
            if winner(child) is not None:  # unreachable: wins handled above
                scores.append(RankedMove(mv, WIN - 1))
                continue
            left = budget - (time.perf_counter() - t0)
            if depth > 1 and left <= 0:
                complete = False
                break
            try:
                res = searcher.search(child, depth, max(left, 0.01) if depth > 1 else None)
            except _Cancelled:
                return ranked, done
            if depth > 1 and res.depth < depth and not is_win_score(res.score):
                complete = False
                break
            scores.append(RankedMove(mv, -res.score))
        if not complete or cancelled():
            break
        scores.sort(key=lambda r: -r.score)
        ranked, done = scores, depth + 1
        if is_win_score(ranked[0].score) or len(root) == 1:
            break
        if time.perf_counter() - t0 > budget / 2 or depth >= 40:
            break  # the next iteration would most likely not finish
        depth += 1
    return ranked, done


def analyze(
    moves: object,
    seconds: float = DEFAULT_TIME,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Top-3 moves with scores and reasons, plus the position evaluation (white's view).

    Each top entry is move_json + score (centi-steps, white's view), mate (proven win/loss,
    set by the server so that the client knows no score constants) and why. `depth` is
    counted from the analysed position (see rank_moves).
    """
    state = replay(moves)
    pos = describe(state, moves)
    if winner(state) is not None:
        return {
            "position": pos,
            "top": [],
            "eval": None,
            "eval_mate": False,
            "depth": 0,
            "cancelled": False,
        }
    seconds = min(MAX_TIME, max(MIN_TIME, seconds))
    t0 = time.perf_counter()
    ranked, depth = rank_moves(state, seconds, cancelled)
    if cancelled() or not ranked:
        return {
            "position": pos,
            "top": [],
            "eval": None,
            "eval_mate": False,
            "depth": 0,
            "cancelled": True,
        }
    top = []
    for r in ranked[:TOP_N]:
        child = apply_move_unchecked(state, r.move)
        top.append(
            {
                **move_json(r.move),
                "mate": is_win_score(r.score),
                "score": _score_white(state, r.score),  # centi-steps, white's view
                "why": _why(state, r.move, child, r.score),
            }
        )
    return {
        "position": pos,
        "top": top,
        "eval": top[0]["score"],
        "eval_mate": top[0]["mate"],
        "depth": depth,
        "elapsed": round(time.perf_counter() - t0, 3),
        "cancelled": False,
    }
