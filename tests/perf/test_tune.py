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
    res = [(2, 1, 0, 0.100, 0.200, 40.0, 1), (0, 0, 1, 0.500, 0.600, 80.0, 2)]
    w, lo, d, ta, tb, pl, dropped = tune.combine(res)
    assert (w, lo, d, dropped) == (2, 1, 1, 3)
    assert ta == pytest.approx(0.2)  # (3 * 0.1 + 0.5) / 4, unweighted would be 0.3
    assert tb == pytest.approx(0.3)
    assert pl == pytest.approx(50.0)
    assert tune.combine([]) == (0, 0, 0, 0.0, 0.0, 0.0, 0)
    # every game of a chunk dropped: it only adds to the counter
    assert tune.combine([(0, 0, 0, 0.0, 0.0, 0.0, 4)]) == (0, 0, 0, 0.0, 0.0, 0.0, 4)


def test_parse_spec_frozen_v4():
    from quoridor.frozen import search_v4

    b = tune.parse_spec("frozen4:", 0.3)(0)
    assert isinstance(b._searcher, search_v4.Searcher) and b.move_time == 0.3
    with pytest.raises(ValueError):
        tune.parse_spec("frozen4:tt_bits=18", 0.1)


def test_max_gap():
    assert tune.max_gap([]) == 0.0
    assert tune.max_gap([5.0]) == 0.0
    assert tune.max_gap([0.0, 1.0, 1.5, 40.0, 41.0]) == pytest.approx(38.5)


class _FakeClock:
    """time.time stand-in: +1 s per call, plus a jump of `jump` s at call number `at`."""

    def __init__(self, at: int | None, jump: float = 0.0) -> None:
        self.t = 0.0
        self.calls = 0
        self.at = at
        self.jump = jump

    def __call__(self) -> float:
        self.calls += 1
        if self.calls == self.at:
            self.t += self.jump
        self.t += 1.0
        return self.t


def test_sleep_guard_drops_only_the_game_with_a_clock_jump():
    """Task 4 of report 0066: a game with a wall-clock jump > 30 s between move stamps
    (machine asleep) is dropped and counted; the statistics are over the other games."""
    reg = {"A": tune.parse_spec("search:depth=1", 0.1), "B": tune.parse_spec("greedy", 0.1)}
    ref = tune.run_guarded(4, reg, 7, 300)
    assert ref[6] == 0 and ref[0] + ref[1] + ref[2] == 4
    # no jump: the same games, nothing dropped (1 s per stamp is below the limit)
    assert tune.run_guarded(4, reg, 7, 300, clock=_FakeClock(None))[:3] == ref[:3]
    # a 100 s jump at the 5th stamp (first game, 3rd move): exactly one game dropped
    res = tune.run_guarded(4, reg, 7, 300, clock=_FakeClock(5, 100.0))
    assert res[6] == 1 and res[0] + res[1] + res[2] == 3
    # a jump below the limit drops nothing
    res = tune.run_guarded(4, reg, 7, 300, clock=_FakeClock(5, 20.0))
    assert res[6] == 0
    # a lower limit (--max-gap) drops every game (all gaps are 1 s here)
    assert tune.run_guarded(2, reg, 7, 300, gap_limit=0.5, clock=_FakeClock(None))[6] == 2


@pytest.mark.slow
def test_tune_main_runs(capsys):
    assert tune.main(["search:depth=1", "greedy", "--games", "4", "--procs", "2"]) == 0
    out = capsys.readouterr().out
    assert "n=4" in out and "dropped 0 game(s)" in out
