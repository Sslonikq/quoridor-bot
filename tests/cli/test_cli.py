from quoridor.bots import GreedyBot
from quoridor.cli import build_parser, play, render, status
from quoridor.notation import format_move, parse_move
from quoridor.rules import apply_move, legal_moves, winner
from quoridor.state import BLACK, BOARD_SIZE, WHITE, GameState, PawnMove, WallMove
from tests.helpers import mk
from tests.playouts import random_game


def scripted(lines):
    it = iter(lines)

    def f(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return f


def run(lines, **kw):
    out: list[str] = []
    res = play(scripted(lines), out.append, **kw)
    return res, "\n".join(out)


class FirstLegalPawnBot:
    """Scripted opponent: first legal pawn move (deterministic)."""

    def choose_move(self, state):
        return legal_moves(state)[0]


def test_render_initial():
    lines = render(GameState.initial()).split("\n")
    assert lines[0].split() == list("abcdefghi")
    assert lines[1].startswith("9 ") and lines[1].split()[1:][4] == "B"
    assert lines[-1].startswith("1 ") and lines[-1].split()[1:][4] == "W"
    assert "|" not in render(GameState.initial()) and "-" not in render(GameState.initial())
    assert "10" in status(GameState.initial()) and "белые" in status(GameState.initial())


def test_render_known_walls():
    # e3h: between ranks 3 and 2, columns e,f; a2v: between a|b, ranks 2 and 1
    s = mk(h=[parse_wall("e3h")], v=[parse_wall("a2v")])
    lines = render(s).split("\n")
    # rank 3 is lines[7], rank 2 is lines[8]; between them lines[... ]
    idx3 = next(i for i, ln in enumerate(lines) if ln.startswith("3 "))
    between = lines[idx3 + 1]
    assert between[2 + 4 * 4 : 2 + 4 * 4 + 3] == "---"
    assert between[2 + 4 * 5 : 2 + 4 * 5 + 3] == "---"
    assert between[2 + 4 * 4 + 3] == "-"
    assert between[2 + 4 * 3 : 2 + 4 * 3 + 3] == "   "
    idx2 = next(i for i, ln in enumerate(lines) if ln.startswith("2 "))
    idx1 = next(i for i, ln in enumerate(lines) if ln.startswith("1 "))
    assert lines[idx2][2 + 3] == "|" and lines[idx1][2 + 3] == "|"


def parse_wall(s):
    m = parse_move(s)
    assert isinstance(m, WallMove)
    return (m.x, m.y)


def _parse_render(text):
    """Recover blocked edges from the picture using only the fixed layout."""
    lines = text.split("\n")[1:]
    row_lines = {}
    between = {}
    for i, ln in enumerate(lines):
        if ln[:1].isdigit():
            r = int(ln[0]) - 1
            row_lines[r] = ln
            if r > 0:
                between[r] = lines[i + 1]
    return row_lines, between


def test_render_matches_blocked_on_random_positions():
    n = 0
    for seed in range(6):
        for s in random_game(seed, max_plies=80, wall_bias=0.7):
            rows, between = _parse_render(render(s))
            for r in range(BOARD_SIZE):
                for c in range(BOARD_SIZE - 1):
                    ln = rows[r].ljust(40)
                    assert (ln[2 + 4 * c + 3] == "|") == s.blocked(c, r, 1, 0)
                if r > 0:
                    b = between[r].ljust(40)
                    for c in range(BOARD_SIZE):
                        seg = b[2 + 4 * c : 2 + 4 * c + 3]
                        assert (seg == "---") == s.blocked(c, r, 0, -1)
            n += 1
    assert n > 100


def test_illegal_then_legal_then_quit():
    res, out = run(["zz", "e5", "e3", "e2", "quit"], bot=FirstLegalPawnBot())
    assert res == "quit"
    assert "Не понял ход 'zz'" in out
    assert "нелегален" in out  # e5 / e3 not reachable
    assert "Бот ходит:" in out  # e2 accepted, bot replied


def test_moves_command_lists_legal():
    res, out = run(["moves", "exit"], bot=FirstLegalPawnBot())
    assert res == "quit"
    assert "e2" in out and "d1" in out and "a4h" in out


def test_eof_is_quit():
    assert run([], bot=FirstLegalPawnBot())[0] == "quit"


def test_walls_exhausted_wall_rejected():
    s = mk(walls_left=(0, 10))
    res, out = run(["e3h", "quit"], bot=FirstLegalPawnBot(), state=s)
    assert "нелегален" in out


def test_human_wins_full_game():
    # White human walks e1->e9 vs a passive bot; bot moves are first-legal-move.
    s = mk(white="e7", black="a9")
    res, out = run(["e8", "e9"], bot=FirstLegalPawnBot(), state=s)
    assert res == "white"
    assert "Победили белые" in out


def test_bot_wins_as_black_human():
    s = mk(white="a8", black="e2", turn=1)
    res, out = run(["e1"], bot=FirstLegalPawnBot(), human_color=BLACK, state=s)
    assert res == "black"


def test_bot_wins_game():
    s = mk(white="e1", black="a2", turn=0)
    # human white shuffles; greedy black reaches rank 1
    res, out = run(["e2", "e1", "e2", "e1", "e2", "e1"], bot=GreedyBot(0), state=s)
    assert res == "black"
    assert "Победили чёрные" in out


def test_draw_on_max_moves():
    res, out = run(["e2", "e8", "e1", "e9"], bot=None, max_moves=4)
    assert res == "draw"
    assert "ничья" in out


def test_two_scripted_players_full_game_to_win():
    # Both sides human (bot=None): straight race along files a (white) and i (black).
    s = mk(white="a1", black="i9")
    seq = []
    cur = s
    while winner(cur) is None:
        c, r = cur.pawns[cur.turn]
        mv = PawnMove(c, r + 1 if cur.turn == WHITE else r - 1)
        seq.append(format_move(mv))
        cur = apply_move(cur, mv)
    res, out = run(seq, bot=None, state=s)
    assert res == "white"  # white moves first and both need 8 steps
    assert "Победили белые" in out


def test_parser_defaults():
    a = build_parser().parse_args([])
    assert (a.bot, a.color, a.seed, a.max_moves) == ("greedy", "white", 0, 300)
    a = build_parser().parse_args(["--bot", "random", "--color", "black", "--seed", "3"])
    assert (a.bot, a.color, a.seed) == ("random", "black", 3)
