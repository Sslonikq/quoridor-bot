"""The tuning stand (tests.perf.tune): spec parsing and a tiny parallel run."""

import pytest

from quoridor.bots import SearchBot
from tests.perf import tune


def test_parse_spec_registry_and_search():
    assert tune.parse_spec("greedy", 0.1)(0) is not None
    b = tune.parse_spec("search:wall_weight=125,tt_bits=18", 0.3)(0)
    assert isinstance(b, SearchBot) and b.move_time == 0.3
    b = tune.parse_spec("frozen:depth=2", 0.3)(0)
    assert b.depth == 2 and b.move_time is None


@pytest.mark.parametrize("bad", ["nope", "search:x", "other:depth=1"])
def test_parse_spec_rejects_garbage(bad):
    with pytest.raises(ValueError):
        tune.parse_spec(bad, 0.1)


@pytest.mark.slow
def test_tune_main_runs(capsys):
    assert tune.main(["search:depth=1", "greedy", "--games", "4", "--procs", "2"]) == 0
    assert "n=4" in capsys.readouterr().out
