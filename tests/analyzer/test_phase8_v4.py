"""Phase 8 (bot v4) criteria as tests, test-engineer (reports 0063, 0068).

Criteria in the wording of ROADMAP (revised, report 0068): game02 move 20 - f2 is not the
top-1; game03 move 16 - a protective wall is in the top-3 and the eval is stable within
+-1 step between 4 and 20 s. The OLD, unmet criteria stay xfail(strict=False) with the
reason (they belong to the backlog item "evaluation of the endgame with walls in stock",
ROADMAP backlog, phase 9); they must not be tuned to pass.
"""

from pathlib import Path

import pytest

from quoridor.analyzer import engine
from quoridor.bots import BOTS, V4_SETTINGS, make_bot
from quoridor.cli import build_parser

FIXTURES = Path(__file__).parents[1] / "fixtures/games"


def load(name: str) -> list[str]:
    text = (FIXTURES / name).read_text("utf-8")
    return [t for line in text.splitlines() if not line.startswith("#") for t in line.split()]


GAME01, GAME02, GAME03 = load("game01.txt"), load("game02.txt"), load("game03.txt")


def white_move(game: list[str], n: int) -> list[str]:
    """Moves played before white's n-th move (1-based)."""
    return game[: 2 * (n - 1)]


# ---------- (a) v4 is the default bot of the cli and the analyzer ----------


def test_cli_default_bot_is_v4_and_registered():
    assert build_parser().parse_args([]).bot == "v4"
    assert {"v4", "v4-clock", "v4-0.2s"} <= set(BOTS)


def test_v4_bots_use_the_v4_settings():
    for name in ("v4", "v4-clock", "v4-0.2s"):
        s = make_bot(name, 0)._searcher
        assert s.v4 == 1 and s.pvs and s.lmr == 3 and s.delay_slack == 2, name
        assert s.threat == V4_SETTINGS["threat"] and s.root_defense == 3, name
        assert s.wall_weight == V4_SETTINGS["wall_weight"], name


def test_v3_is_not_v4():
    s = make_bot("v3", 0)._searcher
    assert not s.v4 and not s.pvs and s.delay_slack == 0 and s.threat == 0


def test_analyzer_searcher_is_v4():
    s = engine._CancellableSearcher(lambda: False)
    assert s.v4 == 1 and s.pvs and s.lmr == 3 and s.delay_slack == 2
    assert s.threat == V4_SETTINGS["threat"] and s.root_defense == V4_SETTINGS["root_defense"]
    assert s.wall_weight == V4_SETTINGS["wall_weight"]


def test_analyzer_and_cli_bot_settings_agree():
    """The analyzer must hint with the same bot that the cli plays with by default."""
    a = engine._CancellableSearcher(lambda: False)
    b = make_bot("v4", 0)._searcher
    for attr in ("v4", "pvs", "lmr", "delay_slack", "threat", "root_defense", "wall_weight"):
        assert getattr(a, attr) == getattr(b, attr), attr


# ---------- (b) analyzer at 3 s reaches the SearchBot depth (slow) ----------

DEPTH_POSITIONS = [
    ("game01.txt", 10),
    ("game01.txt", 24),
    ("game02.txt", 12),
    ("game02.txt", 30),
    ("game03.txt", 8),
    ("game03.txt", 22),
    ("game03.txt", 40),
]


@pytest.mark.slow
@pytest.mark.parametrize("name,n", DEPTH_POSITIONS)
def test_analyzer_depth_is_searchbot_depth_at_3s(name, n):
    """Depth of the hint (3 s) >= depth of SearchBot v4 (3 s per move) - 1 (clock noise at an
    iteration edge); the hint is not slower than 1.1 x budget + slack."""
    moves = load(name)[:n]
    res = engine.analyze(moves, 3.0)
    bot = make_bot("v4", 0)
    bot.depth, bot.move_time = None, 3.0
    bot.choose_move(engine.replay(moves))
    assert res["depth"] >= bot.last.depth - 1, (name, n, res["depth"], bot.last.depth)
    assert res["elapsed"] <= 3.0 * 1.1 + 0.6, res["elapsed"]


# ---------- (c) game02 move 20 ----------


@pytest.mark.slow
def test_game02_move_20_top1_is_not_f2():
    """Phase 8 criterion (ROADMAP wording): at move 20 the top-1 is not f2."""
    res = engine.analyze(white_move(GAME02, 20), engine.DEFAULT_TIME)
    assert res["top"][0]["name"] != "f2", res["top"]


@pytest.mark.slow
@pytest.mark.xfail(
    reason=(
        "old criterion (f2 not in the top-2), backlog item 'evaluation of the endgame with "
        "walls in stock' (ROADMAP, phase 9). Report 0062: f2 is 2nd in the top with the best "
        "move's score (+4.25): the main search (depth 17) cannot tell the root moves apart, "
        "the refutation 20...f2v shows from depth ~20"
    ),
    strict=False,
)
def test_game02_move_20_f2_is_not_in_the_top_2():
    res = engine.analyze(white_move(GAME02, 20), 3.0)
    assert "f2" not in [t["name"] for t in res["top"][:2]], res["top"]


# ---------- (d) game03 moves 11-16: a wall against the fence (3rd row) ----------


@pytest.mark.slow
@pytest.mark.xfail(
    reason=(
        "old criterion (top-1 is a wall on moves 11-16), backlog item 'evaluation of the "
        "endgame with walls in stock' (ROADMAP, phase 9). Report 0062: best move is a pawn "
        "move on moves 11-13, 15, 16 (wall 2nd-3rd); the deep search (v3, 60 s, depth 31) "
        "agrees: the white mistake is earlier, a wall is not better there. Move 14 gives "
        "d7h, a wall on black's side, not a reply to the fence"
    ),
    strict=False,
)
@pytest.mark.parametrize("n", [11, 12, 13, 14, 15, 16])
def test_game03_moves_11_16_top1_is_a_wall(n):
    res = engine.analyze(white_move(GAME03, n), 3.0)
    assert res["top"][0]["k"] == "w", (n, res["top"][:3])


@pytest.mark.slow
def test_game03_move_16_a_wall_reply_is_in_the_top3():
    """Phase 8 criterion: at move 16 a protective wall is in the top-3 (reports 0062, 0068).

    The walls seen there are g3v and g2h; both are replies to the fence on the 3rd row
    (g3v closes the g-h file in front of the fence, g2h the row 2/1 gap).
    """
    res = engine.analyze(white_move(GAME03, 16), engine.DEFAULT_TIME)
    walls = [t["name"] for t in res["top"] if t["k"] == "w"]
    assert walls, res["top"]
    assert set(walls) & {"g3v", "g2h", "g3h", "f3v", "h3v"}, res["top"]


@pytest.mark.slow
def test_game03_move_16_eval_is_stable_between_4_and_20_s():
    moves = white_move(GAME03, 16)
    low = engine.analyze(moves, 4.0)["eval"]  # ROADMAP: stable within +-1 step, 4 vs 20 s
    high = engine.analyze(moves, 20.0)["eval"]
    assert abs(low - high) <= 100, (low, high)  # within one step of the path difference


@pytest.mark.slow
@pytest.mark.xfail(
    reason="old criterion (eval < 0), moved to the backlog item 'evaluation of the endgame "
    "with walls in stock' (ROADMAP, phase 9): the eval is stable but wrong, +4.75 at 4 s and "
    "20 s, real ~ -9 (v3, 30 s, depth 33); the sign flips only near ply 33 (report 0062)",
    strict=False,
)
def test_game03_move_16_eval_is_close_to_the_real_one():
    res = engine.analyze(white_move(GAME03, 16), 20.0)
    assert res["eval"] < 0, res["eval"]
