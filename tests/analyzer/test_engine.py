"""Analyzer engine: replay from the initial position, position JSON, ranking."""

from pathlib import Path

import pytest

from quoridor.analyzer import engine
from quoridor.notation import format_move, parse_move
from quoridor.state import PawnMove


def names(items):
    return [m["name"] for m in items]


def assert_not_better_than_others(top, name):
    """`name` is not the strict top-1: some other move in the top scores at least as well.

    Robust to the order of TIED moves (h5v / f2 / f2h are all +6.25 at 5 s; which of them comes
    first depends on the depth reached). White to move, so a higher score is better.
    """
    mine = [t["score"] for t in top if t["name"] == name]
    others = [t["score"] for t in top if t["name"] != name]
    assert others, top
    assert not mine or max(others) >= max(mine), top


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


# ---------- phase 8, report 0053: one search for the best move, evaluate_move ----------

GAME02 = [
    t
    for line in (Path(__file__).parents[1] / "fixtures/games/game02.txt")
    .read_text("utf-8")
    .splitlines()
    if not line.startswith("#")
    for t in line.split()
]


def test_default_time_is_5s():
    assert engine.DEFAULT_TIME == 5.0 and 0.05 <= engine.ALT_SHARE <= 0.2


def test_top_entries_carry_depth_and_exact():
    res = engine.analyze(["e2", "e8"], 0.5)
    assert res["depth"] == res["top"][0]["depth"] >= 3 and res["top"][0]["exact"]
    assert all(isinstance(t["exact"], bool) and t["depth"] >= 1 for t in res["top"])


def test_evaluate_move_scores_a_wall_outside_the_top():
    res = engine.analyze(["e2", "e8"], 0.3)
    top = set(names(res["top"]))
    move = next(n for n in ("a9h", "h2v", "a2h") if n not in top)
    ev = engine.evaluate_move(["e2", "e8"], move, 0.3)
    assert ev["name"] == move and ev["k"] == "w" and not ev["cancelled"]
    assert ev["depth"] >= 2 and isinstance(ev["score"], int) and ev["why"]
    assert not ev["mate"]


def test_evaluate_move_rejects_illegal_moves_and_finished_games():
    with pytest.raises(engine.MoveListError) as e:
        engine.evaluate_move(["e2", "e8"], "e5", 0.2)
    assert e.value.number == 3
    with pytest.raises(engine.MoveListError):
        engine.evaluate_move(["e2"], 7, 0.2)
    win = ["e2", "e8", "e3", "e7", "e4", "e6", "e5", "d6", "e6", "d5", "e7", "d4", "e8", "d3"]
    assert engine.evaluate_move(win, "e9", 0.2)["mate"]
    with pytest.raises(engine.MoveListError):
        engine.evaluate_move([*win, "e9"], "d2", 0.2)


def test_evaluate_move_can_be_cancelled():
    assert engine.evaluate_move([], "e2", 5.0, cancelled=lambda: True)["cancelled"]


@pytest.mark.slow
@pytest.mark.parametrize("n", [36, 38])
def test_game02_moves_19_20_a_wall_is_in_the_top_and_not_clearly_worse(n):
    """game02, 19th/20th move of white (20. f2? f2v loses).

    Eased in report 0068: the old form "top-1 is a wall, not g2/f2" depended on the order of
    TIED moves (at 5 s depth 14-16 the top is h5v / f2 / f2h or f2h / g2 / h5v, all +6.25,
    sometimes g2 +6.75). What stays: a wall is in the top-3 and is not more than one step
    (100) worse than the best move, i.e. the analyzer does not hide the wall replies.
    """
    res = engine.analyze(GAME02[:n], engine.DEFAULT_TIME)
    walls = [t for t in res["top"] if t["k"] == "w"]
    assert walls, res["top"]
    assert walls[0]["score"] >= res["top"][0]["score"] - 100, res["top"]


@pytest.mark.slow
@pytest.mark.parametrize("n", [14, 16])
def test_game02_moves_8_9_a_wall_is_among_the_top(n):
    res = engine.analyze(GAME02[:n], 3.0)
    assert any(t["k"] == "w" for t in res["top"]), res["top"]


@pytest.mark.slow
def test_game02_move_20_top1_is_not_f2_and_f2_is_not_better_than_the_wall():
    """Phase 8 criterion (ROADMAP): at move 20 f2 is NOT the top-1.

    Eased in report 0068: the old extra check "a separate search of f2 is >= 1 step below the
    best" is dropped. f2 is scored 375..575 for 3 s (the refutation 20...f2v shows from depth
    ~20, the search reaches 14-17), the best is +6.25, so the gap is 0.5..2.5 steps and
    flips between runs. What stays: f2 is not top-1, and a separate search of f2 is not
    BETTER than the top-1 by more than half a step (50, the noise of two searches).
    """
    res = engine.analyze(GAME02[:38], engine.DEFAULT_TIME)
    assert_not_better_than_others(res["top"], "f2")
    ev = engine.evaluate_move(GAME02[:38], "f2", engine.DEFAULT_TIME)
    assert ev["score"] <= res["top"][0]["score"] + 50, (ev["score"], res["top"])


# ---------- phase 8, step 1 (review 0057): mate vs non-mate loss, parse case ----------


def _race(black):
    """White e7 to move (wins by e8, e9; one wall left), black on `black`, no walls."""
    from quoridor.state import GameState

    return GameState(
        pawns=((4, 6), black),
        h_walls=frozenset(),
        v_walls=frozenset(),
        walls_left=(1, 0),
        turn=0,
    )


def _shallow_non_mate(monkeypatch):
    calls = []

    def fake(state, mv, seconds, cancelled):
        calls.append(mv)
        return 300, 1  # what a too shallow separate search could report

    monkeypatch.setattr(engine, "_score_move", fake)
    return calls


def test_slower_mate_of_the_played_move_is_not_a_false_blunder(monkeypatch):
    # e8 mates in 3 plies; the irrelevant wall h3h mates in 5. A separate shallow search
    # of the wall saw no mate: before the fix loss = 12 - 3 steps -> "??".
    calls = _shallow_non_mate(monkeypatch)
    step = engine._review_position(_race((0, 8)), parse_move("h3h"), 1.0, lambda: False)
    assert step["top"][0]["name"] == "e8" and step["top"][0]["mate"]
    if not calls:
        pytest.skip("h3h was scored inside the top, the separate search was not used")
    assert step["played"]["mate"] and step["played"]["score"] > 0
    assert step["mark"] != "b" and step["loss"] < engine.MARK_BLUNDER


def test_played_move_that_walks_into_a_loss_stays_a_blunder(monkeypatch):
    # Black e3 also needs two steps: after the wall h3h black wins first.
    calls = _shallow_non_mate(monkeypatch)
    step = engine._review_position(_race((4, 2)), parse_move("h3h"), 1.0, lambda: False)
    assert step["top"][0]["name"] == "e8" and step["top"][0]["mate"]
    if not calls:
        pytest.skip("h3h was scored inside the top, the separate search was not used")
    assert step["played"]["mate"] and step["played"]["score"] < 0
    assert step["mark"] == "b" and step["loss"] == 2 * engine.REVIEW_MATE


def test_same_depth_scores_can_be_cancelled():
    s = _race((0, 8))
    assert (
        engine._same_depth_scores(s, parse_move("e8"), parse_move("h3h"), 1.0, lambda: True) is None
    )


def test_parse_game_text_accepts_upper_case_moves():
    assert engine.parse_game_text("E2 E8\nE3H") == ["e2", "e8", "e3h"]


# ---------- phase 8 (report 0061): analyzer depth = SearchBot depth at the same time ----------


@pytest.mark.slow
@pytest.mark.parametrize("n", [14, 26, 36])
def test_analyzer_reaches_the_searchbot_depth_at_3s(n):
    """The best move comes from the ordinary search on the whole budget, so the depth is
    the one SearchBot (v4, 3 s per move) reaches; +-1 for clock noise at an iteration edge."""
    from quoridor.bots import make_bot

    res = engine.analyze(GAME02[:n], 3.0)
    bot = make_bot("v4", 0)
    bot.depth, bot.move_time = None, 3.0
    bot.choose_move(engine.replay(GAME02[:n]))
    assert res["depth"] >= bot.last.depth - 1, (res["depth"], bot.last.depth)


def test_search_top_main_search_gets_the_whole_budget(monkeypatch):
    from quoridor.search import Searcher

    seen = []
    orig = Searcher._search

    def spy(self, state, max_depth, time_limit):
        seen.append(time_limit)
        return orig(self, state, max_depth, time_limit)

    monkeypatch.setattr(Searcher, "_search", spy)
    engine.analyze(["e2", "e8"], 0.5)
    assert seen == [0.5]


# ---- bots of the analyzer: v4 / v5 / MCTS (phase 9, step 1) ----


def test_mcts_analysis_top_moves_are_legal_and_sorted_by_visits():
    from quoridor.analyzer import engine

    res = engine.analyze(["e2", "e8", "e3", "e7"], 0.5, bot="mcts")
    legal = {m["name"] for m in res["position"]["legal"]}
    top = res["top"]
    assert 1 <= len(top) <= 3 and {t["name"] for t in top} <= legal
    assert len({t["name"] for t in top}) == len(top)
    assert top[0]["visits"] >= max(t["visits"] for t in top[1:] or top)
    assert res["bot"] == "mcts" and 0 < top[0]["visits"] <= res["sims"] + res["reused"] + 1
    assert res["depth"] == top[0]["depth"] >= 1
    # alternatives are never shown above the best move (side to move: white)
    assert all(t["score"] <= top[0]["score"] for t in top)


def test_mcts_button_uses_mcts2_with_the_endgame_solver():
    """Report 0084 (the user's decision): the analyzer's MCTS is mcts2 (MCTS2_OPTIONS:
    lazy expansion, strong repetition penalty, endgame solver at <= 2 walls)."""
    from quoridor.analyzer import engine
    from quoridor.bots import MCTS2_OPTIONS

    m = engine._new_mcts()
    assert m.expand_visits == MCTS2_OPTIONS["expand_visits"] == 2
    assert m.rep_penalty == MCTS2_OPTIONS["rep_penalty"] == 1.0
    assert m.solver == MCTS2_OPTIONS["solver"] == 2


def test_mcts_tree_is_kept_for_the_next_move_and_after_a_cancel():
    """Report 0083: the analysis of a child / grandchild of the last analysed position
    continues its tree, also when the last analysis was cancelled; others start anew."""
    import time

    from quoridor.analyzer import engine

    engine.reset_mcts_tree()
    first = engine.analyze(["e2", "e8"], 0.4, bot="mcts")
    assert first["reused"] == 0 and first["sims"] > 0
    best = first["top"][0]["name"]
    again = engine.analyze(["e2", "e8"], 0.2, bot="mcts")  # same position (a time switch)
    assert again["reused"] >= first["sims"]
    nxt = engine.analyze(["e2", "e8", best], 0.2, bot="mcts")  # the expected move came
    assert nxt["reused"] > 0
    t0 = time.perf_counter()
    late = lambda: time.perf_counter() - t0 > 0.2  # noqa: E731
    cut = engine.analyze(["e2", "e8", best], 5, late, None, "mcts")
    assert cut["cancelled"]
    after = engine.analyze(["e2", "e8", best], 0.2, bot="mcts")
    assert after["reused"] > nxt["reused"]  # the cancelled search added to the kept tree
    other = engine.analyze(["a2h"], 0.2, bot="mcts")  # not below the last root
    assert other["reused"] == 0
    engine.reset_mcts_tree()


def test_mcts_and_v5_win_on_the_spot_and_report_the_bot():
    from quoridor.analyzer import engine

    moves = ["e2", "e8", "e3", "e7", "e4", "e6", "e5", "d6", "e6", "d5", "e7", "d4", "e8", "d3"]
    for bot in ("mcts", "v5"):
        res = engine.analyze(moves, 0.3, bot=bot)
        assert res["bot"] == bot and res["top"][0]["name"] == "e9" and res["top"][0]["mate"]


def test_unknown_bot_raises_value_error():
    from quoridor.analyzer import engine

    with pytest.raises(ValueError):
        engine.analyze([], 0.2, bot="v3")


def test_mcts_cancel_stops_within_a_simulation_and_progress_is_reported():
    import time

    from quoridor.analyzer import engine

    seen = []
    t0 = time.perf_counter()
    res = engine.analyze(
        [],
        10,
        lambda: time.perf_counter() - t0 > 0.5,
        lambda d, n=0: seen.append((d, n)),
        "mcts",
    )
    assert res["cancelled"] and res["top"] == [] and time.perf_counter() - t0 < 2
    assert seen and seen[-1][1] > 0 and seen[-1][0] >= 1
