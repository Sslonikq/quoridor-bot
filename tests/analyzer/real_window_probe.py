"""Real pywebview window probe (run by test_window_real.py in a subprocess, never imported).

Opens the analyzer window on a throw-away server and config, calls the js_api from the page
(set_on_top, set_compact) after `loaded`, and checks that the window stays responsive.
Prints RESULT lines; always destroys the window itself.
"""

import faulthandler
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
faulthandler.enable()
faulthandler.dump_traceback_later(60, exit=True)

import webview  # noqa: E402

from quoridor.analyzer import window as W  # noqa: E402
from quoridor.analyzer.server import AnalyzerServer  # noqa: E402
from quoridor.analyzer.settings import SettingsStore  # noqa: E402

srv = AnalyzerServer(0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
store = SettingsStore(sys.argv[1])
store.load()
box: dict = {}
orig = webview.create_window


def create(*a, **k):
    box["w"] = orig(*a, **k)
    return box["w"]


webview.create_window = create


def call(w, expr):
    w.evaluate_js(
        "window.__r=null;(" + expr + ").then(r=>{window.__r=JSON.stringify(r)},"
        "e=>{window.__r='ERR '+e}); 1"
    )
    for _ in range(40):
        time.sleep(0.25)
        r = w.evaluate_js("window.__r")
        if r is not None:
            return r
    return "TIMEOUT"


def driver():
    while "w" not in box:
        time.sleep(0.05)
    w = box["w"]
    w.events.loaded.wait(20)
    time.sleep(1)
    try:
        print("RESULT pin_on", call(w, "pywebview.api.set_on_top(true)"), flush=True)
        print("RESULT pin_off", call(w, "pywebview.api.set_on_top(false)"), flush=True)
        print(
            "RESULT compact", '"ok":true' in call(w, "pywebview.api.set_compact(true)"), flush=True
        )
        print("RESULT full", '"ok":true' in call(w, "pywebview.api.set_compact(false)"), flush=True)
        w.evaluate_js("document.getElementById('bPin').click(); 1")
        time.sleep(1)
        print("RESULT responsive", w.evaluate_js("1+1"), flush=True)
    finally:
        w.destroy()


threading.Thread(target=driver, daemon=True).start()
try:
    W.run_native(srv.url, store, webview)
finally:
    srv.shutdown()
    srv.server_close()
print("RESULT exited", flush=True)
