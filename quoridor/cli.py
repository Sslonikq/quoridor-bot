"""Console game: human vs bot. All I/O is injected into play(); only main() touches stdin/stdout."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from quoridor.bots import Bot, GreedyBot, RandomBot
from quoridor.notation import col_name, format_move, parse_move, row_name
from quoridor.rules import apply_move, is_legal_move, legal_moves, winner
from quoridor.state import BLACK, BOARD_SIZE, WHITE, GameState, PawnMove, WallMove

DEFAULT_MAX_MOVES = 300
PAWN_CHAR = ("W", "B")
PLAYER_NAME = ("белые", "чёрные")
HELP = "Ход: клетка (e2) или стенка (e3h / f3v). Команды: moves, help, quit."


def render(state: GameState) -> str:
    """ASCII board. Row 9 on top. Cell = 3 chars + 1 separator char ('|' = vertical wall).

    Between rows, '---' marks a horizontal wall on that edge; the junction char is
    '-' / '|' where a wall's middle lies. Everything is derived from GameState.blocked.
    """
    lines = ["  " + "".join(f" {col_name(c)}  " for c in range(BOARD_SIZE)).rstrip()]
    for row in range(BOARD_SIZE - 1, -1, -1):
        text = f"{row_name(row)} "
        for col in range(BOARD_SIZE):
            if state.pawns[WHITE] == (col, row):
                ch = PAWN_CHAR[WHITE]
            elif state.pawns[BLACK] == (col, row):
                ch = PAWN_CHAR[BLACK]
            else:
                ch = "."
            text += f" {ch} "
            if col < BOARD_SIZE - 1:
                text += "|" if state.blocked(col, row, 1, 0) else " "
        lines.append(text.rstrip())
        if row > 0:
            seg = "  "
            for col in range(BOARD_SIZE):
                seg += "---" if state.blocked(col, row, 0, -1) else "   "
                if col < BOARD_SIZE - 1:
                    if (col, row - 1) in state.h_walls:
                        seg += "-"
                    elif (col, row - 1) in state.v_walls:
                        seg += "|"
                    else:
                        seg += " "
            lines.append(seg.rstrip() if seg.strip() else "")
    return "\n".join(lines)


def status(state: GameState) -> str:
    w, b = state.walls_left
    return (
        f"Стенки: белые (W) {w}, чёрные (B) {b}. "
        f"Ход: {PLAYER_NAME[state.turn]} ({PAWN_CHAR[state.turn]})."
    )


def play(
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    bot: Bot | None = None,
    human_color: int = WHITE,
    max_moves: int = DEFAULT_MAX_MOVES,
    state: GameState | None = None,
) -> str:
    """Run a game. Returns 'white' | 'black' | 'draw' | 'quit'.

    The human side is driven by input_fn(prompt); the other side by `bot`
    (bot=None means both sides are human). EOF on input counts as quit.
    """
    if state is None:
        state = GameState.initial()
    plies = 0
    output_fn(HELP)
    while True:
        output_fn(render(state))
        output_fn(status(state))
        w = winner(state)
        if w is not None:
            output_fn(f"Победили {PLAYER_NAME[w]}!")
            return ("white", "black")[w]
        if plies >= max_moves:
            output_fn(f"Лимит {max_moves} полуходов: ничья.")
            return "draw"

        if bot is not None and state.turn != human_color:
            move = bot.choose_move(state)
            output_fn(f"Бот ходит: {format_move(move)}")
        else:
            move = None
            while move is None:
                try:
                    raw = input_fn("Ваш ход> ").strip().lower()
                except EOFError:
                    return "quit"
                if raw in ("quit", "exit"):
                    output_fn("Выход.")
                    return "quit"
                if raw == "help":
                    output_fn(HELP)
                    continue
                if raw == "moves":
                    all_moves = legal_moves(state)
                    pawn = sorted(format_move(m) for m in all_moves if isinstance(m, PawnMove))
                    walls = sorted(format_move(m) for m in all_moves if isinstance(m, WallMove))
                    output_fn("Шаги: " + " ".join(pawn))
                    output_fn("Постановки: " + (" ".join(walls) if walls else "нет"))
                    continue
                try:
                    cand = parse_move(raw)
                except ValueError:
                    output_fn(f"Не понял ход {raw!r}. {HELP}")
                    continue
                if not is_legal_move(state, cand):
                    output_fn(f"Ход {raw} нелегален. Список: moves.")
                    continue
                move = cand
        state = apply_move(state, move)
        plies += 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m quoridor.cli", description="Коридор: человек против бота"
    )
    p.add_argument("--bot", choices=("random", "greedy"), default="greedy")
    p.add_argument("--color", choices=("white", "black"), default="white")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--max-moves", type=int, default=DEFAULT_MAX_MOVES, help="лимит полуходов (ничья)"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdin, sys.stdout):  # Russian text on Windows consoles
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    bot: Bot = RandomBot(args.seed) if args.bot == "random" else GreedyBot(args.seed)
    color = WHITE if args.color == "white" else BLACK
    try:
        play(input, print, bot=bot, human_color=color, max_moves=args.max_moves)
    except KeyboardInterrupt:
        print("\nВыход.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
