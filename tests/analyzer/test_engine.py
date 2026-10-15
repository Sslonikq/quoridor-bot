"""Analyzer engine: replay from the initial position, position JSON, ranking."""

from pathlib import Path

import pytest

from quoridor.analyzer import engine
from quoridor.notation import format_move, parse_move
from quoridor.state import PawnMove


def names(items):
    return [m["name"] for m in items]


GAME01 = [
    t
    for line in (Path(__file__).parents[1] / "fixtures/games/game01.txt")
    .read_text("utf-8")
    .splitlines()
    if not line.startswith("#")
    for t in line.split()
]


def test_initial_position():
    pos = engine.describe(engine.replay([]))
    assert names(pos["pawns"]) == ["e1", "e9"]
    assert pos["walls_left"] == [10, 10] and pos["turn"] == 0 and pos["winner"] is None
    assert pos["dist"] == [8, 8]
    assert {"e2", "d1", "f1", "e3h", "a2v"} <= set(names(pos["legal"]))
    assert "e1" not in names(pos["legal"]) and "a1h" not in names(
        pos["legal"]
    )  # anchor rank 1 is invalid


def test_game01_replays_to_the_recorded_final_position():
    pos = engine.describe(engine.replay(GAME01))
    assert names(pos["pawns"]) == ["c4", "i3"]
    assert len(pos["walls"]) == 16 and pos["walls_left"] == [4, 0]
    assert pos["turn"] == 0 and pos["winner"] is None


@pytest.mark.parametrize(
    ("moves", "number"),
    [(["e2", "e2"], 2), (["e5"], 1), (["z9"], 1), (["e2", "e8", "a1h"], 3), ([5], 1)],
)
def test_illegal_moves_are_rejected_with_their_number(moves, number):
    with pytest.raises(engine.MoveListError) as e:
        engine.replay(moves)
    assert e.value.number == number


def test_not_a_list_is_rejected():
    with pytest.raises(engine.MoveListError):
        engine.replay("e2 e8")


def test_wall_stock_and_overlap_rejected():
    with pytest.raises(engine.MoveListError):
        engine.replay(["e3h", "e7h", "e3h"])  # same wall twice
    with pytest.raises(engine.MoveListError):
        engine.replay(["e3h", "e7h", "d3h"])  # overlaps e3h by one column


def test_game_can_be_played_to_a_win():
    moves = [
        "e2",
        "e8",
        "e3",
        "e7",
        "e4",
        "e6",
        "e5",
        "d6",
        "e6",
        "d5",
        "e7",
        "d4",
        "e8",
        "d3",
        "e9",
    ]
    # white walks through after black steps aside; the last move wins
    pos = engine.describe(engine.replay(moves))
    assert pos["winner"] == 0 and pos["legal"] == []


def test_analyze_returns_three_ranked_moves_with_reasons():
    res = engine.analyze(["e2", "e8"], 0.3)
    assert not res["cancelled"] and len(res["top"]) == 3
    scores = [t["score"] for t in res["top"]]
    assert scores == sorted(scores, reverse=True)  # white to move: best first
    legal = set(names(res["position"]["legal"]))
    assert all(t["name"] in legal and t["why"] for t in res["top"])
    assert res["eval"] == scores[0] and res["depth"] >= 2  # from the root: child depth + 1


def test_analyze_black_to_move_scores_from_white_view():
    res = engine.analyze(["e2"], 0.3)
    scores = [t["score"] for t in res["top"]]
    assert scores == sorted(scores)  # black to move: best (lowest for white) first


def test_analyze_takes_a_win_on_the_spot():
    moves = ["e2", "e8", "e3", "e7", "e4", "e6", "e5", "d6", "e6", "d5", "e7", "d4", "e8", "d3"]
    res = engine.analyze(moves, 0.3)
    assert res["top"][0]["name"] == "e9" and "выигрывает" in res["top"][0]["why"]


def test_analyze_respects_the_time_budget():
    import time

    t0 = time.perf_counter()
    engine.analyze([], 0.5)
    assert time.perf_counter() - t0 < 1.5


def test_cancel_stops_early():
    res = engine.analyze([], 5.0, cancelled=lambda: True)
    assert res["cancelled"] and res["top"] == []


def test_finished_game_has_no_analysis():
    moves = [
        "e2",
        "e8",
        "e3",
        "e7",
        "e4",
        "e6",
        "e5",
        "d6",
        "e6",
        "d5",
        "e7",
        "d4",
        "e8",
        "d3",
        "e9",
    ]
    res = engine.analyze(moves, 0.3)
    assert res["top"] == [] and res["position"]["winner"] == 0


def test_win_on_the_spot_depth_is_one_and_mate_flag():
    moves = ["e2", "e8", "e3", "e7", "e4", "e6", "e5", "d6", "e6", "d5", "e7", "d4", "e8", "d3"]
    res = engine.analyze(moves, 0.3)
    assert res["depth"] == 1 and res["top"][0]["mate"] and res["eval_mate"]


def test_ordinary_position_is_not_mate():
    res = engine.analyze(["e2", "e8"], 0.3)
    assert not res["eval_mate"] and not any(t["mate"] for t in res["top"])


def _assert_geometry_matches_notation(m):
    parsed = parse_move(m["name"])
    if isinstance(parsed, PawnMove):
        assert m == {"name": m["name"], "k": "p", "c": parsed.col, "r": parsed.row}
    else:
        assert m == {"name": m["name"], "k": "w", "x": parsed.x, "y": parsed.y, "o": parsed.orient}
    assert format_move(parsed) == m["name"]


@pytest.mark.parametrize("n", [0, 1, 2, 10, 25, 40, len(GAME01)])
def test_geometry_equals_parse_move_on_all_legal_moves(n):
    pos = engine.describe(engine.replay(GAME01[:n]), GAME01[:n])
    for m in pos["legal"] + pos["walls"] + pos["pawns"]:
        _assert_geometry_matches_notation(m)
    if n:
        _assert_geometry_matches_notation(pos["last"])
        assert pos["last"]["name"] == GAME01[n - 1]
    else:
        assert pos["last"] is None


def test_geometry_equals_parse_move_on_all_128_anchors():
    anchors = [f"{c}{r}{o}" for c in "abcdefgh" for r in "23456789" for o in "hv"]
    assert len(anchors) == 128
    for name in anchors:
        _assert_geometry_matches_notation(engine.move_json(parse_move(name)))
    # on the empty board every anchor is a legal wall, so `legal` carries all of them
    legal = {m["name"]: m for m in engine.describe(engine.replay([]))["legal"]}
    assert set(anchors) <= set(legal)
    for name in anchors:
        assert legal[name] == engine.move_json(parse_move(name))


def test_top_moves_carry_geometry():
    res = engine.analyze(["e2", "e8"], 0.3)
    legal = {m["name"]: m for m in res["position"]["legal"]}
    for t in res["top"]:
        _assert_geometry_matches_notation({k: v for k, v in t.items() if k in legal[t["name"]]})
        assert all(t[k] == v for k, v in legal[t["name"]].items())
