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


@pytest.mark.parametrize(
    "bad", ["frozen:tt_bits=18", "frozen:wall_weight=125", "frozen:wall_limit=3", "search:foo=1"]
)
def test_parse_spec_rejects_ignored_or_unknown_settings(bad):
    """Review 0040, minor 2: frozen:tt_bits=18 must fail, not silently run as plain v1."""
    with pytest.raises(ValueError):
        tune.parse_spec(bad, 0.1)


def test_combine_weights_chunk_averages_by_games():
    """Review 0040, nit 9: chunks of 3 and 1 games -> weights 3/4 and 1/4."""
    res = [(2, 1, 0, 0.100, 0.200, 40.0), (0, 0, 1, 0.500, 0.600, 80.0)]
    w, lo, d, ta, tb, pl = tune.combine(res)
    assert (w, lo, d) == (2, 1, 1)
    assert ta == pytest.approx(0.2)  # (3 * 0.1 + 0.5) / 4, unweighted would be 0.3
    assert tb == pytest.approx(0.3)
    assert pl == pytest.approx(50.0)
    assert tune.combine([]) == (0, 0, 0, 0.0, 0.0, 0.0)


@pytest.mark.slow
def test_tune_main_runs(capsys):
    assert tune.main(["search:depth=1", "greedy", "--games", "4", "--procs", "2"]) == 0
    assert "n=4" in capsys.readouterr().out
