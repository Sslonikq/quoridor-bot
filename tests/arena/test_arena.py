"""Arena: determinism, colour balance, move limit, statistics on stub bots, CLI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quoridor.arena import (
    BOTS,
    GameError,
    UnknownBotError,
    format_report,
    game_seed,
    main,
    make_bot,
    play_game,
    run_match,
    wilson_interval,
)
from quoridor.notation import parse_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move, pawn_moves, winner
from quoridor.state import BLACK, WHITE, GameState, PawnMove

ROOT = Path(__file__).resolve().parents[2]


class RunnerBot:
    """Stub: always the pawn move that minimises own shortest path (never builds walls)."""

    def __init__(self, seed=0):
        pass

    def choose_move(self, state):
        me = state.turn
        return min(
            pawn_moves(state),
            key=lambda m: shortest_path_length(apply_move(state, m), me),
        )


class IdlerBot:
    """Stub: always the pawn move that maximises own distance: never reaches the goal."""

    def __init__(self, seed=0):
        pass

    def choose_move(self, state):
        me = state.turn
        return max(
            pawn_moves(state),
            key=lambda m: shortest_path_length(apply_move(state, m), me),
        )


STUBS = {"runner": RunnerBot, "idler": IdlerBot}


def test_registry_has_random_and_greedy():
    assert {"random", "greedy"} <= set(BOTS)
    for name in ("random", "greedy"):
        assert hasattr(make_bot(name, 1), "choose_move")


def test_unknown_bot_name_is_clear_error():
    with pytest.raises(UnknownBotError) as e:
        make_bot("nope", 0)
    assert "nope" in str(e.value) and "greedy" in str(e.value) and "random" in str(e.value)
    with pytest.raises(UnknownBotError):
        run_match(2, "random", "nope")


def test_unknown_bot_cli(capsys):
    assert main(["--games", "1", "--a", "nope", "--b", "random"]) == 2
    cap = capsys.readouterr()
    assert "nope" in cap.err and "Traceback" not in cap.err and cap.out == ""


def test_determinism_same_seed():
    m1 = run_match(6, "random", "random", seed=7, max_moves=60)
    m2 = run_match(6, "random", "random", seed=7, max_moves=60)
    assert [g.moves for g in m1.games] == [g.moves for g in m2.games]
    assert [g.winner for g in m1.games] == [g.winner for g in m2.games]


def test_different_seed_differs():
    m1 = run_match(4, "random", "random", seed=1, max_moves=40)
    m2 = run_match(4, "random", "random", seed=2, max_moves=40)
    assert [g.moves for g in m1.games] != [g.moves for g in m2.games]


def test_game_seed_depends_on_base_and_index():
    seeds = {game_seed(b, i) for b in range(5) for i in range(50)}
    assert len(seeds) == 250


@pytest.mark.parametrize("n", [0, 1, 2, 7, 10])
def test_colour_balance(n):
    m = run_match(n, "runner", "idler", registry=STUBS, max_moves=10)
    assert m.as_white.games == (n + 1) // 2
    assert m.as_black.games == n // 2
    assert m.as_white.games - m.as_black.games == n % 2


def test_move_limit_gives_draw():
    m = run_match(4, "runner", "runner", registry=STUBS, max_moves=4)
    assert all(g.winner is None and g.plies == 4 for g in m.games)
    assert m.total.draws == 4 and m.total.wins == 0 and m.win_rate == 0.0


def test_move_limit_one_ply():
    m = run_match(2, "runner", "idler", registry=STUBS, max_moves=1)
    assert all(g.plies == 1 and g.winner is None for g in m.games)


def test_stats_on_stubs_runner_always_wins():
    m = run_match(6, "runner", "idler", registry=STUBS, max_moves=100)
    assert m.total.wins == 6 and m.total.losses == 0 and m.total.draws == 0
    assert m.as_white.wins == 3 and m.as_black.wins == 3
    assert m.win_rate == 1.0
    lo, hi = m.wilson
    assert hi == 1.0 and 0.5 < lo < 1.0
    for g in m.games:
        assert g.outcome_for_a == "W"
        assert g.winner == g.a_color
        assert g.moves_a + g.moves_b == g.plies == len(g.moves)
        assert g.time_a >= 0 and g.time_b >= 0
    assert m.avg_plies == sum(g.plies for g in m.games) / 6
    assert m.avg_move_time_a >= 0 and m.avg_move_time_b >= 0


def test_stats_swapped_sides_all_losses():
    m = run_match(5, "idler", "runner", registry=STUBS, max_moves=100)
    assert (m.total.wins, m.total.losses, m.total.draws) == (0, 5, 0)
    assert m.as_white.losses == 3 and m.as_black.losses == 2
    assert m.wilson[0] == 0.0


def test_wilson_known_values():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(100, 100)
    assert hi == 1.0 and lo == pytest.approx(0.9630, abs=1e-3)
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and 0 < hi < 0.4


def test_log_uses_notation_and_replays_legally():
    m = run_match(2, "random", "random", seed=3, max_moves=50)
    for g in m.games:
        state = GameState.initial()
        for text in g.moves:
            state = apply_move(state, parse_move(text))
        assert winner(state) == g.winner


def test_report_text_and_mirror_warning():
    m = run_match(3, "runner", "idler", registry=STUBS, max_moves=100)
    text = format_report(m)
    assert "W 3 / L 0 / D (лимит ходов) 0" in text and "Уилсон" in text and "нечётное" in text
    assert not m.mirror_deterministic
    g = run_match(4, "greedy", "greedy", max_moves=20)
    assert g.mirror_deterministic
    assert "ПРЕДУПРЕЖДЕНИЕ" in format_report(g)
    # games of the same colour repeat each other
    assert g.games[0].a_color == g.games[2].a_color == WHITE
    assert g.games[0].moves == g.games[2].moves
    assert g.games[1].a_color == g.games[3].a_color == BLACK
    assert g.games[1].moves == g.games[3].moves


def test_win_on_last_allowed_ply_is_win_not_draw():
    free = run_match(1, "runner", "idler", registry=STUBS, max_moves=200).games[0]
    assert free.winner == WHITE
    plies = free.plies
    exact = run_match(1, "runner", "idler", registry=STUBS, max_moves=plies).games[0]
    assert exact.winner == WHITE and exact.plies == plies
    short = run_match(1, "runner", "idler", registry=STUBS, max_moves=plies - 1).games[0]
    assert short.winner is None and short.plies == plies - 1


def test_on_game_callback_called_per_game_in_order():
    seen = []
    m = run_match(5, "runner", "idler", registry=STUBS, max_moves=50, on_game=seen.append)
    assert [g.index for g in seen] == [0, 1, 2, 3, 4]
    assert tuple(seen) == m.games


def test_progress_printed_and_quiet_disables(capsys):
    args = ["--games", "3", "--a", "random", "--b", "random", "--max-moves", "20"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert all(f"Партия {i}:" in out for i in (1, 2, 3))
    assert out.index("Партия 3:") < out.index("Итого для A")
    assert main([*args, "--quiet"]) == 0
    assert "Партия " not in capsys.readouterr().out


class BadAfterBot:
    """Stub: plays like the runner, but makes an illegal move (teleport) on its 3rd call."""

    def __init__(self, seed=0):
        self.calls = 0
        self.inner = RunnerBot()

    def choose_move(self, state):
        self.calls += 1
        if self.calls == 3:
            return PawnMove(0, 8)
        return self.inner.choose_move(state)


def test_illegal_move_raises_game_error_with_context():
    registry = {"runner": RunnerBot, "bad": BadAfterBot}
    with pytest.raises(GameError) as e:
        run_match(1, "runner", "bad", seed=4, registry=registry, max_moves=100)
    err = e.value
    assert err.index == 0 and err.seed == game_seed(4, 0)
    assert err.a_color == WHITE and err.side == BLACK
    assert len(err.moves) == 5  # w b w b w played, then black's 3rd call is illegal
    assert isinstance(err.cause, ValueError)
    text = str(err)
    assert "партия 1" in text and str(game_seed(4, 0)) in text and "бота B" in text
    assert " ".join(err.moves) in text


def test_game_error_has_index_seed_and_colour_of_later_game():
    with pytest.raises(GameError) as e:
        play_game(RunnerBot(), BadAfterBot(), BLACK, 50, index=7, seed=99)
    assert e.value.index == 7 and e.value.seed == 99 and e.value.a_color == BLACK
    assert e.value.side == WHITE and len(e.value.moves) == 4  # B is white: its 3rd call is ply 5


def test_main_illegal_move_prints_context_and_returns_1(capsys, monkeypatch):
    import quoridor.arena as arena

    monkeypatch.setitem(arena.BOTS, "bad", BadAfterBot)
    monkeypatch.setitem(arena.BOTS, "runner", RunnerBot)
    argv = ["--games", "2", "--a", "runner", "--b", "bad", "--seed", "3", "--max-moves", "50"]
    code = main(argv)
    cap = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in cap.err
    assert f"seed партии {game_seed(3, 0)}" in cap.err and "партия 1" in cap.err
    assert "Уже сыграно" in cap.err and "Итого для A" not in cap.out


def test_main_keyboard_interrupt_returns_130(capsys, monkeypatch):
    import quoridor.arena as arena

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(arena, "run_match", boom)
    assert main(["--games", "1", "--a", "random", "--b", "random"]) == 130
    assert "Traceback" not in capsys.readouterr().err


def test_score_counts_draws_as_half():
    m = run_match(4, "runner", "runner", registry=STUBS, max_moves=4)
    assert m.score == 0.5 and m.win_rate == 0.0
    assert "Счёт A (W + D/2)/n: 50.0%" in format_report(m)
    assert run_match(2, "runner", "idler", registry=STUBS, max_moves=100).score == 1.0
    assert run_match(0, "runner", "idler", registry=STUBS).score == 0.0


def test_report_wording_games_count():
    text = format_report(run_match(2, "runner", "idler", registry=STUBS, max_moves=100))
    assert "партий: 2" in text and "2 партий" not in text


def test_registry_and_limit_single_source():
    import quoridor.arena as arena
    import quoridor.bots as bots
    import quoridor.cli as cli

    assert arena.BOTS is bots.BOTS and cli.BOTS is bots.BOTS
    assert arena.DEFAULT_MAX_MOVES == cli.DEFAULT_MAX_MOVES == bots.DEFAULT_MAX_MOVES
    assert arena.make_bot is bots.make_bot and cli.make_bot is bots.make_bot


def test_invalid_args():
    with pytest.raises(ValueError):
        run_match(1, "random", "random", max_moves=0)
    with pytest.raises(ValueError):
        run_match(-1, "random", "random")


def test_main_in_process(capsys):
    assert (
        main(
            [
                "--games",
                "3",
                "--a",
                "random",
                "--b",
                "random",
                "--seed",
                "5",
                "--quiet",
                "--max-moves",
                "30",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Партия " not in out and "Итого для A" in out and "нечётное" in out


def run_arena(args, timeout=300):
    env = {**os.environ, "PYTHONIOENCODING": "cp1251"}
    env.pop("PYTHONUTF8", None)
    p = subprocess.run(
        [sys.executable, "-m", "quoridor.arena", *args],
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=timeout,
    )
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")


@pytest.mark.slow
def test_subprocess_small_match():
    code, out, err = run_arena(["--games", "4", "--a", "greedy", "--b", "random", "--seed", "1"])
    assert code == 0 and err == ""
    assert "Партия 4:" in out and "Итого для A" in out and "Уилсон" in out
    assert "ПРЕДУПРЕЖДЕНИЕ" not in out


@pytest.mark.slow
def test_subprocess_deterministic_and_mirror_warning():
    a = run_arena(["--games", "2", "--a", "greedy", "--b", "greedy", "--quiet"])
    assert a[0] == 0 and "ПРЕДУПРЕЖДЕНИЕ" in a[1]
    r1 = run_arena(
        [
            "--games",
            "4",
            "--a",
            "random",
            "--b",
            "random",
            "--seed",
            "9",
            "--quiet",
            "--max-moves",
            "40",
        ]
    )
    r2 = run_arena(
        [
            "--games",
            "4",
            "--a",
            "random",
            "--b",
            "random",
            "--seed",
            "9",
            "--quiet",
            "--max-moves",
            "40",
        ]
    )
    strip = lambda s: [ln for ln in s.splitlines() if "время" not in ln.lower()]  # noqa: E731
    assert strip(r1[1]) == strip(r2[1])


@pytest.mark.slow
def test_subprocess_progress_lines_present():
    code, out, err = run_arena(["--games", "2", "--a", "greedy", "--b", "random"])
    assert code == 0 and "Партия 2:" in out


@pytest.mark.slow
def test_subprocess_stderr_is_utf8():
    code, out, err = run_arena(["--games", "2", "--a", "zzz", "--b", "random"])
    assert code == 2 and "неизвестный бот" in err


@pytest.mark.slow
def test_subprocess_unknown_bot():
    code, out, err = run_arena(["--games", "2", "--a", "zzz", "--b", "random"])
    assert code == 2 and "zzz" in err and "Traceback" not in err
