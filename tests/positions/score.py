"""Metric of the "real positions" set: share of positions a bot handles right (phase 9, step 2).

    python -m tests.positions.score --bot v4 --time 3

The bot is a name from the BOTS registry (quoridor.bots); `--time` is its time per move in
seconds (SearchBots and MCTSBots are switched to that move time: no fixed depth or
simulation count, no game clock). Per
position it prints the verdict of every hard check, then the shares: all positions, the core
ones (v4 gave the side >= +3, then lost) and by category (sign of the evaluation, move,
no shuffling). Bots without a score (random, greedy) fail the evaluation checks.

Criteria (positions.json, `checks`; see loader.CHECK_TYPES):
  eval_max      the bot's evaluation for the side to move is <= value (steps);
  forbid_*      the chosen move is not the listed one / not of the kind;
  want_*        the chosen move is the listed one / of the kind;
  avoid_cage    a pawn move does not land on a cell whose path length is >= min_dist once the
                listed walls stand (a wall move never "leads into the cage");
  no_repeat     the bot plays `own_moves` own moves; the opponent answers with its recorded
                moves (when still legal, otherwise a greedy pawn step); the key
                (own cell, own walls left) must not repeat (more than max_repeats times);
  any_of        any of the nested checks holds.
Results go to arena_results/positions/ (ignored by git) unless --out says otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from quoridor.bots import BOTS, Bot, SearchBot, make_bot
from quoridor.mcts import MCTSBot
from quoridor.notation import format_move, parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move_unchecked, is_legal_move, pawn_moves, winner
from quoridor.state import GameState, H, Move, PawnMove, WallMove
from tests.positions.loader import Position, game_moves, load_positions, state_at

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "arena_results" / "positions"  # in .gitignore
PASS_SHARE = 0.80  # the goal of phase 9: >= 80% of the set


@dataclass
class Outcome:
    """What the bot did in a position: the move, its score (steps, None: no score), a trace."""

    move: Move
    score: float | None
    depth: int | None = None
    own_moves: list[str] = field(default_factory=list)  # no_repeat: the bot's own moves
    repeats: int | None = None


def make_scored_bot(name: str, seconds: float, seed: int = 0) -> Bot:
    """Registry bot with `seconds` per move (SearchBot: no fixed depth, no game clock;
    MCTSBot: no simulation count, no game clock)."""
    bot = make_bot(name, seed)
    if isinstance(bot, SearchBot):
        bot.depth = None
        bot.game_time = None
        bot.move_time = seconds
    elif isinstance(bot, MCTSBot):
        bot.sims = None
        bot.game_time = None
        bot.move_time = seconds
    return bot


def score_steps(bot: Bot, side_to_move: int) -> tuple[float | None, int | None]:
    """Evaluation of the last choose_move in steps for the side to move (display score)."""
    last = getattr(bot, "last", None)
    if last is None:
        return None, None
    return last.display_score / 100.0, last.depth


def with_walls(state: GameState, walls: list[str]) -> GameState:
    h, v = set(state.h_walls), set(state.v_walls)
    for name in walls:
        w = parse_move(name)
        assert isinstance(w, WallMove)
        (h if w.orient == H else v).add((w.x, w.y))
    return replace(state, h_walls=frozenset(h), v_walls=frozenset(v))


def cage_dist(state: GameState, side: int, cell: tuple[int, int], walls: list[str]) -> int | None:
    """Path length of `side` from `cell` once `walls` also stand (the opponent pawn stays)."""
    caged = with_walls(state, walls)
    pawns = list(caged.pawns)
    pawns[side] = cell
    return shortest_path_length(replace(caged, pawns=(pawns[0], pawns[1])), side)


def greedy_pawn(state: GameState) -> Move:
    """Pawn step with the shortest own path (the scripted opponent's fallback), by name."""
    me = state.turn
    best: Move | None = None
    best_key: tuple[int, str] | None = None
    for m in pawn_moves(state):
        d = shortest_path_length(apply_move_unchecked(state, m), me)
        key = (10**6 if d is None else d, format_move(m))
        if best_key is None or key < best_key:
            best, best_key = m, key
    assert best is not None
    return best


def give_history(bot: Bot, pos: Position) -> None:
    """Tell a bot that tracks the game (SearchBot.set_history, v5 repetitions) the
    positions of the game before `pos`; other bots are not affected."""
    set_history = getattr(bot, "set_history", None)
    if set_history is not None:
        set_history([state_at(pos.game, i) for i in range(pos.ply)])


def simulate_own_moves(bot: Bot, pos: Position, n: int) -> tuple[list[str], int]:
    """The bot plays n own moves from `pos`; returns (its moves, number of key repeats).

    The opponent plays the recorded moves of the game (by ply) while they are legal and
    otherwise a greedy pawn step. Repeats: positions of the side before each own move and
    after the last one, key = (own cell, own walls left); a wall move changes the key.
    """
    recorded = game_moves(pos.game)
    state = state_at(pos.game, pos.ply)
    side = pos.side
    keys = [(state.pawns[side], state.walls_left[side])]
    played: list[str] = []
    ply = pos.ply
    for _ in range(n):
        if winner(state) is not None:
            break
        move = bot.choose_move(state)
        played.append(format_move(move))
        state = apply_move_unchecked(state, move)
        ply += 1
        keys.append((state.pawns[side], state.walls_left[side]))
        if winner(state) is not None:
            break
        reply: Move | None = None
        if ply < len(recorded):
            cand = parse_move(recorded[ply])
            if is_legal_move(state, cand):
                reply = cand
        state = apply_move_unchecked(state, reply if reply is not None else greedy_pawn(state))
        ply += 1
    return played, len(keys) - len(set(keys))


def play_position(bot: Bot, pos: Position) -> Outcome:
    """Run the bot on the position: one move (+ score), or the own-move simulation."""
    give_history(bot, pos)
    shuffle = [c for c in pos.checks if c["type"] == "no_repeat"]
    if shuffle:
        played, repeats = simulate_own_moves(bot, pos, shuffle[0]["own_moves"])
        return Outcome(parse_move(played[0]), None, own_moves=played, repeats=repeats)
    state = state_at(pos.game, pos.ply)
    move = bot.choose_move(state)
    score, depth = score_steps(bot, state.turn)
    return Outcome(move, score, depth)


def is_wall(move: Move) -> bool:
    return isinstance(move, WallMove)


def run_check(c: dict, pos: Position, out: Outcome) -> bool:
    t = c["type"]
    name = format_move(out.move)
    if t == "any_of":
        return any(run_check(s, pos, out) for s in c["checks"])
    if t == "eval_max":
        return out.score is not None and out.score <= c["value"]
    if t == "forbid_moves":
        return name not in c["moves"]
    if t == "want_moves":
        return name in c["moves"]
    if t == "forbid_kind":
        return is_wall(out.move) != (c["kind"] == "wall")
    if t == "want_kind":
        return is_wall(out.move) == (c["kind"] == "wall")
    if t == "avoid_cage":
        if not isinstance(out.move, PawnMove):
            return True
        state = state_at(pos.game, pos.ply)
        d = cage_dist(state, pos.side, (out.move.col, out.move.row), c["walls"])
        return d is not None and d < c["min_dist"]
    if t == "no_repeat":
        return out.repeats is not None and out.repeats <= c.get("max_repeats", 0)
    raise ValueError(f"unknown check {t!r}")


def describe_check(c: dict) -> str:
    t = c["type"]
    if t == "any_of":
        return "(" + " | ".join(describe_check(s) for s in c["checks"]) + ")"
    if t == "eval_max":
        return f"eval<={c['value']:g}"
    if t in ("forbid_moves", "want_moves"):
        return f"{t.split('_')[0]}:{','.join(c['moves'])}"
    if t in ("forbid_kind", "want_kind"):
        return f"{t.split('_')[0]}:{c['kind']}"
    if t == "avoid_cage":
        return f"no-cage(>={c['min_dist']})"
    return f"no-repeat({c['own_moves']})"


@dataclass
class Verdict:
    pos: Position
    ok: bool
    soft_ok: list[bool]
    out: Outcome
    seconds: float


def judge(pos: Position, out: Outcome) -> tuple[bool, list[bool]]:
    return (
        all(run_check(c, pos, out) for c in pos.checks),
        [run_check(c, pos, out) for c in pos.prefer],
    )


def evaluate(bot_name: str, seconds: float, pos: Position) -> Verdict:
    """Fresh bot per position (the same seed), so that positions do not influence each other."""
    bot = make_scored_bot(bot_name, seconds)
    t0 = time.perf_counter()
    out = play_position(bot, pos)
    dt = time.perf_counter() - t0
    ok, soft = judge(pos, out)
    return Verdict(pos, ok, soft, out, dt)


def _job(args: tuple[str, float, str]) -> Verdict:
    bot_name, seconds, pid = args
    pos = next(p for p in load_positions(validate=False) if p.id == pid)
    return evaluate(bot_name, seconds, pos)


def run_all(
    bot_name: str,
    seconds: float,
    positions: list[Position],
    procs: int = 1,
    on_verdict: Callable[[Verdict], None] | None = None,
) -> list[Verdict]:
    if procs <= 1:
        out = []
        for p in positions:
            v = evaluate(bot_name, seconds, p)
            if on_verdict:
                on_verdict(v)
            out.append(v)
        return out
    with ProcessPoolExecutor(procs) as ex:
        res = list(ex.map(_job, [(bot_name, seconds, p.id) for p in positions]))
    for v in res:
        if on_verdict:
            on_verdict(v)
    return res


def share(verdicts: list[Verdict]) -> tuple[int, int]:
    return sum(v.ok for v in verdicts), len(verdicts)


def summary(verdicts: list[Verdict]) -> dict:
    """Counts used by the report and by the pytest wrapper."""
    res: dict = {"all": share(verdicts), "core": share([v for v in verdicts if v.pos.core])}
    for cat in ("sign", "move", "shuffle"):
        res[cat] = share([v for v in verdicts if v.pos.category == cat])
    return res


def fmt_share(ok_n: tuple[int, int]) -> str:
    ok, n = ok_n
    return f"{ok}/{n} = {100 * ok / n:.0f}%" if n else "0/0"


def line(v: Verdict) -> str:
    p, o = v.pos, v.out
    ev = "-" if o.score is None else (f"{o.score:+.2f}" if abs(o.score) < 900 else "mate")
    extra = ""
    if o.own_moves:
        extra = f" moves={' '.join(o.own_moves)} repeats={o.repeats}"
    checks = " ".join(describe_check(c) for c in p.checks)
    soft = ""
    if p.prefer:
        soft = " prefer:" + "".join("+" if s else "-" for s in v.soft_ok)
    mark = "ok  " if v.ok else "FAIL"
    star = "*" if p.core else " "
    return (
        f"{mark} {star}{p.id:<16} {p.game} m{p.move_no:<2} {'W' if p.side == 0 else 'B'} "
        f"{format_move(o.move):<5} eval={ev:<7} {v.seconds:5.1f}s  {checks}{soft}{extra}"
    )


def report(bot_name: str, seconds: float, verdicts: list[Verdict], with_lines: bool = True) -> str:
    s = summary(verdicts)
    lines = [f"bot={bot_name} time={seconds:g}s positions={len(verdicts)} (* = core: v4 >= +3)"]
    if with_lines:
        lines += [line(v) for v in verdicts]
    lines += [
        "",
        f"correct, all positions:      {fmt_share(s['all'])}   (goal >= {PASS_SHARE:.0%})",
        f"correct, core positions:     {fmt_share(s['core'])}",
        f"  sign of the evaluation:    {fmt_share(s['sign'])}",
        f"  move (forbid/want/cage):   {fmt_share(s['move'])}",
        f"  no shuffling:              {fmt_share(s['shuffle'])}",
    ]
    soft = [ok for v in verdicts for ok in v.soft_ok]
    lines.append(f"preferred moves matched:     {fmt_share((sum(soft), len(soft)))}")
    return "\n".join(lines)


def dump(path: Path, bot_name: str, seconds: float, verdicts: list[Verdict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bot": bot_name,
        "time": seconds,
        "summary": summary(verdicts),
        "positions": [
            {
                "id": v.pos.id,
                "ok": v.ok,
                "move": format_move(v.out.move),
                "eval": v.out.score,
                "depth": v.out.depth,
                "own_moves": v.out.own_moves,
                "repeats": v.out.repeats,
                "prefer_ok": v.soft_ok,
                "seconds": round(v.seconds, 2),
            }
            for v in verdicts
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.positions.score", description=__doc__)
    ap.add_argument("--bot", default="v4", help="name from quoridor.bots.BOTS (default v4)")
    ap.add_argument("--time", type=float, default=3.0, help="seconds per move (default 3)")
    ap.add_argument("--only", help="comma-separated position ids")
    ap.add_argument("--procs", type=int, default=1, help="processes (timing noise grows)")
    ap.add_argument("--out", type=Path, help="result JSON (default arena_results/positions/...)")
    ap.add_argument("--no-dump", action="store_true", help="do not write the result JSON")
    args = ap.parse_args(argv)
    if args.bot not in BOTS:
        ap.error(f"unknown bot {args.bot!r}; known: {', '.join(sorted(BOTS))}")
    if args.time <= 0:
        ap.error("--time must be positive")
    positions = load_positions()
    if args.only:
        want = set(args.only.split(","))
        positions = [p for p in positions if p.id in want]
        if len(positions) != len(want):
            ap.error("unknown id in --only")
    verdicts = run_all(
        args.bot, args.time, positions, args.procs, lambda v: print(line(v), flush=True)
    )
    print()
    print(report(args.bot, args.time, verdicts).split("\n", 1 + len(verdicts))[-1])
    if not args.no_dump:
        out = args.out or OUT_DIR / f"{args.bot}-{args.time:g}s.json"
        dump(out, args.bot, args.time, verdicts)
        print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
