"""Report 0066: the frozen v4 baseline (quoridor/frozen/search_v4.py).

* "v4-frozen", "v4-frozen-0.2s", "v4-frozen-3s", "v4-frozen-clock" are in the registry and
  use the frozen searcher;
* the frozen module depends only on rules-level modules (not on search.py / eval.py);
* at a fixed depth it computes the same moves, scores and node counts as the live search
  with the frozen settings (search_v4.SETTINGS: every option added after the freeze is
  off by default, so the live code with these settings is the old v4).
"""

import ast
from pathlib import Path

import pytest

from quoridor import bots
from quoridor.arena import run_match
from quoridor.bots import V4_DEPTH, make_bot
from quoridor.frozen import search_v4
from quoridor.rules import is_legal_move
from quoridor.search import Searcher
from tests.search.test_search_speed import positions

FROZEN = Path(search_v4.__file__)


@pytest.mark.parametrize(
    ("name", "depth", "move_time", "game_time"),
    [
        ("v4-frozen", V4_DEPTH, None, None),
        ("v4-frozen-0.2s", None, bots.FAST_MOVE_TIME, None),
        ("v4-frozen-3s", None, bots.MATCH_MOVE_TIME, None),
        ("v4-frozen-clock", None, None, bots.GAME_TIME),
    ],
)
def test_registry_has_frozen_v4(name, depth, move_time, game_time):
    bot = make_bot(name, 0)
    assert (bot.depth, bot.move_time, bot.game_time) == (depth, move_time, game_time)
    assert isinstance(bot._searcher, search_v4.Searcher)


def test_match_time_bots_in_registry():
    assert bots.MATCH_MOVE_TIME == 3.0
    for name in ("v3-3s", "v4-3s"):
        bot = make_bot(name, 0)
        assert bot.move_time == bots.MATCH_MOVE_TIME and bot.depth is None


def test_frozen_v4_imports_only_rules_level_modules():
    tree = ast.parse(FROZEN.read_text(encoding="utf-8"))
    mods = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("quoridor")
    }
    mods |= {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for a in n.names
        if a.name.startswith("quoridor")
    }
    assert mods <= {"quoridor", "quoridor.bitpaths", "quoridor.rules", "quoridor.state"}


def test_frozen_v4_settings_are_the_old_v4():
    """The settings of reports 0061/0062 (V4_SETTINGS before report 0066)."""
    assert search_v4.SETTINGS == {
        "tt_bits": 18,
        "wall_weight": 125,
        "v4": 1,
        "pvs": 1,
        "lmr": 3,
        "delay_slack": 2,
        "threat": 60,
        "root_defense": 3,
    }


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_frozen_v4_equals_live_search_with_frozen_settings(depth):
    ps = positions(range(60, 63), every=6, max_plies=70, wall_bias=0.6)
    assert len(ps) > 20
    for s in ps:
        a = search_v4.make_searcher(0).search(s, max_depth=depth)
        b = Searcher(0, **search_v4.SETTINGS).search(s, max_depth=depth)
        assert (a.move, a.score, a.nodes) == (b.move, b.score, b.nodes), s


def test_frozen_v4_bot_plays_a_match():
    m = run_match(2, "v4-frozen", "greedy", seed=3)
    assert len(m.games) == 2


def test_frozen_v4_moves_are_legal():
    for s in positions([65], every=6, max_plies=40):
        assert is_legal_move(s, make_bot("v4-frozen", 1).choose_move(s))
