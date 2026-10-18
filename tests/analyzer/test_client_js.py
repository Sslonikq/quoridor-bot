"""Client (app.js) run in Node with a stub DOM and a recording fetch (client_harness.js)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
APP_JS = Path(__file__).parents[2] / "quoridor/analyzer/static/app.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = Path(__file__).with_name("client_harness.js")


@pytest.fixture(scope="module")
def result():
    r = subprocess.run(
        [NODE, str(HARNESS), str(APP_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_plain_refresh_analyzes_without_cancel(result):
    assert "/api/analyze" in result["plain"] and "/api/cancel" not in result["plain"]


def test_refresh_cancels_server_when_review_running(result):
    assert "/api/cancel" in result["running"] and "/api/analyze" not in result["running"]


def test_refresh_cancels_server_when_hint_comes_from_review(result):
    assert "/api/cancel" in result["fromReview"] and "/api/analyze" not in result["fromReview"]


def test_refresh_cancels_server_when_hint_is_off(result):
    assert "/api/cancel" in result["hintOff"] and "/api/analyze" not in result["hintOff"]


def test_graph_click_in_branch_stays_at_common_start(result):
    assert result["branchPly"] == 2
    assert result["mainPly"] == 3


def test_default_hint_time_is_5s(result):
    assert result["defaultTime"] == [5]


def test_default_mode_is_normal_and_nothing_stored_at_start(result):
    assert result["initialMode"] == "normal" and result["initialStore"] == 0


def test_deep_mode_requests_20s_and_shows_progress(result):
    assert result["deepTime"] == [20]
    assert result["deepRunning"] and result["deepProgShown"]


def test_position_change_restarts_analysis_in_the_same_mode(result):
    assert result["deepAfterMove"]
    assert result["deepMoveTimes"] == [20]
    # the new /api/analyze cancels the old one on the server itself
    assert "/api/cancel" not in result["deepMoveCalls"]


def test_escape_cancels_the_search_in_any_mode(result):
    assert not result["escLive"] and result["escState"] == "idle"
    assert "/api/cancel" in result["escCalls"] and "/api/analyze" not in result["escCalls"]


def test_click_on_busy_mode_cancels_and_on_idle_mode_recalculates(result):
    assert not result["clickCancel"] and "/api/cancel" in result["clickCalls"]
    assert result["clickRestart"] == [20]


def test_result_is_marked_with_the_mode(result):
    assert not result["doneLive"] and result["doneMark"] == "deep"
    assert "Глубоко, 20 с" in result["doneMsg"]


def test_mode_change_starts_nothing_without_hint_or_during_review(result):
    assert result["modeHintOff"] == 0 and result["modeReview"] == 0


def test_each_mode_sends_its_time_without_a_racing_cancel(result):
    assert result["modeTimes"] == {"fast": [1], "normal": [5]}
    assert "/api/analyze" in result["modeCalls"] and "/api/cancel" not in result["modeCalls"]


def test_live_depth_is_polled_in_every_mode(result):
    assert result["polled"] and "глубина 7" in result["liveMsg"]
    assert "Глубоко" in result["liveMsg"]


def test_mode_is_remembered_in_local_storage(result):
    assert result["stored"] == "normal"  # the last mode chosen before the check
    assert result["reloaded"] == "normal"
    assert result["loadedDeep"] == "deep"
    assert result["loadedBogus"] == "normal" and result["loadedProto"] == "normal"


def test_broken_local_storage_does_not_break_the_mode_switch(result):
    assert result["loadedThrow"] == "normal"
    assert result["modeAfterThrow"] == "fast"


def test_foreign_cancel_of_current_request_is_retried_once(result):
    assert result["raceCalls"] == 2 and result["raceState"] == "done"
    assert result["stuckState"] == "idle"  # a second cancel does not leave "busy" forever
