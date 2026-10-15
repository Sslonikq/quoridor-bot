"""`python -m quoridor.analyzer --no-browser` as a real subprocess (slow)."""

import http.client
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow
ROOT = Path(__file__).parents[2]


def test_analyzer_process_starts_serves_and_stops():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "quoridor.analyzer", "--no-browser"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        line = proc.stdout.readline().decode("utf-8")  # blocks until the server is up
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        assert m, line
        port = int(m.group(1))
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("GET", "/")
        r = conn.getresponse()
        assert r.status == 200 and b"<html" in r.read().lower()
        conn.request("POST", "/api/state", json.dumps({"moves": ["e2"]}), {})
        r = conn.getresponse()
        assert r.status == 200 and [
            m["name"] for m in json.loads(r.read())["position"]["pawns"]
        ] == ["e2", "e9"]
        conn.close()
        assert proc.poll() is None
    finally:
        proc.terminate()  # our own PID only
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
        proc.stdout.close()
    assert proc.poll() is not None
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/")


def test_analyzer_process_with_a_busy_port_fails_cleanly():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        res = subprocess.run(
            [sys.executable, "-m", "quoridor.analyzer", "--no-browser", "--port", str(port)],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
        )
    assert res.returncode != 0
