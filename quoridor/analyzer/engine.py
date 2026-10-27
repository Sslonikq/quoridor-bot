"""Analyzer logic without HTTP: replay a move list, describe a position, rank root moves.

The client never sends a GameState: only a list of moves in the site notation, which is
replayed here from the initial position with rules.apply_move (illegal moves are rejected).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from quoridor.bots import MCTS2_OPTIONS, MCTS_SEARCHER, V4_SETTINGS, V5_SETTINGS
from quoridor.endgame import mate_text
from quoridor.eval import WIN
from quoridor.mcts import MCTS, PROGRESS_EVERY, MCTSCancelled
from quoridor.notation import format_move, parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move, apply_move_unchecked, legal_moves, pawn_moves, winner
from quoridor.search import Searcher, display_score, is_win_score
from quoridor.state import GameState, Move, PawnMove, goal_row

MAX_MOVES = 400  # more than any real game (arena limit is 300 plies)
DEFAULT_TIME = 5.0  # seconds per hint (the UI may send its own "time")
DEEP_TIME = 20.0  # kept for compatibility; the UI modes are 2 / 10 / 30 s (it sends "time")
BOTS = ("v4", "v5", "mcts")  # white list of the "bot" parameter
DEFAULT_BOT = "v4"  # API default when "bot" is absent (the UI sends its own: MCTS by default)
MCTS_SEED = 0
MIN_TIME = 0.1
MAX_TIME = 30.0  # upper bound of an explicit "time"; the server rejects more
TOP_N = 3
ALT_SHARE = 0.1  # min. extra share for the top-2/3; the best move's search gets the whole budget


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
    """v4 / v5 searcher that aborts as soon as `cancelled()` turns true (checked at every leaf)."""

    def __init__(self, cancelled: Callable[[], bool], settings: dict | None = None) -> None:
        super().__init__(0, **(V4_SETTINGS if settings is None else settings))
        self._cancelled = cancelled

    def evaluate(self, state: GameState) -> int:
        if self._cancelled():
            raise _Cancelled
        return super().evaluate(state)


@dataclass(frozen=True)
class RankedMove:
    move: Move
    score: int  # display centi-steps for the side to move (WIN-ish for proven results)
    depth: int = 1  # plies from the root at which the score was found
    exact: bool = True  # False: an upper bound (an alternative that was only refuted)
    visits: int = 0  # MCTS only: simulations that went through this root move
    mate_in: int = 0  # endgame solver: +n wins on ply n, -n loses on ply n (0: not solved)


def _score_white(state: GameState, score: int) -> int:
    return score if state.turn == 0 else -score


# Tree reuse between analyses (report 0083): one MCTS kept between requests. A position
# equal to the last analysed one, its child or its grandchild (the next move or two typed
# in, a time mode switch) continues that tree; anything else starts a new one inside
# MCTS.search. The lock serialises the searches; a request that cannot get it soon (the
# cancelled older search finishes within a batch of a few ms) searches with its own
# fresh MCTS.
_MCTS_LOCK = threading.Lock()
_MCTS_WAIT = 1.0  # seconds to wait for the shared tree
_mcts_shared: MCTS | None = None


def _new_mcts() -> MCTS:
    return MCTS(MCTS_SEED, MCTS_SEARCHER, **MCTS2_OPTIONS)  # mcts2 + endgame solver (0084)


def reset_mcts_tree() -> None:
    """Forget the kept MCTS tree (tests; frees its memory)."""
    global _mcts_shared
    with _MCTS_LOCK:
        _mcts_shared = None


def _rank_mcts(
    state: GameState,
    budget: float,
    cancelled: Callable[[], bool],
    on_progress: Callable[[int, int], None] | None,
) -> tuple[list[RankedMove], int, int, int]:
    global _mcts_shared
    if not _MCTS_LOCK.acquire(timeout=_MCTS_WAIT):
        return _rank_mcts_with(_new_mcts(), state, budget, cancelled, on_progress)
    try:
        if _mcts_shared is None:
            _mcts_shared = _new_mcts()
        return _rank_mcts_with(_mcts_shared, state, budget, cancelled, on_progress)
    finally:
        _MCTS_LOCK.release()


def _rank_mcts_with(
    mcts: MCTS,
    state: GameState,
    budget: float,
    cancelled: Callable[[], bool],
    on_progress: Callable[[int, int], None] | None,
) -> tuple[list[RankedMove], int, int, int]:
    """Top moves of the PUCT-MCTS (the bot of bots.make_mcts2_*, report 0084), the whole
    budget in one search: the best move is the bot's choice, the others are the other
    most visited root moves (by visits, then by value; no alternative is shown above the
    best). Returns (moves, depth, simulations, reused); depth is the length of the most
    visited line, reused = simulations of the kept tree (see _MCTS_LOCK) the root started with.
    ([], 0, n, 0) when cancelled (the tree is kept for the next request). Cancellation is
    checked between simulation batches (a few ms), progress (line, simulations) is
    reported at most every PROGRESS_EVERY s. A result proven by the endgame solver (both
    sides <= 2 walls) is the one move with its mate distance (mate_in), no tree."""
    try:
        res = mcts.search(
            state,
            None,
            budget,
            should_stop=cancelled,
            on_progress=on_progress,
            progress_every=PROGRESS_EVERY,
            keep_on_cancel=True,
        )
    except MCTSCancelled as e:
        return [], 0, e.sims, 0
    if res.solved is not None:  # proven by the endgame solver: no tree, one move
        solved = RankedMove(res.move, res.score, res.depth, True, 0, res.solved.plies)
        return [solved], res.depth, 0, 0
    best = RankedMove(res.move, res.score, res.depth, True, res.visits)
    ranked = [best]
    rest = [c for c in mcts.root_children() if c.move != res.move and c.visits > 0]
    rest.sort(key=lambda c: (c.proven != -1, c.visits, c.value), reverse=True)
    for c in rest[: TOP_N - 1]:
        ranked.append(
            RankedMove(c.move, min(c.score, best.score), c.depth, c.score <= best.score, c.visits)
        )
    return ranked, res.depth, res.nodes, max(0, res.visits - 1 - res.nodes)


def rank_moves(
    state: GameState,
    budget: float,
    cancelled: Callable[[], bool] = lambda: False,
    on_depth: Callable[..., None] | None = None,
    bot: str = DEFAULT_BOT,
) -> tuple[list[RankedMove], int]:
    """Top moves of `bot` ("v4" | "v5" | "mcts"). For "mcts" see _rank_mcts; the rest of
    this text is about v4 / v5 (same search, v5 has its own settings).
    Returns (moves best first, depth of the best move from the root).

    The best move comes from ONE ordinary search (Searcher.search_top: the same search
    as SearchBot.choose_move with move_time = budget, TT, iterative deepening, so the same
    depth); the other TOP_N - 1 moves from a cheap search_root of the remaining root moves
    in what is left of the budget (at least ALT_SHARE of it), reusing the TT. The depth
    is counted from `state` (the root move is ply 1; a win on the spot: 1). Scores are
    display scores (search.display_score: no odd/even depth swing); alternatives are
    never shown above the best move. Raises ValueError on a terminal position; returns
    ([], 0) when cancelled.
    """
    if winner(state) is not None:
        raise ValueError("terminal position")
    goal = goal_row(state.turn)
    wins = [m for m in pawn_moves(state) if m.row == goal]
    if wins:
        return [RankedMove(m, WIN - 1) for m in wins[:TOP_N]], 1
    if bot not in BOTS:
        raise ValueError(f"unknown bot {bot!r}")
    if bot == "mcts":
        ranked, depth, _, _ = _rank_mcts(state, budget, cancelled, on_depth)
        return ranked, depth
    searcher = _CancellableSearcher(cancelled, V5_SETTINGS if bot == "v5" else None)
    searcher.on_depth = on_depth  # live depth of the main search (completed iterations)
    try:
        res = searcher.search_top(state, budget, TOP_N, ALT_SHARE)
    except _Cancelled:
        return [], 0
    best = res.best
    ranked = [RankedMove(best.move, best.display_score, best.depth, True)]
    ranked += [RankedMove(a.move, a.score, a.depth, a.exact) for a in res.alts]
    return ranked, best.depth


def _clamp_time(seconds: float) -> float:
    return min(MAX_TIME, max(MIN_TIME, seconds))


def evaluate_move(
    moves: object,
    move: object,
    seconds: float = DEFAULT_TIME,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Score of one given move in the position after `moves` (e.g. the move actually
    played, which may be outside the analyzer's top: the root wall set is pruned by
    wall_limit). One v4 search_root over just that move, the whole budget.

    Returns move_json + score (display centi-steps, white's view), mate, depth (from the
    position, the move itself is ply 1), why, elapsed, cancelled. MoveListError if the
    game is over or `move` is not a legal move (number = its index after `moves`).
    """
    state = replay(moves)
    assert isinstance(moves, list)
    number = len(moves) + 1
    if winner(state) is not None:
        raise MoveListError("the game is over", number)
    if not isinstance(move, str):
        raise MoveListError(f"move {number}: not a string", number)
    try:
        mv = parse_move(move)
        apply_move(state, mv)
    except ValueError:
        raise MoveListError(f"move {number}: {move!r} is illegal", number) from None
    t0 = time.perf_counter()
    scored = _score_move(state, mv, seconds, cancelled)
    if scored is None:
        return {**move_json(mv), "cancelled": True}
    score, depth = scored
    return {
        **move_json(mv),
        "score": _score_white(state, score),
        "mate": is_win_score(score),
        "depth": depth,
        "why": _why(state, mv, apply_move_unchecked(state, mv), score),
        "elapsed": round(time.perf_counter() - t0, 3),
        "cancelled": False,
    }


def _score_move(
    state: GameState, mv: Move, seconds: float, cancelled: Callable[[], bool]
) -> tuple[int, int] | None:
    """(display score for the side to move, depth) of one legal move; None if cancelled."""
    searcher = _CancellableSearcher(cancelled)
    try:
        rs = searcher.search_root(state, time_limit=_clamp_time(seconds), moves=[mv])
    except _Cancelled:
        return None
    r = rs.scores[0]
    return display_score(r.score, r.depth), r.depth


def _same_depth_scores(
    state: GameState,
    best: Move,
    played: Move,
    seconds: float,
    cancelled: Callable[[], bool],
) -> tuple[int, int, int] | None:
    """(best score, played score, depth) of two moves from ONE search_root: both at the
    same completed depth, as deep as `seconds` allows (it stops early once both are
    proven), so a slower mate of the played move can still be found. Display scores for
    the side to move; None if cancelled."""
    searcher = _CancellableSearcher(cancelled)
    try:
        rs = searcher.search_root(state, time_limit=_clamp_time(seconds), moves=[best, played])
    except _Cancelled:
        return None
    sc = {r.move: display_score(r.score, r.depth) for r in rs.scores}
    return sc[best], sc[played], rs.depth


def _top_entries(state: GameState, ranked: list[RankedMove]) -> list[dict]:
    top = []
    for r in ranked[:TOP_N]:
        child = apply_move_unchecked(state, r.move)
        top.append(
            {
                **move_json(r.move),
                "mate": is_win_score(r.score),
                "score": _score_white(state, r.score),  # centi-steps, white's view
                "why": mate_text(r.mate_in) if r.mate_in else _why(state, r.move, child, r.score),
                "depth": r.depth,  # plies from the position for this score
                "exact": r.exact,  # False: an upper bound ("not better than")
                **({"visits": r.visits} if r.visits else {}),  # MCTS: simulations through it
                **({"mate_in": r.mate_in} if r.mate_in else {}),  # solver: +-plies to the end
            }
        )
    return top


def analyze(
    moves: object,
    seconds: float = DEFAULT_TIME,
    cancelled: Callable[[], bool] = lambda: False,
    on_depth: Callable[..., None] | None = None,
    bot: str = DEFAULT_BOT,
) -> dict:
    """Top-3 moves with scores and reasons, plus the position evaluation (white's view).

    Each top entry is move_json + score (display centi-steps, white's view), mate (proven
    win/loss, set by the server so that the client knows no score constants), why, depth
    (plies of that entry's search) and exact (False: an upper bound). The top-level
    `depth` is the depth of the best move's search, counted from the analysed position
    (see rank_moves). `on_depth(d)` is called after every completed iteration (live depth).
    `bot` is "v4" | "v5" | "mcts" (ValueError otherwise); the result carries it as "bot".
    For MCTS: depth = length of the main line, `on_depth(d, sims)`, entries have "visits",
    the result has "sims" (simulations of this request) and "reused" (simulations of the
    kept tree from an earlier request on this position, its parent or grandparent).
    """
    if bot not in BOTS:
        raise ValueError(f"unknown bot {bot!r}")
    state = replay(moves)
    pos = describe(state, moves)
    if winner(state) is not None:
        return {
            "position": pos,
            "top": [],
            "eval": None,
            "eval_mate": False,
            "depth": 0,
            "bot": bot,
            "cancelled": False,
        }
    seconds = _clamp_time(seconds)
    t0 = time.perf_counter()
    sims = reused = 0
    if bot == "mcts" and not any(m.row == goal_row(state.turn) for m in pawn_moves(state)):
        ranked, depth, sims, reused = _rank_mcts(state, seconds, cancelled, on_depth)
    else:
        ranked, depth = rank_moves(state, seconds, cancelled, on_depth, bot)
    if cancelled() or not ranked:
        return {
            "position": pos,
            "top": [],
            "eval": None,
            "eval_mate": False,
            "depth": 0,
            "bot": bot,
            "cancelled": True,
        }
    top = _top_entries(state, ranked)
    return {
        "position": pos,
        "bot": bot,
        **({"sims": sims, "reused": reused} if bot == "mcts" else {}),
        "top": top,
        "eval": top[0]["score"],
        "eval_mate": top[0]["mate"],
        **({"eval_text": top[0]["why"]} if "mate_in" in top[0] else {}),  # "выигрыш за N ходов"
        "depth": depth,
        "elapsed": round(time.perf_counter() - t0, 3),
        "cancelled": False,
    }


# ---------------------------------------------------------------------------------------
# Game text and game review
# ---------------------------------------------------------------------------------------

_MOVE_NO = re.compile(r"^\d+\.+")


def parse_game_text(text: object) -> list[str]:
    """Moves (site notation, normalised) from pasted text.

    Moves are separated by spaces / line breaks / commas / semicolons ("d1 e8 c1 ..." and
    the fixtures' "<white> <black>" lines are the same thing). Everything after "#" is a
    comment; a leading move number ("12.") is dropped. The whole list is replayed from the
    initial position: MoveListError (number = 1-based move) on junk or an illegal move.
    """
    if not isinstance(text, str):
        raise MoveListError("text must be a string")
    tokens: list[str] = []
    for line in text.splitlines():
        for tok in re.split(r"[\s,;]+", line.split("#", 1)[0]):
            tok = _MOVE_NO.sub("", tok)  # parse_move normalises the case
            if tok:
                tokens.append(tok)
    if len(tokens) > MAX_MOVES:
        raise MoveListError(f"too many moves (max {MAX_MOVES})")
    moves = []
    for i, tok in enumerate(tokens, 1):
        try:
            moves.append(format_move(parse_move(tok)))
        except ValueError:
            raise MoveListError(f"move {i}: cannot parse {tok!r}", i) from None
    if not moves:
        raise MoveListError("no moves found")
    replay(moves)
    return moves


# Review marks. Loss = (best score - score of the played move) for the side that moved,
# in centi-steps; a score is clamped to +-REVIEW_CAP (10 steps) and a proven win / loss
# counts as +-REVIEW_MATE (12 steps), so that missing a forced win or walking into a forced
# loss is a blunder even from an already lopsided position. Never below 0.
REVIEW_TIME = 1.0  # seconds per position (the UI may send its own "time")
REVIEW_CAP = 1000
REVIEW_MATE = 1200
MARK_BLUNDER = 300  # loss >= 3.0 steps: "??"
MARK_MISTAKE = 150  # >= 1.5: "?"
MARK_INACCURACY = 75  # >= 0.75: "?!"
MARK_ONLY_GAP = 150  # played the best move and the 2nd one is >= 1.5 worse: "!"
PLAYED_SHARE = 0.4  # budget share for a separate search of a played move outside the top


def _cap(score: int) -> int:
    if is_win_score(score):
        return REVIEW_MATE if score > 0 else -REVIEW_MATE
    return max(-REVIEW_CAP, min(REVIEW_CAP, score))


def mark_for(loss: int, played_is_best: bool, gap: int | None) -> str | None:
    """ "b" (??), "m" (?), "i" (?!), "g" (!) or None, from the loss and the best-vs-2nd gap."""
    if loss >= MARK_BLUNDER:
        return "b"
    if loss >= MARK_MISTAKE:
        return "m"
    if loss >= MARK_INACCURACY:
        return "i"
    if played_is_best and gap is not None and gap >= MARK_ONLY_GAP:
        return "g"
    return None


def move_accuracy(loss: int) -> float:
    """100 for the best move, minus 25 per step lost (loss is in centi-steps), at least 0."""
    return max(0.0, 100.0 - loss / 4)


def _review_position(
    state: GameState,
    played: Move | None,
    seconds: float,
    cancelled: Callable[[], bool],
) -> dict | None:
    """One review step: the best moves of `state` and (if given) the played move's loss."""
    if winner(state) is not None:
        sign = 1 if winner(state) == 0 else -1
        return {"eval": sign * (WIN - 1), "eval_mate": True, "depth": 0, "top": [], "elapsed": 0.0}
    t0 = time.perf_counter()
    rank_time = seconds if played is None else seconds * (1 - PLAYED_SHARE)
    ranked, depth = rank_moves(state, rank_time, cancelled)
    if cancelled() or not ranked:
        return None
    top = _top_entries(state, ranked)
    step: dict = {
        "eval": top[0]["score"],
        "eval_mate": top[0]["mate"],
        "depth": depth,
        "top": top,
    }
    if played is not None:
        best = ranked[0]
        hit = next((r for r in ranked if r.move == played), None)
        if hit is not None and hit.exact:
            p_score, p_depth, in_top = hit.score, hit.depth, True
        else:
            scored = _score_move(state, played, seconds * PLAYED_SHARE, cancelled)
            if scored is None or cancelled():
                return None
            (p_score, p_depth), in_top = scored, hit is not None
        best_score = best.score
        if (
            played != best.move
            and is_win_score(best_score)
            and best_score > 0
            and not is_win_score(p_score)
        ):
            # A mate of the best move vs a non-mate of the played one from another search
            # (shallower, or a slower mate beyond its horizon) is no loss measure (review
            # 0057, minor 4): both again in one search, at one common depth, as deep as
            # one more `seconds` allows. Only when this mismatch occurs.
            same = _same_depth_scores(state, best.move, played, seconds, cancelled)
            if same is None or cancelled():
                return None
            best_score, p_score, p_depth = same
        loss = 0 if played == best.move else max(0, _cap(best_score) - _cap(p_score))
        gap = _cap(best.score) - _cap(ranked[1].score) if len(ranked) > 1 else None
        step.update(
            {
                "played": {
                    "score": _score_white(state, p_score),
                    "mate": is_win_score(p_score),
                    "depth": p_depth,
                    "in_top": in_top,
                },
                "loss": loss,
                "mark": mark_for(loss, played == best.move, gap),
                "accuracy": round(move_accuracy(loss), 1),
            }
        )
    step["elapsed"] = round(time.perf_counter() - t0, 3)
    return step


def review_game(
    moves: object,
    seconds: float = REVIEW_TIME,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[dict]:
    """Review of the whole game: yields one step per position 0..len(moves), in order.

    Step i is the position before move i (the last one is the final position: no move).
    Fields: index, move (name or None), eval / eval_mate (white's view, of the position),
    depth, top (like /api/analyze), elapsed and, for a played move: played {score, mate,
    depth, in_top}, loss (centi-steps, side to move, >= 0), mark, accuracy (0..100).
    Stops silently when cancelled. MoveListError (before the first step) for bad moves.
    """
    replay(moves)
    assert isinstance(moves, list)
    parsed = [parse_move(m) for m in moves]
    seconds = _clamp_time(seconds)
    state = GameState.initial()
    for i in range(len(moves) + 1):
        if cancelled():
            return
        played = parsed[i] if i < len(moves) else None
        step = _review_position(state, played, seconds, cancelled)
        if step is None:
            return
        yield {"index": i, "move": format_move(played) if played else None, **step}
        if played is not None:
            state = apply_move_unchecked(state, played)


def summarize(steps: list[dict]) -> dict:
    """Per side: moves, accuracy (mean of per-move accuracies, None without moves), and the
    number of "b" / "m" / "i" / "g" marks. White moves have even step indexes."""
    out = {}
    for side, name in ((0, "white"), (1, "black")):
        mine = [s for s in steps if s.get("move") and s["index"] % 2 == side]
        accs = [s["accuracy"] for s in mine]
        out[name] = {
            "moves": len(mine),
            "accuracy": round(sum(accs) / len(accs), 1) if accs else None,
            **{k: sum(1 for s in mine if s["mark"] == k) for k in "bmig"},
        }
    return out
