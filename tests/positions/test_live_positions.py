"""Live positions of phase 14 (games 08-12, reports 0123 and 0124): data, checks, mcts3.

Eight positions where the hint of mcts3 lost live games (live_positions.json, loader.LIVE_DATA).
They are kept apart from the phase-9 set (positions.json): that set has a 80% goal with a
margin of one position and must not move.

The metric is a fixed number of MCTS simulations, not a clock (report 0090): deterministic,
so one run is the median of any number of runs. Two budgets stand for the modes of the
analyzer: sims=400000 is about 2 s and sims=2000000 about 10 s on the reference machine
(about 230k simulations/s of mcts3, reports 0123/0124 numbers). Positions the current mcts3
fails are xfail(strict=False): they are the targets of the ai-engineer (phase 14, step 2) and
XPASS once fixed. The bot comes from QUORIDOR_LIVE_BOT (default mcts3), e.g. mcts4 later:

    QUORIDOR_LIVE_BOT=mcts4 pytest tests/positions/test_live_positions.py -q
    python -m tests.positions.score --bot mcts3 --set live --budget sims=400000
"""

from __future__ import annotations

import os

import pytest

from quoridor.notation import parse_move
from tests.positions import score
from tests.positions.loader import LIVE_DATA, game_moves, load_positions, state_at

LIVE = load_positions(LIVE_DATA)
BY_ID = {p.id: p for p in LIVE}
BOT = os.environ.get("QUORIDOR_LIVE_BOT", "mcts3")
BUDGETS = {"2s": "sims=400000", "10s": "sims=2000000"}

# What the current mcts3 gets wrong at BOTH budgets (run of 2026-11-03, report 0125).
FAILS = {
    "l-g09-p10": "0123/0124: топ-1 d6v с оценкой около −0.3…−0.9, ответ a7h вне дерева (паттерн 1)",
    "l-g08-p14": "0123: оценка +7 (мираж), ответ 8… d9v вне набора ходов корня (паттерн 1)",
    "l-g08-p15": "0123: d9v нет среди ходов корня чёрных, бот играет f5 (паттерн 1)",
    "l-g10-p19": "0123: d9 с оценкой около +9 (мираж), c7v замыкает коридор (паттерны 4, 5)",
    "l-g10-p22": "0123: b8h в корне есть, но выигрыш не найден (паттерн 2)",
    "l-g11-p16": "0123: h8v в корне есть, но выигрыш не найден (паттерн 2)",
}


def outcome(move: str, value: float | None = None) -> score.Outcome:
    return score.Outcome(parse_move(move), value)


# ---- data ---------------------------------------------------------------------------------


def test_eight_live_positions_from_games_08_to_11():
    assert [p.id for p in LIVE] == [
        "l-g09-p10",
        "l-g09-p11",
        "l-g08-p14",
        "l-g08-p15",
        "l-g10-p19",
        "l-g10-p22",
        "l-g11-p15",
        "l-g11-p16",
    ]
    assert {p.game for p in LIVE} == {"game08", "game09", "game10", "game11"}


def test_live_positions_match_the_game_fixtures_and_name_their_report():
    for p in LIVE:
        moves = game_moves(p.game)
        assert p.played == moves[p.ply], p.id
        assert state_at(p.game, p.ply).turn == p.side, p.id
        assert p.id == f"l-g{p.game[-2:]}-p{p.ply}", p.id
        assert p.why and "reports/012" in p.comment, p.id
        assert not p.core and p.checks, p.id


def test_game12_shares_the_two_game09_positions():
    """Report 0124: game12 repeats game09 for 14 plies, so plies 10 and 11 are one position."""
    for ply in (10, 11):
        assert game_moves("game12")[:ply] == game_moves("game09")[:ply]
        assert state_at("game12", ply) == state_at("game09", ply)


def test_criteria_of_the_live_positions():
    d6v = BY_ID["l-g09-p10"]
    assert score.judge(d6v, outcome("d5v", -1.5))[0]
    assert score.judge(d6v, outcome("d6v", -3.2))[0]  # d6v is fine if the bot sees a7h
    assert not score.judge(d6v, outcome("d6v", -0.6))[0]
    assert score.judge(d6v, outcome("d5v", -1.5))[1] == [True]  # d5v is preferred
    assert score.judge(BY_ID["l-g09-p11"], outcome("a7h", 5.0))[0]
    assert not score.judge(BY_ID["l-g09-p11"], outcome("e7h", 0.6))[0]
    mirage = BY_ID["l-g08-p14"]
    assert score.judge(mirage, outcome("f6", 2.9))[0]
    assert not score.judge(mirage, outcome("f6", 7.5))[0]
    assert score.judge(BY_ID["l-g08-p15"], outcome("d9v", -2.0))[0]
    assert not score.judge(BY_ID["l-g08-p15"], outcome("f5", -6.0))[0]
    d9 = BY_ID["l-g10-p19"]
    assert score.judge(d9, outcome("c8h", 9.0))[0]
    assert score.judge(d9, outcome("d9", 2.0))[0]
    assert not score.judge(d9, outcome("d9", 9.0))[0]
    assert score.judge(BY_ID["l-g10-p22"], outcome("b8h", 3.0))[0]
    assert not score.judge(BY_ID["l-g10-p22"], outcome("b6h", -3.9))[0]
    assert score.judge(BY_ID["l-g11-p15"], outcome("h8h", 0.3))[0]
    assert not score.judge(BY_ID["l-g11-p15"], outcome("f8h", 0.6))[0]
    assert score.judge(BY_ID["l-g11-p16"], outcome("h8v", 5.0))[0]
    assert not score.judge(BY_ID["l-g11-p16"], outcome("f4", 0.9))[0]


def test_every_xfail_reason_names_a_position_and_a_report():
    assert set(FAILS) <= set(BY_ID)
    assert all("0123" in r or "0124" in r for r in FAILS.values())


def test_score_cli_runs_the_live_set(capsys):
    args = ["--bot", "random", "--set", "live", "--no-dump", "--time", "0.1"]
    assert score.main(args) == 0
    text = capsys.readouterr().out
    assert "l-g09-p10" in text and "l-g11-p16" in text and "g02-m20" not in text


# ---- mcts3 on the live positions (slow) ---------------------------------------------------


def _cases():
    for pid in BY_ID:
        for label, budget in BUDGETS.items():
            marks = []
            if pid in FAILS:
                marks.append(pytest.mark.xfail(strict=False, reason=FAILS[pid]))
            yield pytest.param(pid, budget, marks=marks, id=f"{pid}-{label}")


@pytest.mark.slow
@pytest.mark.parametrize(("pid", "budget"), list(_cases()))
def test_live_position_with_a_fixed_budget(pid, budget):
    (v,) = score.run_all(BOT, 0.0, [BY_ID[pid]], budget=score.parse_budget(budget))
    assert v.ok, score.line(v)


@pytest.mark.slow
def test_a7h_is_found_with_a_reused_tree():
    """Report 0123 (B, ply 11): a7h at 10 s with the tree kept from the earlier positions was
    valued as e7h (-0.63), with a fresh tree 5 steps better. The bot analyses every position
    of the game in turn (the analyzer does the same), the budget is fixed, not a clock."""
    p = BY_ID["l-g09-p11"]
    bot = score.make_scored_bot(BOT, 0.0, 0, score.parse_budget(BUDGETS["2s"]))
    chosen = None
    for ply in range(p.ply + 1):
        chosen = bot.choose_move(state_at(p.game, ply))
    assert score.run_check(p.checks[0], p, score.Outcome(chosen, None)), chosen
