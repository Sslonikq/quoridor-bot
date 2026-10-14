"""Phase 6 (0032 nit 6): the frozen v1 baseline (quoridor/frozen/search_v1.py).

* "v1-frozen" is in the registry and plays legal moves;
* at fixed depth it computes the same scores as the live search with the v1 settings
  (as long as eval/move generation of v1 are unchanged; when they change on purpose,
  this test documents it and is updated together with a new frozen snapshot);
* the frozen module does not depend on modules that are expected to evolve.
"""

import ast
from pathlib import Path

import pytest

from quoridor import bots
from quoridor.arena import run_match
from quoridor.bots import V1_DEPTH, make_bot
from quoridor.frozen import search_v1
from quoridor.rules import is_legal_move
from quoridor.search import Searcher
from tests.search.test_search_speed import positions

FROZEN = Path(search_v1.__file__)


def test_registry_has_frozen_baseline():
    assert "v1-frozen" in bots.BOTS
    bot = make_bot("v1-frozen", 0)
    assert bot.depth == V1_DEPTH
    assert isinstance(bot._searcher, search_v1.Searcher)


def test_frozen_imports_only_rules_level_modules():
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


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_frozen_equals_live_v1_scores(depth):
    ps = positions(range(60, 64), every=5, max_plies=70, wall_bias=0.6)
    assert len(ps) > 30
    for s in ps:
        a = search_v1.Searcher(0).search(s, max_depth=depth)
        b = Searcher(0).search(s, max_depth=depth)
        assert a.score == b.score, s
        assert a.nodes == b.nodes, s  # same ordering: phase-6 cache bounds change no node


def test_frozen_bot_plays_legal_moves_in_a_match():
    m = run_match(2, "v1-frozen", "greedy", seed=3)
    assert len(m.games) == 2


def test_frozen_moves_are_legal():
    for s in positions([65], every=4, max_plies=40):
        assert is_legal_move(s, make_bot("v1-frozen", 1).choose_move(s))
