"""Replay of real games from the site (tests/fixtures/games)."""

import json
from pathlib import Path

import pytest

from quoridor.notation import format_move, parse_move
from quoridor.paths import has_path_to_goal
from quoridor.rules import apply_move, is_terminal, legal_moves, pawn_moves, wall_moves
from quoridor.state import BLACK, WALLS_PER_PLAYER, WHITE, GameState, PawnMove, WallMove

GAMES = Path(__file__).resolve().parents[1] / "fixtures" / "games"
COLORS = {WHITE: "white", BLACK: "black"}


def load_moves(name: str) -> list[str]:
    moves: list[str] = []
    for line in (GAMES / f"{name}.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        moves.extend(line.split())
    return moves


def replay(name: str):
    """Yield (ply_index, mover, move_str, state_before, state_after)."""
    state = GameState.initial()
    for i, s in enumerate(load_moves(name)):
        move = parse_move(s)
        assert move in legal_moves(state), f"ply {i + 1} ({COLORS[state.turn]}) {s!r} is illegal"
        after = apply_move(state, move)
        yield i, state.turn, s, state, after
        state = after


def cell_name(cell) -> str:
    return format_move(PawnMove(*cell))


@pytest.mark.parametrize("name", ["game01"])
def test_game_replays_to_expected_position(name):
    expected = json.loads((GAMES / f"{name}.expected.json").read_text(encoding="utf-8"))
    final = GameState.initial()
    walls = {"white": [], "black": []}
    for _, mover, s, _, after in replay(name):
        if isinstance(parse_move(s), WallMove):
            walls[COLORS[mover]].append(s)
        final = after

    assert cell_name(final.pawns[WHITE]) == expected["white"]
    assert cell_name(final.pawns[BLACK]) == expected["black"]
    assert final.walls_left == (
        expected["walls_left"]["white"],
        expected["walls_left"]["black"],
    )
    for color in ("white", "black"):
        assert sorted(walls[color]) == sorted(expected["walls"][color])
    # every wall on the board is accounted for
    on_board = {format_move(w) for w in final.walls()}
    assert on_board == set(expected["walls"]["white"]) | set(expected["walls"]["black"])
    assert COLORS[final.turn] == expected["to_move"]
    assert expected["winner"] is None
    assert not is_terminal(final)


def test_game01_move23_white_diagonal_jump():
    moves = load_moves("game01")
    assert moves[2 * 22] == "f4"  # white's 23rd move
    state = GameState.initial()
    for s in moves[: 2 * 22]:
        state = apply_move(state, parse_move(s))
    assert state.turn == WHITE
    assert state.pawns == ((6, 2), (6, 3))  # white g3, black g4 right above
    assert PawnMove(6, 4) not in pawn_moves(state)  # straight jump to g5 blocked by f5h
    assert PawnMove(5, 3) in pawn_moves(state)  # diagonal to f4
    after = apply_move(state, parse_move("f4"))
    assert after.pawns[WHITE] == (5, 3)


def final_state(name: str = "game01") -> GameState:
    state = GameState.initial()
    for s in load_moves(name):
        state = apply_move(state, parse_move(s))
    return state


def test_game01_invariants_after_every_ply():
    n_plies = 0
    for _, _, s, _, after in replay("game01"):
        n_plies += 1
        assert len(after.walls()) + sum(after.walls_left) == 2 * WALLS_PER_PLAYER, s
        assert all(n >= 0 for n in after.walls_left), s
        assert has_path_to_goal(after, WHITE), s
        assert has_path_to_goal(after, BLACK), s
        assert 0 <= after.pawns[WHITE][0] < 9 and 0 <= after.pawns[BLACK][1] < 9
        assert after.pawns[WHITE] != after.pawns[BLACK], s
        # string round trip of the move that was just played
        assert format_move(parse_move(s)) == s
    assert n_plies == len(load_moves("game01"))


def test_game01_turn_alternates_and_no_early_winner():
    expected_turn = WHITE
    for _, mover, s, before, after in replay("game01"):
        assert mover == expected_turn, s
        assert after.turn == 1 - mover, s
        assert not is_terminal(before), s
        expected_turn = 1 - expected_turn
    assert not is_terminal(final_state())


def test_game01_wall_counts_match_plies():
    walls = {WHITE: 0, BLACK: 0}
    for _, mover, s, _, after in replay("game01"):
        if isinstance(parse_move(s), WallMove):
            walls[mover] += 1
        assert after.walls_left == (
            WALLS_PER_PLAYER - walls[WHITE],
            WALLS_PER_PLAYER - walls[BLACK],
        ), s


def _wall_strings(state: GameState) -> list[str]:
    return [format_move(w) for w in wall_moves(state)]


@pytest.mark.parametrize("which", ["initial", "final"])
def test_roundtrip_all_legal_walls(which):
    state = GameState.initial() if which == "initial" else final_state()
    walls = wall_moves(state)
    if which == "initial":
        assert len(walls) == 128  # empty board: every slot is legal
    else:
        assert walls  # white still has 4 walls and free slots
    strings = _wall_strings(state)
    assert len(set(strings)) == len(strings)  # format is injective
    for w, s in zip(walls, strings, strict=True):
        assert parse_move(s) == w
        assert format_move(parse_move(s)) == s
        assert s[1] in "23456789" and s[0] in "abcdefgh" and s[2] in "hv"


def test_roundtrip_all_legal_moves_every_ply():
    for _, _, _, before, _ in replay("game01"):
        for m in legal_moves(before):
            assert parse_move(format_move(m)) == m


def test_final_position_black_has_no_walls_to_offer():
    state = final_state()
    assert state.walls_left == (4, 0)
    after_white = apply_move(state, legal_moves(state)[0])
    if not is_terminal(after_white):
        assert wall_moves(after_white) == []  # black is out of walls


def test_walls_not_legal_twice_and_occupied_slots_excluded():
    state = final_state()
    legal = set(wall_moves(state))
    for w in state.walls():
        assert w not in legal
        # same slot, other orientation (cross) is illegal too
        other = WallMove(w.x, w.y, "v" if w.orient == "h" else "h")
        assert other not in legal


def test_wall_string_geometry_independent_of_formula():
    """Check every anchor string against the site's description via state.blocked,
    not via the notation's own formula."""
    for col in range(8):
        for rank in range(2, 10):
            row0 = rank - 1
            h = apply_wall_only(f"{'abcdefgh'[col]}{rank}h")
            # h: between rank and rank-1, columns col and col+1
            assert h.blocked(col, row0, 0, -1) and h.blocked(col + 1, row0, 0, -1)
            assert h.blocked(col, row0 - 1, 0, 1) and h.blocked(col + 1, row0 - 1, 0, 1)
            if col + 2 < 9:
                assert not h.blocked(col + 2, row0, 0, -1)
            if row0 + 1 < 9:
                assert not h.blocked(col, row0 + 1, 0, -1)
            assert len(h.walls()) == 1
            v = apply_wall_only(f"{'abcdefgh'[col]}{rank}v")
            # v: between col and col+1, ranks rank and rank-1
            assert v.blocked(col, row0, 1, 0) and v.blocked(col, row0 - 1, 1, 0)
            assert v.blocked(col + 1, row0, -1, 0) and v.blocked(col + 1, row0 - 1, -1, 0)
            if row0 + 1 < 9:
                assert not v.blocked(col, row0 + 1, 1, 0)
            if row0 - 2 >= 0:
                assert not v.blocked(col, row0 - 2, 1, 0)
            if col + 1 < 8:
                assert not v.blocked(col + 1, row0, 1, 0)


def apply_wall_only(s: str) -> GameState:
    return apply_move(GameState.initial(), parse_move(s))


@pytest.mark.parametrize("bad", ["a1h", "h1v", "i2h", "i9v", "a0h", "a10h", "j5h", "e1h"])
def test_out_of_range_walls_rejected_by_parse(bad):
    with pytest.raises(ValueError):
        parse_move(bad)


def test_game01_screenshot_walls_all_present_on_final_board():
    expected = json.loads((GAMES / "game01.expected.json").read_text(encoding="utf-8"))
    n = sum(len(v) for v in expected["walls"].values())
    assert n == len(final_state().walls()) == 16
    assert sum(final_state().walls_left) == 2 * WALLS_PER_PLAYER - n
