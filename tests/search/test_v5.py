"""Phase 9, steps 3-4 (v5): combined wall threats (quoridor.threat), the potential in the
eval, root combination walls, repetitions and the game history of SearchBot."""

import itertools

import pytest

from quoridor import bitpaths
from quoridor.bitpaths import GOAL_MASK, bit, build_masks
from quoridor.bots import BOTS, V4_SETTINGS, V5_SETTINGS, SearchBot, _follows, make_bot
from quoridor.rules import apply_move, apply_move_unchecked, is_wall_legal, legal_moves
from quoridor.search import REP_WINDOW, STEP, Searcher
from quoridor.state import WALL_GRID, GameState, H, PawnMove, V, WallMove
from quoridor.threat import combo_threat, fits
from tests.search.test_search_speed import positions

ALL_WALLS = [(x, y, o) for x in range(WALL_GRID) for y in range(WALL_GRID) for o in (H, V)]


@pytest.fixture(scope="module")
def sample():
    return positions([61, 62, 63], every=4, max_plies=80, wall_bias=0.7)


def dist_after(state: GameState, walls, player: int) -> int | None:
    h, v = set(state.h_walls), set(state.v_walls)
    for x, y, o in walls:
        (h if o == H else v).add((x, y))
    return bitpaths.distance(build_masks(h, v), bit(state.pawns[player]), GOAL_MASK[player])


def test_single_wall_is_the_exact_best(sample):
    """k = 1: the beam sees every wall that lengthens the path (all block a DAG edge)."""
    for s in sample[:40]:
        m = build_masks(s.h_walls, s.v_walls)
        for p in (0, 1):
            d0, ds, ws = combo_threat(m, s.h_walls, s.v_walls, s.pawns[p], p, 1)
            best = d0
            for w in ALL_WALLS:
                if fits(s.h_walls, s.v_walls, w):
                    d = dist_after(s, [w], p)
                    if d is not None:
                        best = max(best, d)
            assert (ds[0] if ds else d0) == best


def test_sets_are_placeable_and_distances_exact(sample):
    for s in sample[:40]:
        m = build_masks(s.h_walls, s.v_walls)
        for p in (0, 1):
            d0, ds, ws = combo_threat(m, s.h_walls, s.v_walls, s.pawns[p], p, 4)
            assert list(ds) == sorted(ds) and all(d >= d0 for d in ds)
            for j, (d, walls) in enumerate(zip(ds, ws, strict=True)):
                assert len(walls) <= j + 1
                h, v = set(s.h_walls), set(s.v_walls)
                for w in walls:  # placeable one after another
                    assert fits(h, v, w)
                    (h if w[2] == H else v).add((w[0], w[1]))
                assert dist_after(s, walls, p) == d


def test_pair_beats_greedy_somewhere(sample):
    """The point of the beam: two walls together can add more than the best single wall
    plus the best wall after it (a cage), and the beam finds that on real positions."""
    found = 0
    for s in sample:
        m = build_masks(s.h_walls, s.v_walls)
        for p in (0, 1):
            d0, ds, ws = combo_threat(m, s.h_walls, s.v_walls, s.pawns[p], p, 2, beam=4)
            if len(ds) < 2:
                continue
            g1 = ds[0]
            # greedy: the best wall, then the best wall after it
            h, v = set(s.h_walls), set(s.v_walls)
            w1 = ws[0][0]
            (h if w1[2] == H else v).add((w1[0], w1[1]))
            m1 = build_masks(h, v)
            _, ds1, _ = combo_threat(m1, frozenset(h), frozenset(v), s.pawns[p], p, 1)
            greedy = ds1[0] if ds1 else g1
            assert ds[1] >= g1
            found += ds[1] > greedy
    assert found > 0


def test_pair_not_worse_than_brute_force_too_much():
    """On a small open position the beam pair equals the brute-force best pair."""
    s = GameState(((4, 6), (4, 8)), frozenset(), frozenset(), (10, 10), 0)
    m = build_masks((), ())
    d0, ds, _ = combo_threat(m, s.h_walls, s.v_walls, s.pawns[0], 0, 2, beam=8)
    best = d0
    cand = [w for w in ALL_WALLS if w[1] >= 4]  # walls near the pawn's way to row 9
    for a, b in itertools.combinations(cand, 2):
        if not fits(set(), set(), a):
            continue
        h, v = set(), set()
        (h if a[2] == H else v).add(a[:2])
        if not fits(h, v, b):
            continue
        d = dist_after(s, [a, b], 0)
        if d is not None:
            best = max(best, d)
    assert ds[1] == best


def test_potential_and_combo_term(sample):
    se = Searcher(0, **{**V4_SETTINGS, "combo": 100, "combo_k": 3})
    s = sample[5]
    se._reset(s)
    me = s.turn
    d_me = se.dist(s, me)
    pot = se.potential(s.pawns[me], me, s.walls_left[1 - me], d_me)
    d0, ds, _ = se.combo_of(*se._root_walls, s.pawns[me], me)
    n = min(s.walls_left[1 - me], len(ds))
    want = max([0] + [(ds[j] - d0) * STEP - (j + 1) * se.combo_cost for j in range(n)])
    assert pot == want
    # the walls placed in the tree already took part of it
    assert se.potential(s.pawns[me], me, s.walls_left[1 - me], d_me + 1) == max(0, want - STEP)
    assert se.potential(s.pawns[me], me, 0, d_me) == 0


def test_combo_stock_caps_useless_walls():
    """game03 move 17: white's 7 walls can add 2 steps at most (black is in a one-cell
    corridor); v4 counts them as 7 * 1.25 steps, the capped stock does not."""
    from tests.positions.loader import state_at

    s = state_at("game03", 32)
    v4 = Searcher(0, **V4_SETTINGS)
    capped = Searcher(0, **{**V4_SETTINGS, "combo_stock": 25, "combo_k": 10})
    v4._reset(s)
    capped._reset(s)
    assert v4.evaluate(s) > 0  # the old misjudgement
    assert capped.evaluate(s) < 0


def test_combo_root_walls_are_legal(sample):
    se = Searcher(0, **{**V4_SETTINGS, "combo": 50, "combo_root": 12})
    for s in sample[:30]:
        se._reset(s)
        have = {m for m, _ in se.ordered_moves(s)}
        extra = se._combo_walls(s, have)
        assert len(extra) <= 12
        for w, child in extra:
            assert w not in have
            assert is_wall_legal(s, w)
            assert child == apply_move_unchecked(s, w)


def test_v4_ignores_history(sample):
    """The v5 options are off for v4: the history changes nothing (A/B, report)."""
    for i, s in enumerate(sample[:12]):
        a = Searcher(0, **V4_SETTINGS)
        b = Searcher(0, **V4_SETTINGS)
        a.set_history(sample[max(0, i - 6) : i])
        ra, rb = a.search(s, max_depth=3), b.search(s, max_depth=3)
        assert (ra.move, ra.score, ra.nodes) == (rb.move, rb.score, rb.nodes)


def test_repeated_position_scores_zero():
    s0 = GameState.initial()
    s1 = apply_move(s0, PawnMove(4, 1))  # e2
    s2 = apply_move(s1, PawnMove(4, 7))  # e8
    se = Searcher(0, **{**V4_SETTINGS, "rep": 1})
    # root s2 (white to move); e1 leads to a position seen before? no: black is on e8.
    # Put the child of e2 -> e1 into the history instead.
    child = apply_move(s2, PawnMove(4, 0))
    se.set_history([s0, s1, child])
    rs = se.search_root(s2, max_depth=1, moves=[PawnMove(4, 0), PawnMove(4, 2)])
    by = {r.move: r.score for r in rs.scores}
    assert by[PawnMove(4, 0)] == 0
    plain = Searcher(0, **V4_SETTINGS).search_root(s2, max_depth=1, moves=[PawnMove(4, 0)])
    assert plain.scores[0].score != 0


def test_root_penalty_counts_recent_own_positions():
    s0 = GameState.initial()
    states = [s0]
    for mv in [PawnMove(4, 1), PawnMove(4, 7), PawnMove(3, 1), PawnMove(4, 6)]:
        states.append(apply_move(states[-1], mv))
    root = states[-1]  # white on d2 to move; white stood on e2 before
    se = Searcher(0, **{**V4_SETTINGS, "rep_penalty": 30})
    se.set_history(states[:-1])
    kids = [(m, apply_move_unchecked(root, m)) for m in (PawnMove(4, 1), PawnMove(2, 1))]
    assert se._root_penalties(root, kids) == [30, 0]
    assert REP_WINDOW >= 2


def test_follows():
    s0 = GameState.initial()
    s1 = apply_move(s0, PawnMove(4, 1))
    s2 = apply_move(s1, WallMove(3, 3, H))
    assert _follows(s1, s2)
    assert not _follows(s0, s2)  # two moves
    assert not _follows(s1, s0)
    assert not _follows(s2, s0)


def test_searchbot_records_the_game():
    a, b = make_bot("v5", 1), make_bot("greedy", 2)
    assert isinstance(a, SearchBot)
    a.depth = 1
    s = GameState.initial()
    for _ in range(6):
        bot = a if s.turn == 0 else b
        s = apply_move(s, bot.choose_move(s))
    # 3 own moves: before the 3rd one the bot knew 4 earlier positions
    assert len(a._history) == 5
    a.choose_move(GameState.initial())  # a new game: the history starts again
    assert len(a._history) == 1


def test_v5_registry_and_settings():
    assert {"v5", "v5-clock", "v5-0.2s", "v5-3s"} <= set(BOTS)
    for k, v in V4_SETTINGS.items():
        assert V5_SETTINGS[k] == v
    with pytest.raises(TypeError):
        SearchBot(0, depth=1, no_such_option=1)


def test_v5_plays_legal_moves(sample):
    bot = make_bot("v5", 0)
    for s in sample[:6]:
        mv = bot.choose_move(s)
        assert mv in legal_moves(s)


def test_race_options_default_to_the_v4_constants(sample):
    """race_plan / race_soft (step 7, tuning): the defaults are eval.PLAN_V4 / SOFT_V4, so
    v4 and v5 score every position as before; SearchBot passes them to the Searcher."""
    from quoridor.eval import PLAN_V4, SOFT_V4

    base = Searcher(0, **V5_SETTINGS)
    explicit = Searcher(0, race_plan=PLAN_V4, race_soft=SOFT_V4, **V5_SETTINGS)
    assert (base.race_plan, base.race_soft) == (PLAN_V4, SOFT_V4)
    for s in sample:
        assert base.evaluate(s) == explicit.evaluate(s)
    bot = SearchBot(0, depth=1, race_plan=7, race_soft=0)
    assert (bot._searcher.race_plan, bot._searcher.race_soft) == (7, 0)
