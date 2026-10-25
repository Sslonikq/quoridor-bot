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


def test_parse_spec_v4_v5_and_mcts():
    """Report 0076: v4: / v5: (registry settings + overrides) and mcts: specs."""
    from quoridor.bots import V4_SETTINGS, V5_SETTINGS
    from quoridor.mcts import MCTSBot

    b = tune.parse_spec("v4:", 0.3)(0)
    assert isinstance(b, SearchBot) and b.move_time == 0.3
    assert b._searcher.reserve == V4_SETTINGS["reserve"] and b._searcher.combo == 0
    b = tune.parse_spec("v5:move_time=1.0", 0.3)(0)
    assert b.move_time == 1.0 and b._searcher.combo == V5_SETTINGS["combo"]
    b = tune.parse_spec("mcts:c_puct=2.0", 0.3)(0)
    assert isinstance(b, MCTSBot) and b.move_time == 0.3 and b._mcts.c_puct == 2.0
    b = tune.parse_spec("mcts:s_combo=40,s_wall_limit=7,c_puct=1.0", 0.3)(0)
    assert b._mcts.s.combo == 40 and b._mcts.s.wall_limit == 7 and b._mcts.c_puct == 1.0
    b = tune.parse_spec("mcts2:move_time=10.0", 0.3)(0)
    assert b.move_time == 10.0 and b._mcts.expand_visits == 2 and b._mcts.rep_penalty == 1.0
    b = tune.parse_spec("mcts:sims=50", 0.3)(0)
    assert b.sims == 50 and b.move_time is None
    assert tune.uses_default_move_time("mcts:") and not tune.uses_default_move_time("mcts:sims=5")
    with pytest.raises(ValueError):
        tune.parse_spec("mcts:nope=1", 0.1)


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


def test_gap_limit_follows_the_move_budget():
    """Review 0069 minor 3: --max-gap is checked against the per-move budget."""
    assert tune.gap_limit_for(0.2, None) == tune.MAX_GAP
    assert tune.gap_limit_for(20.0, None) == pytest.approx(60.0)
    assert tune.gap_limit_for(5.0, 12.0) == 12.0
    with pytest.raises(ValueError):
        tune.gap_limit_for(30.0, 30.0)
    budget = tune.move_budget_of
    assert budget(tune.parse_spec("search:move_time=7.5", 0.2)) == 7.5
    assert budget(tune.parse_spec("search:depth=2", 0.2)) == 0.0
    assert budget(tune.parse_spec("v4-clock", 0.2)) == tune.MAX_TIME
    assert budget(tune.parse_spec("greedy", 0.2)) == 0.0


def test_max_gap_not_above_move_time_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit):
        tune.main(["search:tt_bits=0", "greedy", "--move-time", "40", "--max-gap", "30"])
    assert "--max-gap" in capsys.readouterr().err
    # which specs take --move-time (the header names it only for them)
    assert not tune.uses_default_move_time("v4-3s")
    assert not tune.uses_default_move_time("search:move_time=1.0")
    assert tune.uses_default_move_time("search:tt_bits=18")
    assert tune.uses_default_move_time("frozen4:")


@pytest.mark.slow
def test_tune_header_names_own_time_limits(capsys):
    assert tune.main(["search:depth=1", "greedy", "--games", "2", "--procs", "1"]) == 0
    out = capsys.readouterr().out
    assert "own time limits of the bots" in out and "move_time 0.2" not in out
