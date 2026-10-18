"""Phase 9, step 6: the numba kernels (quoridor.kernels) equal the pure-Python reference.

With numba on, every hot function is compared with its Python reference on random
positions of legal play: bitpaths (distance, player_distance, shortest_layers,
shortest_path), the wall sets recovered from the masks, threat.combo_threat,
Searcher._race_cands and Searcher.candidate_walls. Without numba (not installed, or
QUORIDOR_NO_NUMBA=1) these tests are skipped and the rest of the suite runs on the
reference code; test_no_numba_env_switch checks that switch in a child process.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from quoridor import bitpaths, jit, search, threat
from quoridor.bitpaths import GOAL_MASK, bit, build_masks
from quoridor.search import Searcher
from quoridor.state import BOARD_SIZE, WALL_GRID, GameState, H
from tests.playouts import random_game

ROOT = Path(__file__).resolve().parents[2]
needs_numba = pytest.mark.skipif(not jit.ENABLED, reason=f"numba off: {jit.REASON}")


def _numba_importable() -> bool:
    try:
        import numba  # noqa: F401
    except Exception:
        return False
    return True


def sample_states(seeds=range(500, 512), every=2, max_plies=90):
    out = []
    for i, seed in enumerate(seeds):
        bias = (0.4, 0.6, 0.8, 0.95)[i % 4]
        for j, s in enumerate(random_game(seed, max_plies=max_plies, wall_bias=bias)):
            if j % every == 0:
                out.append(s)
    return out


@pytest.fixture(scope="module")
def states():
    return sample_states()


def test_enabled_when_numba_is_installed():
    """numba installed and not switched off -> the kernels are in use (a failure to
    compile them must not pass silently)."""
    if os.environ.get(jit.DISABLE_ENV, "") not in ("", "0") or not _numba_importable():
        pytest.skip("numba off on purpose / not installed")
    assert jit.ENABLED, jit.REASON
    assert bitpaths._K is not None and search._K is not None
    assert bitpaths.distance is bitpaths._jit_distance
    assert threat.combo_threat is threat._jit_combo_threat


def test_identity_decorator_without_numba():
    f = jit._identity(lambda x: x + 1)
    g = jit._identity("int64(int64)", cache=True)(lambda x: x + 2)
    h = jit._identity(cache=True)(lambda x: x + 3)
    assert (f(1), g(1), h(1)) == (2, 3, 4)


@pytest.mark.slow
def test_no_numba_env_switch():
    """QUORIDOR_NO_NUMBA=1: pure Python everywhere, and the same search result."""
    code = (
        "from quoridor import jit, bitpaths, search, threat\n"
        "from quoridor.bots import V5_SETTINGS\n"
        "from tests.playouts import random_game\n"
        "s = list(random_game(7, max_plies=24, wall_bias=0.6))[-1]\n"
        "r = search.Searcher(0, **V5_SETTINGS).search(s, max_depth=3)\n"
        "print(jit.ENABLED, bitpaths._K is None, search._K is None,\n"
        "      bitpaths.distance is bitpaths.py_distance,\n"
        "      threat.combo_threat is threat.py_combo_threat, r.move, r.score, r.nodes)\n"
    )
    outs = []
    for off in ("1", "0"):
        env = {**os.environ, jit.DISABLE_ENV: off, "PYTHONPATH": str(ROOT)}
        p = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
        )
        assert p.returncode == 0, p.stderr
        outs.append(p.stdout.split())
    off, on = outs
    assert off[:5] == ["False", "True", "True", "True", "True"]
    assert off[5:] == on[5:]  # same move, score and nodes
    if _numba_importable():
        assert on[:5] == ["True", "False", "False", "False", "False"]


@needs_numba
def test_distance_layers_path_equal_reference(states):
    rng = random.Random(1)
    n = 0
    for s in states:
        m = build_masks(s.h_walls, s.v_walls)
        cells = [s.pawns[0], s.pawns[1]] + [
            (rng.randrange(BOARD_SIZE), rng.randrange(BOARD_SIZE)) for _ in range(4)
        ]
        for cell in cells:
            for p in (0, 1):
                start, goal = bit(cell), GOAL_MASK[p]
                assert bitpaths.distance(m, start, goal) == bitpaths.py_distance(m, start, goal)
                assert bitpaths.player_distance(m, cell, p) == bitpaths.py_player_distance(
                    m, cell, p
                )
                assert bitpaths.shortest_layers(m, start, goal) == bitpaths.py_shortest_layers(
                    m, start, goal
                )
                assert bitpaths.shortest_path(m, cell, p) == bitpaths.py_shortest_path(m, cell, p)
                n += 1
        # arbitrary start / goal sets (not just a goal row)
        a = rng.getrandbits(81) & rng.getrandbits(81) & rng.getrandbits(81)
        b = rng.getrandbits(81) & rng.getrandbits(81) & rng.getrandbits(81)
        if a and b:
            assert bitpaths.distance(m, a, b) == bitpaths.py_distance(m, a, b)
            assert bitpaths.shortest_layers(m, a, b) == bitpaths.py_shortest_layers(m, a, b)
    assert n > 5000


@needs_numba
def test_walls_from_masks(states):
    """The kernels read the wall sets off the masks: exact for legal positions."""
    from quoridor import kernels

    for s in states:
        hb, vb = kernels._walls_of(bitpaths.m8(build_masks(s.h_walls, s.v_walls)))
        h = {(i % 8, i // 8) for i in range(64) if int(hb) >> i & 1}
        v = {(i % 8, i // 8) for i in range(64) if int(vb) >> i & 1}
        assert (h, v) == (set(s.h_walls), set(s.v_walls))


@needs_numba
@pytest.mark.parametrize(("k", "beam", "narrow"), [(1, 1, 0), (2, 3, 0), (3, 3, 3), (4, 4, 0)])
def test_combo_threat_equals_reference(states, k, beam, narrow):
    for s in states[::2]:
        m = build_masks(s.h_walls, s.v_walls)
        for p in (0, 1):
            args = (m, s.h_walls, s.v_walls, s.pawns[p], p, k, beam, narrow)
            assert threat.combo_threat(*args) == threat.py_combo_threat(*args), (s, p)


@needs_numba
def test_race_cands_equal_reference(states, monkeypatch):
    fast, slow = Searcher(0), Searcher(0)
    for s in states:
        m = build_masks(s.h_walls, s.v_walls)
        for b in (0, 1):
            got = fast._race_cands(m, s.h_walls, s.v_walls, s.pawns[b], b)
            monkeypatch.setattr(search, "_K", None)
            want = slow._race_cands(m, s.h_walls, s.v_walls, s.pawns[b], b)
            monkeypatch.setattr(search, "_K", bitpaths._K)
            assert got == want, (s, b)


@needs_numba
@pytest.mark.parametrize("wall_limit", [1, 2, 10, 40])
def test_candidate_walls_equal_reference(states, monkeypatch, wall_limit):
    for s in states:
        for turn in (0, 1):
            t = GameState(s.pawns, s.h_walls, s.v_walls, s.walls_left, turn)
            got = Searcher(0, wall_limit=wall_limit).candidate_walls(t)
            monkeypatch.setattr(search, "_K", None)
            want = Searcher(0, wall_limit=wall_limit).candidate_walls(t)
            monkeypatch.setattr(search, "_K", bitpaths._K)
            assert got == want, t


@needs_numba
def test_candidate_walls_prime_the_distance_memo(states):
    """The kernel's distances after each kept wall equal fresh BFS results."""
    for s in states[::5]:
        sr = Searcher(0)
        sr.candidate_walls(s)
        for (m, cell, p), d in sr._dist_cache.items():
            assert bitpaths.py_player_distance(m, cell, p) == d


@needs_numba
def test_wall_code_tables():
    for code, (x, y, o) in enumerate(bitpaths.WALL_OF_CODE):
        assert 0 <= x < WALL_GRID and 0 <= y < WALL_GRID
        assert code == (y * 8 + x) * 2 + (0 if o == H else 1)
        not_masks = bitpaths.WALL_NOT_OF_CODE[code][3:]
        assert tuple(~b for b in bitpaths.wall_bits(x, y, o)) == not_masks


def test_pawn_moves_from_masks_equal_rules(states):
    """search.pawn_moves_m (the search's pawn moves, pure Python on the masks) gives
    rules.pawn_moves_for exactly, order included; pawns also put next to each other."""
    from quoridor.rules import pawn_moves_for
    from quoridor.search import pawn_moves_m

    rng = random.Random(3)
    n_adjacent = 0
    for s in states:
        m = build_masks(s.h_walls, s.v_walls)
        trials = [s.pawns]
        for _ in range(6):
            a = (rng.randrange(BOARD_SIZE), rng.randrange(BOARD_SIZE))
            dc, dr = rng.choice(((0, 1), (0, -1), (1, 0), (-1, 0)))
            b = (a[0] + dc, a[1] + dr)
            if 0 <= b[0] < BOARD_SIZE and 0 <= b[1] < BOARD_SIZE:
                trials.append((a, b))
                n_adjacent += 1
        for pawns in trials:
            t = GameState(pawns, s.h_walls, s.v_walls, s.walls_left, s.turn)
            for p in (0, 1):
                assert pawn_moves_m(m, pawns[p], pawns[1 - p]) == pawn_moves_for(t, p), (t, p)
    assert n_adjacent > 1000
