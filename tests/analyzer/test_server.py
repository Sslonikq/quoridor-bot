"""HTTP API of the analyzer on a real server (127.0.0.1, random port)."""

import http.client
import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from quoridor.analyzer.server import HOST, AnalyzerServer


@pytest.fixture
def server():
    srv = AnalyzerServer(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(HOST, srv.server_address[1], timeout=20)
    data = None if body is None else json.dumps(body).encode()
    conn.request(method, path, data, headers or {})
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


def post(srv, path, body):
    status, raw = call(srv, "POST", path, body)
    return status, json.loads(raw)


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1" and server.server_address[1] > 0


def test_static_files_are_served_and_self_contained(server):
    for path in ("/", "/app.js", "/app.css"):
        status, raw = call(server, "GET", path)
        assert status == 200 and raw
        text = raw.decode("utf-8").replace("http://www.w3.org/2000/svg", "")  # XML namespace id
        assert "http://" not in text and "https://" not in text  # no CDN, no external fonts
    assert call(server, "GET", "/../server.py")[0] == 404


def test_state_ok(server):
    status, res = post(server, "/api/state", {"moves": ["e2", "e8"]})
    assert status == 200 and res["ok"]
    assert [m["name"] for m in res["position"]["pawns"]] == ["e2", "e8"] and res["position"][
        "turn"
    ] == 0


def test_state_illegal_move_gives_its_number(server):
    status, res = post(server, "/api/state", {"moves": ["e2", "e8", "e8"]})
    assert status == 400 and not res["ok"] and res["bad_move"] == 3


@pytest.mark.parametrize("body", [{"moves": "e2"}, {"moves": [1]}, {"moves": ["zz"]}])
def test_state_garbage_is_a_400(server, body):
    assert post(server, "/api/state", body)[0] == 400


def test_client_cannot_send_a_game_state(server):
    status, res = post(server, "/api/state", {"pawns": [[0, 0], [0, 1]], "walls": []})
    assert status == 200  # extra keys are ignored: the position is the initial one
    assert [m["name"] for m in res["position"]["pawns"]] == ["e1", "e9"]


def test_bad_json_and_host(server):
    conn = http.client.HTTPConnection(HOST, server.server_address[1])
    try:
        conn.request("POST", "/api/state", b"[1]", {})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 400
    finally:
        conn.close()
    status, _ = call(server, "GET", "/", headers={"Host": "evil.example"})
    assert status == 403


def test_analyze_returns_top3(server):
    status, res = post(server, "/api/analyze", {"moves": ["e2"], "time": 0.3})
    assert status == 200 and res["ok"] and not res.get("cancelled")
    assert len(res["top"]) == 3 and res["position"]["turn"] == 1


def test_analyze_rejects_illegal_moves(server):
    status, res = post(server, "/api/analyze", {"moves": ["e5"], "time": 0.3})
    assert status == 400 and res["bad_move"] == 1


def test_new_analyze_cancels_the_stale_one(server):
    out = {}

    def slow():
        out["a"] = post(server, "/api/analyze", {"moves": [], "time": 8})

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    status, res = post(server, "/api/analyze", {"moves": ["e2"], "time": 0.3})
    t.join(10)
    assert status == 200 and not res.get("cancelled")
    assert out["a"][1].get("cancelled") is True
    assert time.perf_counter() - t0 < 5


# ---- phase 7 criteria: illegal moves, wins, cancellation, time, network isolation ----

WALLED_IN = ["b2h", "e8", "d2h", "e9", "f2h", "e8", "h2h", "e9"]  # white: only a1 -> a2 is open
WIN_WHITE = [
    "e2",
    "e8",
    "e3",
    "e7",
    "e4",
    "e6",
    "e5",
    "d6",
    "e6",
    "d5",
    "e7",
    "d4",
    "e8",
    "d3",
    "e9",
]
STATIC_DIR = Path(__file__).parents[2] / "quoridor" / "analyzer" / "static"


@pytest.mark.parametrize("route", ["/api/state", "/api/analyze"])
@pytest.mark.parametrize(
    ("moves", "bad"),
    [
        (["e3h", "e7h", "e3h"], 3),  # wall on top of the same wall
        (["e3h", "e7h", "d3h"], 3),  # partial overlap
        (["e3h", "e7h", "e3v"], 3),  # cross at the same centre
        (WALLED_IN + ["a2v"], 9),  # cuts the last way to the goal
        (["e2h", "e8", "e2"], 3),  # step through a wall
        (["e1"], 1),  # not a move at all
        (["a1h"], 1),  # anchor on rank 1 is outside the board
        (["i2h"], 1),  # anchor in column i
    ],
)
def test_server_rejects_illegal_moves(server, route, moves, bad):
    status, res = post(server, route, {"moves": moves, "time": 0.2})
    assert status == 400 and res["ok"] is False and res["bad_move"] == bad


def test_walled_in_prefix_is_legal(server):
    status, res = post(server, "/api/state", {"moves": WALLED_IN})
    assert status == 200 and res["position"]["dist"][0] is not None


def test_no_move_after_a_win_white(server):
    status, res = post(server, "/api/state", {"moves": WIN_WHITE})
    assert status == 200 and res["position"]["winner"] == 0 and res["position"]["legal"] == []
    for extra in ("e8", "e1", "a2h"):
        status, res = post(server, "/api/state", {"moves": [*WIN_WHITE, extra]})
        assert status == 400 and res["bad_move"] == len(WIN_WHITE) + 1


def test_win_for_black_and_no_move_after_it(server):
    # white shuffles, black walks e9 -> e1 (white's pawn steps aside at the end)
    moves = ["e2", "e8", "e1", "e7", "e2", "e6", "e1", "e5", "e2", "e4", "e1", "e3", "d1", "e2"]
    status, res = post(server, "/api/state", {"moves": moves})
    assert status == 200 and res["position"]["turn"] == 0
    # white is on d1, black on e2: black steps to e1 and wins
    moves += ["d2", "e1"]
    status, res = post(server, "/api/state", {"moves": moves})
    assert status == 200, res
    assert res["position"]["winner"] == 1 and res["position"]["legal"] == []
    status, res = post(server, "/api/state", {"moves": [*moves, "d3"]})
    assert status == 400 and res["bad_move"] == len(moves) + 1
    status, res = post(server, "/api/analyze", {"moves": moves, "time": 0.2})
    assert status == 200 and res["top"] == [] and res["position"]["winner"] == 1


def test_analyze_on_finished_game_is_instant(server):
    t0 = time.perf_counter()
    status, res = post(server, "/api/analyze", {"moves": WIN_WHITE, "time": 5})
    assert status == 200 and res["top"] == [] and time.perf_counter() - t0 < 1


@pytest.mark.slow
def test_hint_within_3s_at_time_2(server):
    t0 = time.perf_counter()
    status, res = post(server, "/api/analyze", {"moves": ["e2", "e8"], "time": 2})
    elapsed = time.perf_counter() - t0
    assert status == 200 and len(res["top"]) == 3
    assert elapsed <= 3.0, elapsed


GAME01 = [
    t
    for line in (Path(__file__).parents[1] / "fixtures/games/game01.txt")
    .read_text("utf-8")
    .splitlines()
    if not line.startswith("#")
    for t in line.split()
]


@pytest.mark.slow
@pytest.mark.parametrize("n", [20, 30, 40, 50, len(GAME01) - 4, len(GAME01) - 1])
def test_hint_within_3s_at_time_2_game01_positions(server, n):
    t0 = time.perf_counter()
    status, res = post(server, "/api/analyze", {"moves": GAME01[:n], "time": 2})
    elapsed = time.perf_counter() - t0
    assert status == 200 and res["ok"] and not res.get("cancelled"), res
    assert res["top"] and all(m["name"] for m in res["top"])
    assert elapsed <= 3.0, (n, elapsed)


def test_rejected_request_does_not_cancel_the_running_analysis(server):
    out = {}

    def run():
        out["a"] = post(server, "/api/analyze", {"moves": [], "time": 1.0})

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert post(server, "/api/analyze", {"moves": ["e5"], "time": 0.2})[0] == 400
    t.join(10)
    assert out["a"][0] == 200 and not out["a"][1].get("cancelled")


@pytest.mark.slow
def test_stale_analysis_chain_only_last_survives(server):
    results = {}

    def run(i):
        results[i] = post(server, "/api/analyze", {"moves": [], "time": 6})[1]

    threads = []
    for i in range(3):
        th = threading.Thread(target=run, args=(i,))
        th.start()
        threads.append(th)
        time.sleep(0.3)
    last = post(server, "/api/analyze", {"moves": ["e2"], "time": 0.3})[1]
    for th in threads:
        th.join(10)
    assert all(r.get("cancelled") is True for r in results.values())
    assert not last.get("cancelled")


def test_hostile_origin_and_host_rejected(server):
    status, _ = call(server, "POST", "/api/state", {"moves": []}, {"Origin": "http://evil.example"})
    assert status == 403
    status, _ = call(server, "POST", "/api/state", {"moves": []}, {"Host": "evil.example"})
    assert status == 403
    status, _ = call(
        server, "GET", "/", headers={"Origin": f"http://127.0.0.1:{server.server_address[1]}"}
    )
    assert status == 200


def test_oversized_body_and_bad_time(server):
    status, _ = post(server, "/api/state", {"moves": ["e2"] * 40000})
    assert status == 413
    assert post(server, "/api/analyze", {"moves": [], "time": "2"})[0] == 400
    assert post(server, "/api/analyze", {"moves": [], "time": True})[0] == 400


def test_not_reachable_through_a_non_loopback_address(server):
    try:
        other = socket.gethostbyname(socket.gethostname())
    except OSError:
        pytest.skip("no host address")
    if other.startswith("127."):
        pytest.skip("host resolves to loopback only")
    with pytest.raises(OSError), socket.create_connection((other, server.server_address[1]), 1):
        pass


def test_static_dir_has_no_external_urls():
    files = [p for p in STATIC_DIR.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"index.html", "app.js", "app.css"}
    for p in files:
        text = p.read_text("utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # comments
        assert not re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", text, re.I), p.name
        assert not re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", text, re.I), p.name
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", text), p.name
        assert not re.search(r"wss?://|new\s+WebSocket|EventSource|sendBeacon", text), p.name
        for m in re.finditer(r"fetch\(\s*([^,)]+)", text):
            assert not re.match(r"\s*['\"`](?:https?:)?//", m.group(1)), p.name


def _record_errors(server):
    errors = []
    server.handle_error = lambda request, client_address: errors.append(client_address)
    return errors


def test_413_closes_connection_without_a_trace(server):
    errors = _record_errors(server)
    conn = http.client.HTTPConnection(HOST, server.server_address[1], timeout=20)
    conn.request("POST", "/api/state", b"x" * (64 * 1024 + 10))
    r = conn.getresponse()
    r.read()
    assert r.status == 413
    assert r.will_close  # server announced/closed the connection
    conn.close()
    time.sleep(0.3)
    assert errors == []


def test_get_survives_a_dropped_client(server, monkeypatch):
    from quoridor.analyzer import server as srv_mod

    errors = _record_errors(server)

    def boom(self, code, body, ctype):
        raise BrokenPipeError

    monkeypatch.setattr(srv_mod.Handler, "_send", boom)
    with pytest.raises((http.client.HTTPException, OSError)):
        call(server, "GET", "/")
    time.sleep(0.3)
    assert errors == []


def test_deeply_nested_json_is_a_400(server):
    conn = http.client.HTTPConnection(HOST, server.server_address[1], timeout=20)
    conn.request("POST", "/api/state", b"[" * 60000)
    r = conn.getresponse()
    assert r.status == 400 and json.loads(r.read())["ok"] is False
    conn.close()


def test_security_headers(server):
    conn = http.client.HTTPConnection(HOST, server.server_address[1], timeout=20)
    conn.request("GET", "/")
    r = conn.getresponse()
    r.read()
    conn.close()
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")


def test_handler_has_a_socket_timeout():
    from quoridor.analyzer.server import Handler

    assert Handler.timeout and Handler.timeout <= 30
