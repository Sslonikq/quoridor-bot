"""CLI end-to-end (subprocess) and input-robustness / full-game tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quoridor.bots import GreedyBot, RandomBot
from quoridor.cli import play
from quoridor.rules import apply_move, is_legal_move, is_terminal, winner
from quoridor.state import BLACK, WHITE, GameState
from tests.cli.test_cli import FirstLegalPawnBot, run
from tests.helpers import mk

ROOT = Path(__file__).resolve().parents[2]


def run_cli(args, stdin, timeout=60):
    env = {**os.environ, "PYTHONIOENCODING": "cp1251"}  # hostile locale: CLI must force UTF-8
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, "-m", "quoridor.cli", *args],
        input=stdin.encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", "replace")


def test_subprocess_random_bot_quit():
    code, out, err = run_cli(["--bot", "random", "--seed", "1"], "e2\nquit\n")
    assert code == 0
    assert "Traceback" not in err and "Traceback" not in out
    assert err == ""
    assert "Бот ходит:" in out
    assert "Стенки: белые (W) 10" in out
    assert "Выход." in out
    assert "�" not in out  # no mojibake / replacement chars


def test_subprocess_eof_exits_cleanly():
    code, out, err = run_cli(["--bot", "greedy"], "")
    assert code == 0 and "Traceback" not in err


def test_subprocess_black_bot_moves_first():
    code, out, err = run_cli(["--bot", "random", "--seed", "1", "--color", "black"], "quit\n")
    assert code == 0 and err == ""
    first_bot = out.index("Бот ходит:")
    assert first_bot < out.index("Выход.")
    # after the bot's move it is black's (human) turn
    assert "Ход: чёрные (B)" in out[first_bot:]


def test_subprocess_bad_args_nonzero():
    code, _, err = run_cli(["--bot", "nope"], "")
    assert code != 0 and "Traceback" not in err


def test_subprocess_same_seed_same_output():
    a = run_cli(["--bot", "random", "--seed", "5"], "e2\ne3\nquit\n")
    b = run_cli(["--bot", "random", "--seed", "5"], "e2\ne3\nquit\n")
    assert a == b


def test_subprocess_garbage_stream_no_traceback():
    garbage = "\n\n   \nхрень\n日本語\n\U0001f600\n\x00\ne2h2\nE2\n e2 \nquit\n"
    code, out, err = run_cli(["--bot", "random"], garbage)
    assert code == 0 and "Traceback" not in err


# ---- input robustness through play() ----


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "xyz",
        "e",
        "e22",
        "j1",
        "a0",
        "e10",
        "e2e",
        "e 2",
        "e2,",
        "хрень",
        "е2",  # Cyrillic 'е'
        "e٢",  # Arabic-Indic digit two
        "e²",
        "日本語",
        "\U0001f600",
        "ｅ２",  # fullwidth
        "\x00",
        "i9h",  # anchor outside a-h
        "a1h",  # anchor rank 1 not allowed
        "e3x",
        "-e2",
    ],
)
def test_garbage_input_reprompts_without_exception(bad):
    res, out = run([bad, "quit"], bot=FirstLegalPawnBot())
    assert res == "quit"
    assert "Не понял ход" in out
    assert "Бот ходит" not in out


def test_uppercase_and_spaces_accepted():
    res, out = run(["  E2  ", "quit"], bot=FirstLegalPawnBot())
    assert "Бот ходит" in out and "Не понял" not in out and "нелегален" not in out


def test_uppercase_wall_and_command():
    res, out = run(["E3H", "QUIT"], bot=FirstLegalPawnBot())
    assert res == "quit" and "Бот ходит" in out


def test_move_onto_occupied_cell_rejected():
    s = mk(white="e4", black="e5")
    res, out = run(["e5", "quit"], bot=FirstLegalPawnBot(), state=s)
    assert "нелегален" in out and "Бот ходит" not in out


def test_wall_off_board_rejected():
    # a1h / i9v are not valid anchors; i9h is out of the a-h range
    for w in ("a1h", "i9v", "i5h", "e1v"):
        res, out = run([w, "quit"], bot=FirstLegalPawnBot())
        assert "Не понял ход" in out and "Бот ходит" not in out, w


def test_wall_with_zero_left_rejected_and_state_unchanged():
    s = mk(walls_left=(0, 10))
    res, out = run(["e3h", "e3v", "quit"], bot=FirstLegalPawnBot(), state=s)
    assert out.count("нелегален") == 2 and "Бот ходит" not in out


def test_overlapping_wall_rejected():
    res, out = run(["e3h", "e3h", "e3v", "quit"], bot=FirstLegalPawnBot())
    # first wall accepted (bot replies once); the exact repeat and the cross at the same
    # centre are both rejected, so exactly two rejections and exactly one bot reply
    assert out.count("нелегален") == 2
    assert out.count("Бот ходит") == 1


def test_wall_blocking_last_path_rejected():
    # a2h already closes a1/b1 from above; b2v would seal {a1, b1} completely
    s = mk(white="a1", black="e9", h=[(0, 0)])
    res, out = run(["b2v", "quit"], bot=FirstLegalPawnBot(), state=s)
    assert "нелегален" in out and "Бот ходит" not in out


def test_input_after_game_over_not_consumed():
    s = mk(white="e9", black="a5")
    calls = []

    def inp(prompt):
        calls.append(prompt)
        return "e2"

    out: list[str] = []
    res = play(inp, out.append, bot=FirstLegalPawnBot(), state=s)
    assert res == "white" and calls == []
    assert any("Победили белые" in line for line in out)


def test_game_ends_right_after_winning_jump_no_bot_move():
    # white jumps over black onto the goal row: game over immediately, bot does not move
    s = mk(white="e8", black="e9")  # not a jump to goal; e9 occupied -> diagonal d9/f9 wins
    res, out = run(["d9"], bot=FirstLegalPawnBot(), state=s)
    assert res == "white" and out.count("Бот ходит") == 0


def test_black_human_bot_first_move_printed():
    res, out = run(["quit"], bot=GreedyBot(0), human_color=BLACK)
    assert "Бот ходит:" in out
    assert out.index("Бот ходит:") < out.index("Выход.")


# ---- full games ----


def _full_game(white_bot, black_bot, max_plies=400):
    s = GameState.initial()
    bots = (white_bot, black_bot)
    plies = 0
    while not is_terminal(s) and plies < max_plies:
        m = bots[s.turn].choose_move(s)
        assert is_legal_move(s, m), (plies, m)
        s = apply_move(s, m)
        assert min(s.walls_left) >= 0
        assert len(s.h_walls) + len(s.v_walls) + sum(s.walls_left) == 20
        plies += 1
    return s, plies


@pytest.mark.parametrize("cls", [RandomBot, GreedyBot])
def test_bot_vs_bot_full_game_consistent(cls):
    s, plies = _full_game(cls(1), cls(2))
    if is_terminal(s):
        w = winner(s)
        assert w in (WHITE, BLACK)
        goal = 8 if w == WHITE else 0
        assert s.pawns[w][1] == goal
    else:
        assert plies == 400


@pytest.mark.parametrize("cls", [RandomBot, GreedyBot])
def test_full_game_deterministic_per_seed(cls):
    a = _full_game(cls(3), cls(4), max_plies=120)
    b = _full_game(cls(3), cls(4), max_plies=120)
    assert a == b


def test_different_seeds_diverge_for_random():
    a = _full_game(RandomBot(1), RandomBot(2), max_plies=60)[0]
    b = _full_game(RandomBot(11), RandomBot(12), max_plies=60)[0]
    assert a != b


def test_play_greedy_vs_greedy_through_cli_play():
    """Drive play() with a bot on the human side too: results match a direct game."""
    gw, gb = GreedyBot(1), GreedyBot(2)
    expect, _ = _full_game(GreedyBot(1), GreedyBot(2), max_plies=300)

    class Human:
        def __call__(self, prompt):
            # the 'human' (white) plays whatever GreedyBot would play from the tracked state
            from quoridor.notation import format_move

            return format_move(gw.choose_move(self.state))

    h = Human()
    h.state = GameState.initial()
    out: list[str] = []
    last = {}

    def output(line):
        out.append(line)

    # mirror the state by replaying printed bot moves
    from quoridor.notation import parse_move

    def inp(prompt):
        for line in out[last.get("n", 0) :]:
            if line.startswith("Бот ходит: "):
                h.state = apply_move(h.state, parse_move(line.split(": ")[1]))
        last["n"] = len(out)
        mv = h(prompt)
        h.state = apply_move(h.state, parse_move(mv))
        return mv

    res = play(inp, output, bot=gb, max_moves=300)
    exp = "draw" if not is_terminal(expect) else ("white", "black")[winner(expect)]
    assert res == exp
