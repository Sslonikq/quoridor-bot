"""Game review: text parsing, marks, background review job over HTTP."""

import threading
import time
from pathlib import Path

import pytest

from quoridor.analyzer import engine
from quoridor.analyzer.engine import MoveListError
from quoridor.analyzer.server import HOST, AnalyzerServer
from tests.analyzer.test_server import call, post

GAMES = Path(__file__).parents[1] / "fixtures/games"
SLOW = pytest.mark.slow


def game_text(name):
    return (GAMES / name).read_text(encoding="utf-8")


def game_moves(name):
    return engine.parse_game_text(game_text(name))


# After these 14 plies white is on d1 and black on e2: black wins with e1 unless white walls
# it off (e2h). White's d2 instead of that loses at once.
BLACK_RUSH = ["e2", "e8", "e1", "e7", "e2", "e6", "e1", "e5", "e2", "e4", "e1", "e3", "d1", "e2"]


@pytest.fixture
def server():
    srv = AnalyzerServer(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


# ---- parsing ----


def test_parse_game_files_with_comments_and_pairs():
    g1, g2 = game_moves("game01.txt"), game_moves("game02.txt")
    assert g1[:6] == ["d1", "e8", "c1", "e7", "b1", "e6"] and len(g1) == 52
    assert len(g2) == 58 and all(isinstance(m, str) for m in g2)


@pytest.mark.parametrize(
    "text",
    [
        "e2 e8 e3",
        "e2\ne8\ne3",
        "  E2,  e8 ;e3 # a comment\n",
        "# header\n1. e2 e8\n2.e3",
        "1.e2 e8 2. e3",
    ],
)
def test_parse_separators_comments_numbers_case(text):
    assert engine.parse_game_text(text) == ["e2", "e8", "e3"]


def test_parse_junk_token_has_its_number():
    with pytest.raises(MoveListError) as err:
        engine.parse_game_text("e2 zz")
    assert err.value.number == 2


def test_parse_illegal_move_has_its_number():
    with pytest.raises(MoveListError) as err:
        engine.parse_game_text("e2 e8 e8")
    assert err.value.number == 3


@pytest.mark.parametrize("text", ["", "# only a comment", "e2 e5"])
def test_parse_other_errors(text):
    with pytest.raises(MoveListError):
        engine.parse_game_text(text)


def test_parse_rejects_non_text_and_huge_games():
    with pytest.raises(MoveListError):
        engine.parse_game_text(["e2"])
    with pytest.raises(MoveListError):
        engine.parse_game_text("e2 " * (engine.MAX_MOVES + 1))


# ---- marks and accuracy ----


@pytest.mark.parametrize(
    ("loss", "best", "gap", "mark"),
    [
        (0, True, None, None),
        (74, False, None, None),
        (75, False, None, "i"),
        (149, False, 0, "i"),
        (150, False, None, "m"),
        (299, False, None, "m"),
        (300, False, None, "b"),
        (2000, False, None, "b"),
        (0, True, 149, None),
        (0, True, 150, "g"),
        (0, False, 500, None),  # an equally good non-best move is not "!"
    ],
)
def test_mark_thresholds(loss, best, gap, mark):
    assert engine.mark_for(loss, best, gap) == mark


def test_accuracy_formula():
    assert engine.move_accuracy(0) == 100
    assert engine.move_accuracy(100) == 75  # one step lost: -25
    assert engine.move_accuracy(400) == 0 and engine.move_accuracy(5000) == 0


def test_summarize_splits_sides_and_counts_marks():
    steps = [
        {"index": 0, "move": "a", "accuracy": 100.0, "mark": None},
        {"index": 1, "move": "b", "accuracy": 50.0, "mark": "m"},
        {"index": 2, "move": "c", "accuracy": 0.0, "mark": "b"},
        {"index": 3, "move": None},  # the final position: no move
    ]
    s = engine.summarize(steps)
    assert s["white"]["accuracy"] == 50 and s["white"]["b"] == 1 and s["white"]["moves"] == 2
    assert s["black"]["accuracy"] == 50 and s["black"]["m"] == 1
    assert engine.summarize([])["white"]["accuracy"] is None


# ---- review_game ----


def test_review_prefix_of_game01_has_a_step_per_position():
    moves = game_moves("game01.txt")[:6]
    steps = list(engine.review_game(moves, 0.15))
    assert [s["index"] for s in steps] == list(range(7))
    assert [s["move"] for s in steps] == [*moves, None]
    for s in steps[:-1]:
        assert s["top"] and s["loss"] >= 0 and 0 <= s["accuracy"] <= 100
        assert s["played"]["score"] is not None
    assert "played" not in steps[-1] and steps[-1]["top"]


def test_review_rejects_bad_games_before_the_first_step():
    with pytest.raises(MoveListError) as err:
        list(engine.review_game(["e2", "e8", "e8"], 0.1))
    assert err.value.number == 3


def test_review_stops_when_cancelled():
    seen = []
    cancel = {"on": False}
    for step in engine.review_game(["e2", "e8", "e3", "e7"], 0.2, lambda: cancel["on"]):
        seen.append(step["index"])
        cancel["on"] = True
    assert seen == [0]


def test_review_final_position_of_a_won_game_is_a_mate():
    moves = [*BLACK_RUSH, "d2", "e1"]
    steps = list(engine.review_game(moves, 0.1))
    last = steps[-1]
    assert last["index"] == len(moves) and last["top"] == []
    assert last["eval_mate"] and last["eval"] < 0  # black won


def test_review_marks_the_blunder_that_loses_at_once():
    moves = [*BLACK_RUSH, "d2", "e1"]
    steps = list(engine.review_game(moves, 0.2))
    blunder = steps[len(BLACK_RUSH)]  # white: d2 instead of walling e1 off
    assert blunder["move"] == "d2" and blunder["mark"] == "b" and blunder["loss"] >= 300
    assert blunder["played"]["mate"] and blunder["played"]["score"] < 0
    assert engine.summarize(steps)["white"]["b"] >= 1


# ---- HTTP ----


def test_parse_route(server):
    status, res = post(server, "/api/parse", {"text": "# x\n1. d1 e8\nc1 e7"})
    assert status == 200 and res["moves"] == ["d1", "e8", "c1", "e7"]
    status, res = post(server, "/api/parse", {"text": "d1 e8 d1"})
    assert status == 400 and res["bad_move"] == 3
    status, res = post(server, "/api/parse", {"text": 5})
    assert status == 400 and not res["ok"]
    assert post(server, "/api/parse", {"text": "   # only a comment"})[0] == 400


def wait_review(server, job_id, timeout=30):
    since, results = 0, []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        status, res = post(server, "/api/review/status", {"id": job_id, "since": since})
        assert status == 200 and res["ok"]
        results += res["results"]
        since += len(res["results"])
        assert res["done"] == since
        if res["finished"]:
            return res, results
        time.sleep(0.05)
    raise AssertionError("review did not finish")


def test_review_route_progress_and_results(server):
    moves = game_moves("game01.txt")[:8]
    status, res = post(server, "/api/review", {"moves": moves, "time": 0.15})
    assert status == 200 and res["total"] == 9
    final, results = wait_review(server, res["id"])
    assert not final["cancelled"] and final["error"] is None and final["done"] == 9
    assert [r["index"] for r in results] == list(range(9))
    assert final["summary"]["white"]["moves"] == 4 and final["summary"]["black"]["moves"] == 4


def test_review_errors(server):
    for body in (
        {"moves": []},
        {"moves": ["e2", "e8", "e8"]},
        {"moves": "e2"},
        {"moves": ["e2"], "time": "1"},
        {"moves": ["e2"], "time": True},
    ):
        status, res = post(server, "/api/review", body)
        assert status == 400 and not res["ok"], body
    assert post(server, "/api/review", {"moves": ["e2", "e8", "e8"]})[1]["bad_move"] == 3
    assert post(server, "/api/review/status", {"id": 99})[0] == 404
    assert post(server, "/api/review/status", {"since": 0})[0] == 404
    res = post(server, "/api/review", {"moves": ["e2"], "time": 0.1})[1]
    for bad in (-1, "0", True):
        assert post(server, "/api/review/status", {"id": res["id"], "since": bad})[0] == 400


def test_review_can_be_cancelled(server):
    moves = game_moves("game01.txt")[:20]
    status, res = post(server, "/api/review", {"moves": moves, "time": 2})
    assert status == 200
    time.sleep(0.5)
    t0 = time.perf_counter()
    assert post(server, "/api/review/cancel", {})[1]["ok"]
    final, results = wait_review(server, res["id"], timeout=5)
    assert final["cancelled"] and final["done"] < final["total"]
    assert time.perf_counter() - t0 < 3


def test_new_review_cancels_the_old_one_and_old_id_is_gone(server):
    moves = game_moves("game01.txt")[:20]
    first = post(server, "/api/review", {"moves": moves, "time": 3})[1]
    second = post(server, "/api/review", {"moves": ["e2", "e8"], "time": 0.1})[1]
    assert second["id"] != first["id"]
    assert post(server, "/api/review/status", {"id": first["id"]})[0] == 404
    final, _ = wait_review(server, second["id"])
    assert final["done"] == 3


def test_review_cancels_a_running_analysis(server):
    out = {}
    t = threading.Thread(
        target=lambda: out.update(a=post(server, "/api/analyze", {"moves": [], "time": 8}))
    )
    t.start()
    time.sleep(0.5)
    res = post(server, "/api/review", {"moves": ["e2", "e8"], "time": 0.1})[1]
    t.join(10)
    assert out["a"][1].get("cancelled") is True
    wait_review(server, res["id"])


def test_review_routes_are_post_only_on_loopback(server):
    assert server.server_address[0] == HOST
    assert call(server, "GET", "/api/review")[0] == 404


@SLOW
@pytest.mark.parametrize("name", ["game01.txt", "game02.txt"])
def test_review_whole_game_small_budget(server, name):
    moves = game_moves(name)
    status, res = post(server, "/api/review", {"moves": moves, "time": 0.1})
    assert status == 200 and res["total"] == len(moves) + 1
    final, results = wait_review(server, res["id"], timeout=180)
    assert final["error"] is None and not final["cancelled"] and len(results) == len(moves) + 1
    assert {r.get("mark") for r in results} <= {None, "b", "m", "i", "g"}
    for side in ("white", "black"):
        acc = final["summary"][side]["accuracy"]
        assert acc is not None and 0 <= acc <= 100
    assert results[-1]["move"] is None


# ---- nits of review 0057 ----


def test_review_id_must_be_a_real_int(server):
    status, r = post(server, "/api/review", {"moves": BLACK_RUSH[:2], "time": 0.1})
    assert status == 200 and r["id"] == 1
    for bad in (True, 1.0, "1"):
        status, _ = post(server, "/api/review/status", {"id": bad, "since": 0})
        assert status == 404, bad
    status, _ = post(server, "/api/review/status", {"id": 1, "since": 0})
    assert status == 200


def test_review_error_is_written_and_read_under_the_lock(monkeypatch):
    from quoridor.analyzer.server import ReviewJob

    def boom(*a, **k):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(engine, "review_game", boom)
    job = ReviewJob(1, ["e2"], 0.1)
    with job.lock:
        job.thread.start()
        time.sleep(0.2)
        assert job.error is None  # the thread waits for the lock
    job.thread.join(5)
    st = job.status(0)
    assert st["finished"] and st["error"] == "RuntimeError: boom"
