"""Window block of app.js (phase 12) in Node: window_harness.js with a mock pywebview api."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
APP_JS = Path(__file__).parents[2] / "quoridor/analyzer/static/app.js"
HARNESS = Path(__file__).with_name("window_harness.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

NOTE_MISSING = "pywebview не установлен"
NOTE_ERROR = "Окно не открылось"
NOTE_BROWSER = "--browser"


def run(**cfg):
    r = subprocess.run(
        [NODE, str(HARNESS), str(APP_JS), json.dumps(cfg)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def names(snap):
    return [c[0] for c in snap["calls"]]


# --- browser mode ---


def test_pin_hidden_in_browser_after_timer():
    s = run(script=["wait:700", "snap:a"])["snaps"]["a"]
    assert s["pinHidden"] is True
    assert s["calls"] == []


def test_browser_without_nowin_has_no_note():
    s = run(script=["wait:700", "snap:a"])["snaps"]["a"]
    assert s["noteHidden"] is True and s["noteText"] == ""


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ("?nowin=missing", NOTE_MISSING),
        ("?nowin=error", NOTE_ERROR),
        ("?nowin=browser", NOTE_BROWSER),
        ("?nowin=whatever", NOTE_MISSING),
        ("?nowin=", NOTE_MISSING),
        ("?nowin=constructor", NOTE_MISSING),
    ],
)
def test_note_text_by_reason(query, fragment):
    s = run(search=query, script=["wait:700", "snap:a"])["snaps"]["a"]
    assert s["noteHidden"] is False
    assert fragment in s["noteText"]
    assert s["pinHidden"] is True


def test_three_reason_texts_are_different():
    texts = {
        q: run(search=f"?nowin={q}", script=["wait:700", "snap:a"])["snaps"]["a"]["noteText"]
        for q in ("missing", "error", "browser")
    }
    assert len(set(texts.values())) == 3


def test_other_query_params_do_not_show_note():
    s = run(search="?x=1", script=["wait:700", "snap:a"])["snaps"]["a"]
    assert s["noteHidden"] is True


# --- late pywebviewready ---


def test_late_pywebviewready_switches_browser_to_window_mode():
    r = run(
        search="?nowin=missing",
        settings={"on_top": True, "compact": True},
        script=["wait:700", "snap:before", "late", "snap:after"],
    )
    before, after = r["snaps"]["before"], r["snaps"]["after"]
    assert before["pinHidden"] is True and before["noteHidden"] is False
    assert before["compact"] is False and before["calls"] == []
    assert after["pinHidden"] is False
    assert after["noteHidden"] is True
    assert after["pinPressed"] == "true"
    assert after["compact"] is True and after["compactPressed"] == "true"
    assert names(after) == ["get_settings"]


def test_ready_event_before_timer_uses_window_without_double_init():
    r = run(settings={"on_top": False, "compact": False}, script=["late", "wait:700", "snap:a"])
    s = r["snaps"]["a"]
    assert s["pinHidden"] is False and s["noteHidden"] is True
    assert names(s) == ["get_settings"]  # the timer sees native and does not init again


def test_ready_event_registered_in_browser():
    assert run(script=["snap:a"])["readyHandlers"] == 1


def test_ready_with_failing_get_settings_stays_in_browser_mode():
    r = run(
        search="?nowin=error",
        getSettingsThrows=True,
        script=["wait:700", "late", "snap:a"],
    )
    s = r["snaps"]["a"]
    assert s["pinHidden"] is True and s["noteHidden"] is False
    assert NOTE_ERROR in s["noteText"]


# --- window already there at load ---


def test_window_at_start_takes_settings_from_api():
    r = run(native=True, settings={"on_top": True, "compact": True}, script=["wait:100", "snap:a"])
    s = r["snaps"]["a"]
    assert s["pinHidden"] is False and s["noteHidden"] is True
    assert s["pinPressed"] == "true" and s["compact"] is True
    assert s["stored"] is None  # the window mode does not touch localStorage
    assert r["readyHandlers"] == 0


def test_window_at_start_defaults_off():
    s = run(native=True, script=["wait:100", "snap:a"])["snaps"]["a"]
    assert s["pinPressed"] == "false" and s["compact"] is False


def test_nowin_ignored_when_window_exists():
    s = run(native=True, search="?nowin=error", script=["wait:100", "snap:a"])["snaps"]["a"]
    assert s["noteHidden"] is True


# --- compact: key C and button ---


def test_key_c_toggles_compact_in_browser_and_stores_it():
    r = run(script=["wait:700", "key:C", "snap:on", "key:c", "snap:off"])["snaps"]
    assert (
        r["on"]["compact"] is True
        and r["on"]["compactPressed"] == "true"
        and r["on"]["stored"] == "1"
    )
    assert r["off"]["compact"] is False and r["off"]["stored"] == "0"
    assert r["off"]["calls"] == []


def test_key_c_cyrillic_layout():
    r = run(script=["wait:700", "key:с", "snap:a"])["snaps"]["a"]
    assert r["compact"] is True


def test_compact_button_in_browser():
    r = run(script=["wait:700", "click:bCompact", "snap:a"])["snaps"]["a"]
    assert r["compact"] is True and r["stored"] == "1"


def test_browser_restores_compact_from_storage():
    s = run(storedCompact="1", script=["wait:700", "snap:a"])["snaps"]["a"]
    assert s["compact"] is True and s["compactPressed"] == "true"


def test_compact_button_in_window_calls_api_not_storage():
    r = run(
        native=True, script=["wait:100", "click:bCompact", "snap:on", "click:bCompact", "snap:off"]
    )["snaps"]
    assert ["set_compact", True] in r["on"]["calls"] and r["on"]["compact"] is True
    assert ["set_compact", False] in r["off"]["calls"] and r["off"]["compact"] is False
    assert r["off"]["stored"] is None


def test_key_c_in_window_calls_api():
    r = run(native=True, script=["wait:100", "key:C", "snap:a"])["snaps"]["a"]
    assert ["set_compact", True] in r["calls"] and r["compact"] is True


# --- pin button ---


def test_pin_button_calls_api_and_toggles():
    r = run(native=True, script=["wait:100", "click:bPin", "snap:on", "click:bPin", "snap:off"])[
        "snaps"
    ]
    assert ["set_on_top", True] in r["on"]["calls"] and r["on"]["pinPressed"] == "true"
    assert ["set_on_top", False] in r["off"]["calls"] and r["off"]["pinPressed"] == "false"


def test_pin_button_unpins_when_settings_say_on_top():
    r = run(native=True, settings={"on_top": True}, script=["wait:100", "click:bPin", "snap:a"])[
        "snaps"
    ]["a"]
    assert ["set_on_top", False] in r["calls"] and r["pinPressed"] == "false"


def test_pin_button_noop_without_window():
    r = run(script=["wait:700", "click:bPin", "snap:a"])["snaps"]["a"]
    assert r["calls"] == [] and r["pinPressed"] == "false"


def test_pin_button_works_after_late_ready():
    r = run(script=["wait:700", "late", "click:bPin", "snap:a"])["snaps"]["a"]
    assert ["set_on_top", True] in r["calls"] and r["pinPressed"] == "true"


def test_pin_api_error_keeps_state():
    r = run(native=True, setOnTopThrows=True, script=["wait:100", "click:bPin", "snap:a"])["snaps"][
        "a"
    ]
    assert r["pinPressed"] == "false"


def test_late_ready_window_settings_replace_stored_compact():
    """Browser mode restored compact=1 from localStorage; a late pywebviewready then takes
    the compact flag from analyzer.json (the window is the source of truth)."""
    r = run(
        storedCompact="1",
        settings={"on_top": False, "compact": False},
        script=["wait:700", "snap:before", "late", "snap:after"],
    )["snaps"]
    assert r["before"]["compact"] is True
    assert r["after"]["compact"] is False and r["after"]["compactPressed"] == "false"
