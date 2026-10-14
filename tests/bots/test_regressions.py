"""Phase 6 regressions: v3 beats greedy; every registered bot plays legal moves."""

import pytest

from quoridor.arena import play_game, run_match
from quoridor.bots import BOTS, GreedyBot, make_bot
from quoridor.state import BLACK, WHITE

pytestmark = pytest.mark.slow


def test_v3_beats_greedy_at_least_95_percent_on_50_games():
    m = run_match(50, "v3", "greedy", seed=0)
    t = m.total
    assert t.wins + t.losses + t.draws == 50
    assert t.wins >= 48, t  # >= 96%: draws (move limit) count as non-wins


@pytest.mark.parametrize("name", sorted(BOTS))
@pytest.mark.parametrize("color", [WHITE, BLACK])
def test_every_bot_plays_without_illegal_moves(name, color):
    # play_game applies each move with the checked apply_move: an illegal move raises
    # GameError. Clock/time bots get a short game (24 plies) to keep the suite fast.
    bot = make_bot(name, 3)
    if getattr(bot, "game_time", None) is not None:
        bot.game_time = 6.0  # a real 10-minute clock would spend ~10 s per move here
    res = play_game(bot, GreedyBot(1), color, max_moves=24)
    assert res.plies == len(res.moves) <= 24
