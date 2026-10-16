"""Phase 8 criteria end to end: a real `python -m quoridor.analyzer` process, only HTTP (slow).

Covers what the API can show of the UI: game01 loads, review runs to done, marks exist,
stepping (positions at every ply), branch and return (move lists), errors with a move number.
The look of the page is checked by the user by hand.
"""

import http.client
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow
ROOT = Path(__file__).parents[2]
GAME01_TEXT = (ROOT / "tests/fixtures/games/game01.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def port():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "quoridor.analyzer", "--no-browser"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        line = proc.stdout.readline().decode("utf-8")
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        assert m, line
        yield int(m.group(1))
    finally:
        proc.terminate()  # our own PID only
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
        proc.stdout.close()


def post(port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", path, json.dumps(body).encode())
    r = conn.getresponse()
    data = json.loads(r.read())
    conn.close()
    return r.status, data


def run_review(port, moves, seconds, timeout=120):
    status, res = post(port, "/api/review", {"moves": moves, "time": seconds})
    assert status == 200 and res["total"] == len(moves) + 1
    seen, results, polls = 0, [], 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        status, st = post(port, "/api/review/status", {"id": res["id"], "since": seen})
        assert status == 200 and st["ok"]
        results += st["results"]
        seen += len(st["results"])
        assert st["done"] == seen <= st["total"]
        polls += 1
        if st["finished"]:
            return st, results, polls
        time.sleep(0.1)
    raise AssertionError("review did not finish")


def test_phase8_game01_load_review_step_branch_return_errors(port):
    # 1. load: the site text goes through /api/parse
    status, res = post(port, "/api/parse", {"text": GAME01_TEXT})
    assert status == 200
    game = res["moves"]
    assert len(game) == 52 and game[:3] == ["d1", "e8", "c1"]

    # 2. review runs, progress reaches done, summary and marks are there
    final, results, polls = run_review(port, game, 0.1)
    assert final["finished"] and not final["cancelled"] and final["error"] is None
    assert final["done"] == final["total"] == 53 and final["summary"]
    assert [r["index"] for r in results] == list(range(53))
    assert [r["move"] for r in results] == [*game, None]
    marks = [r.get("mark") for r in results]
    assert set(marks) <= {None, "b", "m", "i", "g"}
    assert any(marks), "a whole game with no marks at all"
    for r in results[:-1]:
        assert r["loss"] >= 0 and 0 <= r["accuracy"] <= 100 and r["top"]
    for side in ("white", "black"):
        assert 0 <= final["summary"][side]["accuracy"] <= 100
        assert final["summary"][side]["moves"] == 26
    assert results[-1]["move"] is None and results[-1]["top"]  # game01 is unfinished

    # 3. stepping: every prefix replays; the recorded final position is c4 / i3
    for ply in (0, 1, 10, 25, 51, 52):
        status, st = post(port, "/api/state", {"moves": game[:ply]})
        assert status == 200
        pos = st["position"]
        assert pos["turn"] == ply % 2
        if ply:
            assert pos["last"] is not None
    assert [p["name"] for p in pos["pawns"]] == ["c4", "i3"] and pos["winner"] is None

    # 4. branch at ply 10: the main game is kept by the client, the server sees only lists
    status, st = post(port, "/api/state", {"moves": game[:10]})
    alt = next(m["name"] for m in st["position"]["legal"] if m["name"] != game[10])
    branch = [*game[:10], alt]
    status, st = post(port, "/api/state", {"moves": branch})
    assert status == 200 and st["position"]["turn"] == 1
    bfinal, bres, _ = run_review(port, branch, 0.1)
    assert bfinal["done"] == 12 and [r["move"] for r in bres[:11]] == branch
    # the shared start of the branch and of the game is marked the same way (same moves)
    assert [r["move"] for r in bres[:10]] == [r["move"] for r in results[:10]]

    # 5. return to the game: the original list replays to the same final position
    status, back = post(port, "/api/state", {"moves": game})
    assert status == 200 and back["position"]["winner"] is None
    assert back["position"]["pawns"] == pos["pawns"]
    again, _, _ = run_review(port, game[:6], 0.1)
    assert again["done"] == 7

    # 6. errors carry the move number: parse, state, review, evaluate
    bad_text = "d1 e8 c1 e7 b1 e6 b1"  # b1 again: the white pawn is already there
    status, err = post(port, "/api/parse", {"text": bad_text})
    assert status == 400 and err["bad_move"] == 7
    status, err = post(port, "/api/parse", {"text": "d1 e8 zz"})
    assert status == 400 and err["bad_move"] == 3
    status, err = post(port, "/api/state", {"moves": [*game[:5], "e9"]})
    assert status == 400 and err["bad_move"] == 6
    status, err = post(port, "/api/review", {"moves": [*game[:4], "a9"]})
    assert status == 400 and err["bad_move"] == 5
    status, err = post(port, "/api/evaluate", {"moves": game[:3], "move": "e5", "time": 0.1})
    assert status == 400 and err["bad_move"] == 4
