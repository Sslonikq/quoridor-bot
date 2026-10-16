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


def test_deep_analysis_requests_20s_and_shows_progress(result):
    assert result["deepTime"] == [20]
    assert result["deepRunning"] and result["deepProgShown"]


def test_deep_analysis_cancelled_by_position_change(result):
    assert not result["deepAfterMove"]
    assert "/api/analyze" in result["deepMoveCalls"]  # the normal hint for the new position


def test_escape_cancels_deep_analysis_and_restores_the_normal_hint(result):
    assert not result["escDeep"]
    assert "/api/cancel" in result["escCalls"]
    assert result["escTimes"] == [5]


def test_second_click_cancels_deep_analysis(result):
    assert not result["clickCancel"] and "/api/cancel" in result["clickCalls"]


def test_deep_result_is_marked(result):
    assert not result["doneDeep"] and result["doneMark"] == 20
    assert "Глубокий, 20 с" in result["doneMsg"]


def test_no_deep_analysis_without_hint_or_during_review(result):
    assert not result["deepHintOff"] and not result["deepReview"]
