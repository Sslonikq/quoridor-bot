"""game04-06 at the analyzer level (report 0068): loading through /api/parse and the key hints.

The search-level versions of the same positions are in tests/search/test_eval_reserve.py
(Searcher.search_top); here the path is the user's: the file text -> /api/parse -> engine.analyze.
"""

import http.client
import json
import threading
from pathlib import Path

import pytest

from quoridor.analyzer import engine
from quoridor.analyzer.server import HOST, AnalyzerServer

FIXTURES = Path(__file__).parents[1] / "fixtures/games"
NAMES = ["game04.txt", "game05.txt", "game06.txt"]


def text_of(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def moves_of(name: str) -> list[str]:
    return [t for ln in text_of(name).splitlines() if not ln.startswith("#") for t in ln.split()]


@pytest.fixture(scope="module")
def server():
    srv = AnalyzerServer(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def post(srv, path, body):
    conn = http.client.HTTPConnection(HOST, srv.server_address[1], timeout=60)
    conn.request("POST", path, json.dumps(body).encode())
    r = conn.getresponse()
    data = json.loads(r.read())
    conn.close()
    return r.status, data


@pytest.mark.parametrize("name", NAMES)
def test_game_loads_through_api_parse_with_comments(server, name):
    """The raw file (with the '#' comment lines, ?? and the unfinished last pair) is accepted."""
    status, res = post(server, "/api/parse", {"text": text_of(name)})
    assert status == 200, res
    assert res["moves"] == moves_of(name)


def test_game_lengths_and_results(server):
    """game04 is finished (white e9), game05/06 are not finished and have no winner."""
    expected = {"game04.txt": 77, "game05.txt": 27, "game06.txt": 82}
    for name, n in expected.items():
        assert len(moves_of(name)) == n, name
        status, res = post(server, "/api/state", {"moves": moves_of(name)})
        assert status == 200 and res["ok"], res
    _, res = post(server, "/api/state", {"moves": moves_of("game04.txt")})
    assert res["position"]["winner"] == 0
    for name in ("game05.txt", "game06.txt"):
        _, res = post(server, "/api/state", {"moves": moves_of(name)})
        assert res["position"]["winner"] is None, name


def test_game_prefixes_replay_without_errors():
    """Every prefix of game04-06 is a legal position (the site games replay move by move)."""
    for name in NAMES:
        moves = moves_of(name)
        for n in range(0, len(moves) + 1, 4):
            engine.replay(moves[:n])
        engine.replay(moves)


# (game, plies played, best move from the comment, the wrong move that was played)
KEY = [
    ("game04.txt", 21, "f3v", "g4"),
    ("game05.txt", 13, "c7v", "b8h"),
    ("game05.txt", 21, "g7v", "h9h"),
    ("game06.txt", 15, "f6", "h6"),
]


def test_key_positions_have_the_played_move_from_the_comment():
    for name, n, _, played in KEY:
        assert moves_of(name)[n] == played, (name, n)


@pytest.mark.slow
@pytest.mark.parametrize(("name", "n", "hint", "played"), KEY)
def test_analyzer_finds_the_hint_of_the_comment(name, n, hint, played):
    res = engine.analyze(moves_of(name)[:n], 3.0)
    assert res["top"][0]["name"] == hint, (name, n, res["top"])


@pytest.mark.slow
@pytest.mark.parametrize("name,n", [(a, b) for a, b, _, _ in KEY])
def test_analyzer_hint_beats_the_played_move_by_evaluate_move(name, n):
    """The played move is scored below the hint (the '??' of the comment), at 3 s each."""
    moves = moves_of(name)
    best = engine.analyze(moves[:n], 3.0)["top"][0]
    played = engine.evaluate_move(moves[:n], moves[n], 3.0)
    assert not played["cancelled"]
    side = 1 if len(moves[:n]) % 2 == 0 else -1  # score is white's view
    assert side * (best["score"] - played["score"]) >= 0, (best, played["score"])


@pytest.mark.slow
@pytest.mark.parametrize("n", [33, 35, 37, 39])
def test_game06_analyzer_keeps_the_last_walls(n):
    """game06 17...-20...: black has <= 2 walls, white all 10; the top-1 is not a wall."""
    moves = moves_of("game06.txt")[:n]
    state = engine.replay(moves)
    assert state.turn == 1 and state.walls_left[0] == 10 and state.walls_left[1] <= 2
    res = engine.analyze(moves, 3.0)
    assert res["top"][0]["k"] == "p", (n, res["top"])
