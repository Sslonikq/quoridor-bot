"""The "real positions" set (phase 9, step 2): the data, the loader, the checks and the metric.

Fast tests need no search (stub bots). The slow one runs the current v4 on the whole set at
3 s per move and expects the phase-9 goal (>= 80%); it is xfail until the bot gets there
(baseline: reports/0074), and it XPASSes (does not fail) once it does.
"""

from __future__ import annotations

import json

import pytest

from quoridor.bots import SearchBot
from quoridor.notation import format_move, parse_move
from quoridor.rules import pawn_moves
from quoridor.state import Move
from tests.positions import loader, score
from tests.positions.loader import PositionsError, load_positions, state_at

POSITIONS = load_positions()
BY_ID = {p.id: p for p in POSITIONS}


def outcome(move: str, value: float | None = None, repeats: int | None = None) -> score.Outcome:
    return score.Outcome(parse_move(move), value, repeats=repeats)


# ---- data and loader ----------------------------------------------------------------------


def test_data_loads_and_matches_the_game_fixtures():
    assert len(POSITIONS) >= 15
    for p in POSITIONS:
        assert p.played == loader.game_moves(p.game)[p.ply], p.id
        assert state_at(p.game, p.ply).turn == p.side, p.id
        assert p.why and p.comment, p.id


def test_every_game_02_to_07_is_present():
    assert {p.game for p in POSITIONS} == {f"game0{i}" for i in range(2, 8)}


def test_game07_moves_24_to_30_and_the_shuffle_are_in_the_set():
    """The mandatory part of the step: game07 24-30 (sign or cage) and the shuffle 30-44."""
    for n in range(24, 31):
        p = BY_ID[f"g07-m{n}"]
        assert (p.game, p.move_no, p.side) == ("game07", n, 0)
        (c,) = p.checks
        assert c["type"] == "any_of"
        assert {s["type"] for s in c["checks"]} == {"eval_max", "avoid_cage"}
        assert c["checks"][0]["value"] == 0
        assert c["checks"][1]["walls"] == ["c8h", "b7v", "e6h", "g8h", "h7v"]
    shuffles = [p for p in POSITIONS if p.game == "game07" and p.category == "shuffle"]
    assert shuffles
    assert min(p.move_no for p in shuffles) == 30
    assert max(p.move_no + p.checks[0]["own_moves"] - 1 for p in shuffles) >= 44


def test_core_positions_are_a_large_part_of_the_set():
    assert len([p for p in POSITIONS if p.core]) >= 8
    assert {p.category for p in POSITIONS} == {"sign", "move", "shuffle"}


def test_loader_rejects_a_wrong_side_a_wrong_move_and_a_bad_check(tmp_path):
    base = json.loads(loader.DATA.read_text("utf-8"))["positions"][0]

    def load(**patch):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"positions": [{**base, **patch}]}), "utf-8")
        return load_positions(path)

    assert load()[0].id == base["id"]
    with pytest.raises(PositionsError, match="side"):
        load(side="black")
    with pytest.raises(PositionsError, match="played"):
        load(played="a1")
    with pytest.raises(PositionsError, match="bad check"):
        load(checks=[{"type": "nope"}])
    with pytest.raises(PositionsError, match="past the end"):
        load(ply=500)


def test_loader_rejects_duplicate_ids(tmp_path):
    raw = json.loads(loader.DATA.read_text("utf-8"))["positions"][0]
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"positions": [raw, raw]}), "utf-8")
    with pytest.raises(PositionsError, match="duplicate"):
        load_positions(path)


# ---- the checks ---------------------------------------------------------------------------


def test_eval_max_needs_a_score():
    p = BY_ID["g03-m17"]
    assert score.run_check({"type": "eval_max", "value": 0}, p, outcome("h2", -1.5))
    assert score.run_check({"type": "eval_max", "value": 0}, p, outcome("h2", 0.0))
    assert not score.run_check({"type": "eval_max", "value": 0}, p, outcome("h2", 0.01))
    assert not score.run_check({"type": "eval_max", "value": 0}, p, outcome("h2", None))


def test_move_and_kind_checks():
    p = BY_ID["g06-p35"]
    wall, pawn = outcome("b5h"), outcome("f4")
    assert not score.run_check({"type": "forbid_kind", "kind": "wall"}, p, wall)
    assert score.run_check({"type": "forbid_kind", "kind": "wall"}, p, pawn)
    assert score.run_check({"type": "want_kind", "kind": "wall"}, p, wall)
    assert score.run_check({"type": "forbid_moves", "moves": ["b5h"]}, p, pawn)
    assert not score.run_check({"type": "forbid_moves", "moves": ["b5h"]}, p, wall)
    assert score.run_check({"type": "want_moves", "moves": ["f4", "g5"]}, p, pawn)


def test_any_of_and_the_pass_rule():
    p = BY_ID["g07-m24"]
    assert score.judge(p, outcome("d6", 5.0))[0]  # d6 is outside the cage: dist 7 < 8
    assert score.judge(p, outcome("c7", -1.0))[0]  # into the cage, but the eval is not above 0
    assert not score.judge(p, outcome("e7", 5.0))[0]  # into the cage with a plus
    assert score.judge(p, outcome("d9v", 5.0))[0]  # a wall does not walk into the cage


def test_cage_distances_of_game07():
    """Under the five walls the cells of the cage cost 8+ steps, d6 and the rows below less."""
    p = BY_ID["g07-m24"]
    state = state_at(p.game, p.ply)
    walls = ["c8h", "b7v", "e6h", "g8h", "h7v"]
    for cell, lo, hi in (("c7", 8, 99), ("d7", 8, 99), ("e7", 8, 99), ("d6", 0, 7), ("a7", 0, 7)):
        m = parse_move(cell)
        d = score.cage_dist(state, p.side, (m.col, m.row), walls)
        assert d is not None and lo <= d <= hi, (cell, d)


def test_cage_dist_does_not_change_the_state():
    p = BY_ID["g07-m24"]
    state = state_at(p.game, p.ply)
    before = (state.pawns, state.h_walls, state.v_walls)
    score.cage_dist(state, p.side, (3, 5), ["c8h"])
    assert (state.pawns, state.h_walls, state.v_walls) == before


# ---- the simulation of own moves (no_repeat) ------------------------------------------------


class Shuffler:
    """Steps back to the previous cell whenever it can: the shuffle the criterion must catch."""

    def __init__(self) -> None:
        self.prev: tuple[int, int] | None = None

    def choose_move(self, state) -> Move:
        me = state.pawns[state.turn]
        moves = pawn_moves(state)
        back = [m for m in moves if (m.col, m.row) == self.prev]
        self.prev = me
        return back[0] if back else moves[0]


class Walker:
    """Always the shortest-path step: never returns to a cell."""

    def choose_move(self, state) -> Move:
        return score.greedy_pawn(state)


def test_simulation_catches_a_shuffler_and_accepts_a_walker():
    p = BY_ID["g07-shuffle-m30"]
    played, repeats = score.simulate_own_moves(Shuffler(), p, 8)
    assert len(played) == 8 and repeats >= 4
    played, repeats = score.simulate_own_moves(Walker(), p, 8)
    assert len(played) == 8 and repeats == 0
    assert not score.run_check(p.checks[0], p, outcome("e7", None, repeats=2))
    assert score.run_check(p.checks[0], p, outcome("e7", None, repeats=0))


def test_simulation_wall_moves_break_a_repeat():
    """A wall changes (cell, walls left): returning to the cell after a wall is not a repeat."""
    p = BY_ID["g07-shuffle-m30"]
    state = state_at(p.game, p.ply)
    here = state.pawns[p.side]

    class WallBetween:
        def __init__(self) -> None:
            self.k = 0

        def choose_move(self, st) -> Move:
            self.k += 1
            if self.k == 1:
                return pawn_moves(st)[0]
            if self.k == 2:
                return parse_move("a2h")
            return next(m for m in pawn_moves(st) if (m.col, m.row) == here)

    played, repeats = score.simulate_own_moves(WallBetween(), p, 3)
    assert played[1] == "a2h" and repeats == 0


def test_simulation_stops_when_the_game_is_over():
    p = BY_ID["g03-shuffle-m21"]
    played, _ = score.simulate_own_moves(Walker(), p, 60)
    assert 0 < len(played) < 60


# ---- the runner and the CLI ---------------------------------------------------------------


def test_make_scored_bot_overrides_depth_and_clock():
    b = score.make_scored_bot("v4", 1.5)
    assert isinstance(b, SearchBot) and b.depth is None and b.move_time == 1.5
    c = score.make_scored_bot("v4-clock", 0.7)
    assert isinstance(c, SearchBot) and c.game_time is None and c.move_time == 0.7
    m = score.make_scored_bot("mcts", 0.4)  # report 0076: MCTS on a move time too
    assert m.sims is None and m.game_time is None and m.move_time == 0.4


def test_bot_without_a_score_fails_the_sign_checks_but_runs():
    ids = ["g03-m17", "g04-m11"]
    verdicts = score.run_all("random", 0.1, [BY_ID[i] for i in ids])
    assert [v.pos.id for v in verdicts] == ids
    assert not verdicts[0].ok and verdicts[0].out.score is None


def test_real_search_on_one_position_gives_a_score_and_a_summary():
    (v,) = score.run_all("v4", 0.3, [BY_ID["g04-m11"]])
    assert v.out.score is not None and v.out.depth
    s = score.summary([v])
    assert s["all"][1] == 1 and s["move"] == (int(v.ok), 1)
    text = score.report("v4", 0.3, [v])
    assert "g04-m11" in text and "correct, all positions" in text


def test_dump_writes_json(tmp_path):
    (v,) = score.run_all("random", 0.1, [BY_ID["g04-m11"]])
    path = tmp_path / "d" / "r.json"
    score.dump(path, "random", 0.1, [v])
    data = json.loads(path.read_text("utf-8"))
    assert data["bot"] == "random" and data["positions"][0]["id"] == "g04-m11"
    assert data["positions"][0]["move"] == format_move(v.out.move)


def test_cli_rejects_unknown_bot_and_ids(capsys):
    with pytest.raises(SystemExit):
        score.main(["--bot", "nope"])
    with pytest.raises(SystemExit):
        score.main(["--only", "zzz", "--no-dump"])
    with pytest.raises(SystemExit):
        score.main(["--time", "0"])


def test_results_dir_is_git_ignored():
    gi = (score.REPO / ".gitignore").read_text("utf-8").splitlines()
    assert "arena_results/" in gi and score.OUT_DIR.parent.name == "arena_results"


# ---- the metric for the current bot -------------------------------------------------------


@pytest.mark.slow
@pytest.mark.xfail(
    strict=False,
    reason="phase 9 goal: >= 80% of the real positions; baseline of v4 is below (reports/0074)",
)
def test_v4_handles_80_percent_of_the_real_positions():
    verdicts = score.run_all("v4", 3.0, POSITIONS)
    score.dump(score.OUT_DIR / "pytest-v4-3s.json", "v4", 3.0, verdicts)
    ok, n = score.summary(verdicts)["all"]
    assert ok / n >= score.PASS_SHARE, score.report("v4", 3.0, verdicts)
