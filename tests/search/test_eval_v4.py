"""Phase 8, v4 (report 0061): wall race eval, shortest-path layers, PVS / LMR, the choice in
a proven-lost position, registry."""

import pytest

from quoridor import bitpaths
from quoridor.bitpaths import GOAL_MASK, bit, build_masks
from quoridor.bots import BOTS, V4_SETTINGS, make_bot
from quoridor.eval import (
    RACE,
    STEP,
    THREAT_V4,
    WALL_V3,
    evaluate_dists,
    evaluate_v4,
    reserve_term,
)
from quoridor.notation import format_move
from quoridor.paths import shortest_path_length
from quoridor.rules import apply_move_unchecked, legal_moves, pawn_moves, wall_fits
from quoridor.search import V2_TT_BITS, Searcher, is_win_score
from quoridor.state import WALL_GRID, GameState, WallMove
from tests.search.test_eval_v3 import mirror
from tests.search.test_search_speed import positions

V3 = {"tt_bits": V2_TT_BITS, "wall_weight": WALL_V3}


@pytest.fixture(scope="module")
def sample():
    return positions([51, 52, 53, 54], every=3, max_plies=100, wall_bias=0.7)


def with_stock(s: GameState, walls_left: tuple[int, int]) -> GameState:
    return GameState(s.pawns, s.h_walls, s.v_walls, walls_left, s.turn)


def race_sample(sample):
    """The sample positions with only one side holding walls (both ways round)."""
    out = []
    for s in sample:
        out += [with_stock(s, (4, 0)), with_stock(s, (0, 4)), with_stock(s, (9, 0))]
    return out


# ---------- shortest-path layers (bitpaths) ----------


def test_shortest_layers_are_the_cells_on_shortest_paths(sample):
    for s in sample[:40]:
        m = build_masks(s.h_walls, s.v_walls)
        for p in (0, 1):
            layers = bitpaths.shortest_layers(m, bit(s.pawns[p]), GOAL_MASK[p])
            d = shortest_path_length(s, p)
            assert layers is not None and len(layers) == d + 1
            for k, layer in enumerate(layers):
                for i in range(81):
                    cell = (i % 9, i // 9)
                    # on a shortest path at step k <=> k from the start and d - k to go
                    fwd = bitpaths.distance(m, bit(s.pawns[p]), bit(cell))
                    on = fwd == k and bitpaths.player_distance(m, cell, p) == d - k
                    assert bool(layer >> i & 1) == on, (s, p, k, cell)


# ---------- wall race ----------


def brute_lengthening(s: GameState, b: int) -> dict[tuple, int]:
    """Every wall that fits and lengthens player b's path -> b's new distance."""
    st = GameState(s.pawns, s.h_walls, s.v_walls, (5, 5), 1 - b)
    d = shortest_path_length(st, b)
    out = {}
    for x in range(WALL_GRID):
        for y in range(WALL_GRID):
            for o in "hv":
                w = WallMove(x, y, o)
                if wall_fits(st, w):
                    nb = shortest_path_length(apply_move_unchecked(st, w), b)
                    if nb is not None and nb > d:
                        out[(x, y, o)] = nb
    return out


def test_race_candidates_are_exactly_the_lengthening_walls(sample):
    for s in sample[::2]:
        sr = Searcher(0, **V4_SETTINGS)
        for b in (0, 1):
            d, cands = sr._race_cands(sr.masks(s), set(s.h_walls), set(s.v_walls), s.pawns[b], b)
            assert d == shortest_path_length(s, b)
            assert {w: nb for nb, w in cands} == brute_lengthening(s, b)
            assert [nb for nb, _ in cands] == sorted((nb for nb, _ in cands), reverse=True)


def test_wall_race_plan_is_real(sample):
    """Replaying the plan's walls one by one gives B's distance; each adds >= 2 steps."""
    for s in race_sample(sample)[::3]:
        a = 0 if s.walls_left[0] else 1
        b = 1 - a
        sr = Searcher(0, **V4_SETTINGS)
        sr._reset(s)
        e_a, e_b = sr.wall_race(s, a)
        d_a, d_b = shortest_path_length(s, a), shortest_path_length(s, b)
        used = e_a - d_a
        assert 0 <= used <= s.walls_left[a] and e_b >= d_b + 2 * used
        # greedy step by step with the brute force: the same distances
        cur = s
        for _ in range(used):
            cands = brute_lengthening(cur, b)
            best = max(cands.values())
            assert best - shortest_path_length(cur, b) >= 2
            x, y, o = min(
                (w for w, nb in cands.items() if nb == best),
                key=lambda w: (w[0], w[1] if b == 0 else WALL_GRID - 1 - w[1], w[2]),
            )
            h = cur.h_walls | {(x, y)} if o == "h" else cur.h_walls
            v = cur.v_walls | {(x, y)} if o == "v" else cur.v_walls
            cur = GameState(cur.pawns, h, v, cur.walls_left, cur.turn)
        assert shortest_path_length(cur, b) == e_b


def test_v4_eval_is_colour_symmetric(sample):
    for s in sample + race_sample(sample):
        a, b = Searcher(0, **V4_SETTINGS), Searcher(0, **V4_SETTINGS)
        assert a.evaluate(s) == b.evaluate(mirror(s))


def test_v4_eval_while_both_sides_have_walls_is_v3_plus_the_threat_term(sample):
    """Both sides with walls (or none): v3 + THREAT_V4 x (the most one wall of ours adds to
    the opponent's path - the most one of theirs adds to ours), by brute force; plus the
    reserve term of report 0066 (eval.reserve_term, tested in test_eval_reserve.py)."""
    for s in sample:
        me = s.turn
        res = reserve_term(s.walls_left[me], s.walls_left[1 - me], V4_SETTINGS["reserve"])
        v3 = Searcher(0, **V3).evaluate(s) + res
        if max(s.walls_left) == 0:
            assert Searcher(0, **V4_SETTINGS).evaluate(s) == v3
        elif min(s.walls_left) > 0:
            me = s.turn
            d_op, d_me = shortest_path_length(s, 1 - me), shortest_path_length(s, me)
            g_me = max(brute_lengthening(s, 1 - me).values(), default=d_op) - d_op
            g_op = max(brute_lengthening(s, me).values(), default=d_me) - d_me
            want = v3 + THREAT_V4 * (g_me - g_op)
            assert Searcher(0, **V4_SETTINGS).evaluate(s) == want, s
            assert Searcher(0, **dict(V4_SETTINGS, threat=0)).evaluate(s) == v3


def test_v4_eval_terms():
    # both sides have walls / none: v3
    assert evaluate_v4(7, 5, 3, 2, 7, 5) == evaluate_dists(7, 5, 3, 2, wall_weight=WALL_V3)
    assert evaluate_v4(5, 5, 0, 0, 5, 5) == RACE
    # the pure race is won: RACE, no plan needed
    assert evaluate_v4(5, 6, 2, 0, 5, 6) == STEP + 2 * WALL_V3 + RACE
    # we are A, 3 steps behind; the plan (2 walls) adds 6 to the opponent: race won
    base = evaluate_dists(9, 6, 4, 0, wall_weight=WALL_V3)
    v = evaluate_v4(9, 6, 4, 0, 11, 12, plan=50, soft=400)
    assert v == base + 50 * (6 - 2) + 400
    # the same from the other side (the opponent is A): exactly the negative
    assert evaluate_v4(6, 9, 0, 4, 12, 11, plan=50, soft=400) == -v


# ---------- PVS / LMR ----------


@pytest.mark.parametrize("depth", [2, 3, 4])
def test_pvs_keeps_the_exact_score(sample, depth):
    for s in sample[::5]:
        a = Searcher(0, **V3).search(s, max_depth=depth)
        b = Searcher(0, **V3, pvs=1).search(s, max_depth=depth)
        assert a.score == b.score


def _won_samples():
    """Positions where v4 without LMR proves a win for the side to move at depth 4."""
    from tests.search.test_search_edge import sample_positions

    out = []
    for s in sample_positions(range(40, 56), every=3, max_plies=120, wall_bias=0.5):
        # a win within 4 plies takes 2 own moves: path <= 3 even with a jump (speed)
        if shortest_path_length(s, s.turn) > 3:
            continue
        r = Searcher(0, **dict(V4_SETTINGS, lmr=0)).search(s, max_depth=4)
        if is_win_score(r.score) and r.score > 0:
            out.append(s)
    return out


def test_lmr_needs_pvs_and_still_finds_wins(sample):
    """Review 0064 minor 4: LMR is off without PVS; with it, every win that v4 without
    LMR proves at depth 4 is still proven (at most one ply deeper, the reduction)."""
    assert Searcher(0, lmr=3).lmr == 0
    sr = Searcher(0, **V4_SETTINGS)
    for s in sample[::7]:
        r = sr.search(s, max_depth=4)
        assert r.move in legal_moves(s)
    won = _won_samples()
    assert len(won) >= 3
    for s in won:
        r = Searcher(0, **V4_SETTINGS).search(s, max_depth=5)
        assert is_win_score(r.score) and r.score > 0, s


def test_lmr_fail_high_is_researched_at_full_depth(sample):
    """Review 0064 minor 4: a reduced null-window search that fails high for the parent
    (the late move looks better than alpha) is searched again one ply deeper, with the
    full window, before the parent moves on."""
    calls: list[tuple] = []  # (parent call no., child, depth, reduced, failed high)
    stack: list[tuple[int, int]] = []  # (call no., depth) of the open _negamax calls
    sr = Searcher(0, **V4_SETTINGS)
    inner = sr._negamax

    def spy(state, depth, alpha, beta, ply, key):
        parent = stack[-1] if stack else (-1, depth + 1)
        no = len(calls)
        calls.append(None)
        stack.append((no, depth))
        try:
            ret = inner(state, depth, alpha, beta, ply, key)
        finally:
            stack.pop()
        reduced = depth == parent[1] - 2 and beta == alpha + 1
        calls[no] = (parent[0], state, depth, reduced, ret < beta)
        return ret

    sr._negamax = spy
    for s in sample[::4]:
        sr.search(s, max_depth=5)
    by_parent: dict[int, list[tuple]] = {}
    for c in calls:
        if c is not None:
            by_parent.setdefault(c[0], []).append(c)
    researched = 0
    for kids in by_parent.values():
        for c, nxt in zip(kids, kids[1:] + [None], strict=True):
            if c[3] and c[4]:  # reduced, and the parent's v > alpha
                assert nxt is not None and nxt[1] is c[1] and nxt[2] == c[2] + 1
                assert not nxt[3]
                researched += 1
    assert researched > 0


# ---------- registry ----------


def test_v4_registry_entries():
    for name in ("v4", "v4-clock", "v4-0.2s"):
        assert name in BOTS
    a, b = make_bot("v4", 3), make_bot("v4", 3)
    s = GameState.initial()
    assert a.choose_move(s) == b.choose_move(s)  # fixed depth: deterministic
    sr = make_bot("v4-0.2s", 0)._searcher
    assert sr.v4 and sr.tt_bits == V2_TT_BITS and sr.wall_weight == WALL_V3


# ---------- proven loss: the practical choice (delay_slack) ----------


def _lost_samples():
    from tests.search.test_search_edge import sample_positions

    out = []
    for s in sample_positions(range(40, 56), every=3, max_plies=120, wall_bias=0.5):
        r = Searcher(0, **V3).search(s, max_depth=3)
        if is_win_score(r.score) and r.score < 0:
            out.append(s)
    return out


# White (a5, 1 wall) to move cannot win the race against black on e3 (2 steps, no walls):
# every pawn move loses at once, the lengthening walls (d2h, e3h, ...) lose 2 plies later.
# The random samples never reach the wall branch below at depth 3 (review 0064 minor 3).
LOST_WITH_WALL = GameState(((0, 4), (4, 2)), frozenset(), frozenset(), (1, 0), 0)


def test_practical_loss_stays_within_the_slack_and_prefers_lengthening_walls():
    seen = walls_seen = 0
    # depth 3 for the samples; LOST_WITH_WALL is proven lost only from depth 5
    for s, depth in [*((x, 3) for x in _lost_samples()), (LOST_WITH_WALL, 5)]:
        kw = dict(V4_SETTINGS, pvs=0, lmr=0)  # exact scores at a fixed depth
        r = Searcher(0, **kw).search(s, max_depth=depth)
        if not (is_win_score(r.score) and r.score < 0):
            continue
        seen += 1
        slow = Searcher(0, **V3).search(s, max_depth=depth)  # the slowest loss (v3 rule)
        assert r.score >= slow.score - kw["delay_slack"]
        own = Searcher(0, **kw).search_root(s, max_depth=depth, moves=[r.move])
        assert own.scores[0].score == r.score
        me = s.turn
        d_op = shortest_path_length(s, 1 - me)
        lengthening = [
            m
            for m in legal_moves(s)
            if isinstance(m, WallMove)
            and shortest_path_length(apply_move_unchecked(s, m), 1 - me) > d_op
        ]
        if lengthening:
            rs = Searcher(0, **kw).search_root(s, max_depth=depth, moves=lengthening)
            if max(x.score for x in rs.scores) >= slow.score - kw["delay_slack"]:
                walls_seen += 1
                assert isinstance(r.move, WallMove), (s, r)
    assert seen >= 3
    assert walls_seen > 0  # review 0064 minor 3: the wall branch is really exercised


def test_lost_position_prefers_the_lengthening_wall():
    """A known answer for the wall branch: a pawn move loses 2 plies faster than the
    slack allows, so v4 (with and without LMR) plays a wall that lengthens black's path."""
    s = LOST_WITH_WALL
    d_op = shortest_path_length(s, 1)
    for kw in (V4_SETTINGS, dict(V4_SETTINGS, pvs=0, lmr=0)):
        r = Searcher(0, **kw).search(s, max_depth=5)
        assert is_win_score(r.score) and r.score < 0
        assert isinstance(r.move, WallMove)
        assert shortest_path_length(apply_move_unchecked(s, r.move), 1) > d_op


def test_game03_lost_endgame_keeps_the_slowest_line_when_progress_loses_much_faster():
    """game03, white's move 24 (f3, black on d3 in the same corridor): e3 lets black jump
    and loses 3 moves faster than g3, more than the slack: g3 stays."""
    from tests.search.test_search_root import replay_fixture

    s = replay_fixture("game03.txt", 46)
    r = Searcher(0, **V4_SETTINGS).search(s, max_depth=21)
    assert format_move(r.move) == "g3" and is_win_score(r.score) and r.score < 0


def test_root_defensive_walls_take_away_a_threat(sample):
    seen = 0
    for s in sample:
        if min(s.walls_left) == 0:
            continue
        sr = Searcher(0, **V4_SETTINGS)
        sr._reset(s)
        root = {m for m, _ in sr.ordered_moves(s)}
        flipped = GameState(s.pawns, s.h_walls, s.v_walls, s.walls_left, 1 - s.turn)
        threats = [w for g, w, _ in sr.candidate_walls(flipped) if g >= 2][: sr.root_defense]
        me = s.turn
        for w, child in sr._defensive_walls(s, root):
            seen += 1
            assert w not in root and w in legal_moves(s) and child == apply_move_unchecked(s, w)
            assert any(not wall_fits(GameState(*child_parts(child, me)), t) for t in threats)
            gain_op = shortest_path_length(child, 1 - me) - shortest_path_length(s, 1 - me)
            gain_me = shortest_path_length(child, me) - shortest_path_length(s, me)
            assert gain_me <= gain_op
    assert seen > 0


def child_parts(child: GameState, me: int):
    """The child with the opponent to move and a wall in stock (for wall_fits)."""
    return child.pawns, child.h_walls, child.v_walls, (5, 5), 1 - me


def test_search_root_default_set_is_the_root_of_search(sample):
    """Review 0064 minor 5: search_root(moves=None) scores exactly search()'s root moves,
    the defensive walls of root_defense included."""
    extra = 0
    for s in sample[::6]:
        sr = Searcher(0, **V4_SETTINGS)
        got = {r.move for r in sr.search_root(s, max_depth=1).scores}
        sr._reset(s)
        root = {m for m, _ in sr._root_moves(s, pawn_moves(s))}
        assert got == root
        extra += len(root - {m for m, _ in sr.ordered_moves(s)})
    assert extra > 0  # some sample positions do have defensive walls
