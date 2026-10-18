"""Loader of the "real positions" set (phase 9, step 2): data in positions.json.

A position is a prefix of a recorded game (tests/fixtures/games/gameNN.txt), so it is stored
as (game, ply) and replayed through quoridor.analyzer.engine.replay: the data file does not
duplicate the moves and cannot drift from the fixtures. Everything is offline and pure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from quoridor.analyzer import engine
from quoridor.state import GameState

HERE = Path(__file__).parent
DATA = HERE / "positions.json"
GAMES = HERE.parent / "fixtures" / "games"

SIDES = {"white": 0, "black": 1}
CHECK_TYPES = {
    "eval_max",  # side's eval (steps) <= value
    "forbid_moves",  # the chosen move is not in `moves`
    "forbid_kind",  # the chosen move is not of the kind ("wall" | "pawn")
    "want_moves",  # the chosen move is in `moves`
    "want_kind",  # the chosen move is of the kind
    "avoid_cage",  # a pawn move does not land on a cell with dist >= min_dist once `walls` stand
    "no_repeat",  # over `own_moves` own moves the key (own cell, own walls left) repeats <= max
    "any_of",  # any of `checks` holds
}


class PositionsError(ValueError):
    """The data file is malformed or disagrees with the game fixtures."""


@dataclass(frozen=True)
class Position:
    id: str
    game: str  # "game07"
    ply: int  # plies played before the position (= index of the move in the game file)
    move_no: int  # 1-based move number of the side (ply // 2 + 1)
    side: int  # 0 = white, 1 = black (the side to move)
    played: str | None  # the move actually played (None: the game ends before)
    core: bool  # v4 gave this side >= +3 here (the selection of the step), else a plan-only entry
    v4_eval: float | None  # informational: v4 eval for `side` in steps, 3 s, see the report
    why: str  # what is known and why it is the right plan
    comment: str  # where in the fixture comments it comes from
    checks: tuple[dict, ...]  # hard checks: the position passes if all hold
    prefer: tuple[dict, ...] = field(default=())  # soft checks: reported, do not fail

    @property
    def kinds(self) -> frozenset[str]:
        out: set[str] = set()

        def walk(c: dict) -> None:
            out.add(c["type"])
            for sub in c.get("checks", ()):
                walk(sub)

        for c in self.checks:
            walk(c)
        return frozenset(out)

    @property
    def category(self) -> str:
        """Metric category: "shuffle", "sign" or "move"."""
        k = self.kinds
        if "no_repeat" in k:
            return "shuffle"
        if "eval_max" in k:
            return "sign"
        return "move"


def game_moves(game: str) -> list[str]:
    """Moves of tests/fixtures/games/<game>.txt (comment lines skipped)."""
    path = GAMES / f"{game}.txt"
    text = path.read_text("utf-8")
    return [t for ln in text.splitlines() if not ln.startswith("#") for t in ln.split()]


def state_at(game: str, ply: int) -> GameState:
    return engine.replay(game_moves(game)[:ply])


def _check_check(pid: str, c: object) -> None:
    if not isinstance(c, dict) or c.get("type") not in CHECK_TYPES:
        raise PositionsError(f"{pid}: bad check {c!r}")
    t = c["type"]
    if t == "any_of":
        subs = c.get("checks")
        if not isinstance(subs, list) or not subs:
            raise PositionsError(f"{pid}: any_of without checks")
        for s in subs:
            _check_check(pid, s)
    elif t == "eval_max":
        if not isinstance(c.get("value"), int | float):
            raise PositionsError(f"{pid}: eval_max needs a number")
    elif t in ("forbid_moves", "want_moves"):
        if not c.get("moves") or not all(isinstance(m, str) for m in c["moves"]):
            raise PositionsError(f"{pid}: {t} needs moves")
    elif t in ("forbid_kind", "want_kind"):
        if c.get("kind") not in ("wall", "pawn"):
            raise PositionsError(f"{pid}: {t} needs kind wall|pawn")
    elif t == "avoid_cage":
        if not c.get("walls") or not isinstance(c.get("min_dist"), int):
            raise PositionsError(f"{pid}: avoid_cage needs walls and min_dist")
    elif t == "no_repeat":
        if not isinstance(c.get("own_moves"), int) or c["own_moves"] < 2:
            raise PositionsError(f"{pid}: no_repeat needs own_moves >= 2")


def load_positions(path: Path = DATA, validate: bool = True) -> list[Position]:
    """Read the set. validate=True replays every game prefix and cross-checks the data."""
    raw = json.loads(path.read_text("utf-8"))
    out: list[Position] = []
    seen: set[str] = set()
    for p in raw["positions"]:
        pid = p["id"]
        if pid in seen:
            raise PositionsError(f"duplicate id {pid}")
        seen.add(pid)
        moves = game_moves(p["game"])
        ply = p["ply"]
        pos = Position(
            id=pid,
            game=p["game"],
            ply=ply,
            move_no=ply // 2 + 1,
            side=SIDES[p["side"]],
            played=moves[ply] if ply < len(moves) else None,
            core=bool(p["core"]),
            v4_eval=p.get("v4_eval"),
            why=p["why"],
            comment=p["comment"],
            checks=tuple(p["checks"]),
            prefer=tuple(p.get("prefer", ())),
        )
        if not pos.checks:
            raise PositionsError(f"{pid}: no checks")
        for c in (*pos.checks, *pos.prefer):
            _check_check(pid, c)
        if validate:
            if ply >= len(moves):
                raise PositionsError(f"{pid}: ply {ply} is past the end of {p['game']}")
            if state_at(p["game"], ply).turn != pos.side:
                raise PositionsError(f"{pid}: side {p['side']} is not to move at ply {ply}")
            if "played" in p and p["played"] != pos.played:
                raise PositionsError(f"{pid}: played {p['played']!r} != game {pos.played!r}")
        out.append(pos)
    return out
