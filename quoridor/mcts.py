"""Monte Carlo tree search (PUCT) with a wall-aware policy (phase 9; reports 0076, 0082).

* Move set of a node: all pawn moves + the pruned walls of search.Searcher.candidate_walls
  (walls on the opponent's shortest path with a positive net gain, best `wall_limit`; if
  the opponent threatens to win next move, exactly the walls that stop it). At the root
  also Searcher._root_moves extras: defensive walls (root_defense) and the walls of the
  best combinations of quoridor.threat (combo_root). tree_defense=1 adds the defensive
  walls at every node (dearer).
* Policy (priors, softmax over logits): a pawn move by the change of our distance
  (pawn * steps gained: the shortest-path step first), a wall by its net gain
  (wall + gain * (opponent's path growth - ours), + behind when we are behind in the race),
  root extras get `special`, walls stopping an immediate win get `saving` on top. Equal
  logits keep a seeded order (a splitmix64 shuffle, the same in both cores).
* Value of a new leaf: the eval of the searcher (v4 / v5 terms: path race, wall stock,
  wall race, single-wall threat, combination potential) plus half a step of tempo for the
  side to move, squashed by tanh(score / scale) into [-1, 1]. leaf_depth > 0: a small
  alpha-beta (Searcher._negamax) of that depth instead of the static eval; rollout > 0:
  that many plies of the greedy policy (shortest-path step or the best wall if it gains
  at least 2) before the eval.
* expand_visits = 2 (report 0083): a new leaf is only evaluated (and checked for a win in
  one); its moves are generated on its second visit, which then descends into a child.
  Most leaves are visited once, so this saves their move generation.
* Proven results: a side that can step onto its goal row wins (the node is not
  expanded); a node with a winning child is won, a node whose children all lose is lost
  (over the pruned move set, as in alpha-beta).
* Selection: PUCT  Q + c_puct * P * sqrt(N) / (1 + n), unvisited children with the
  parent's Q minus `fpu`. The move: most visits (ties: higher Q, then the move order).
* Tree reuse: the subtree of the new position is kept if it is a child or grandchild of
  the last root.
* rep_penalty (value units): root moves back to an own (cell, walls left) of the last
  REP_WINDOW own positions of the game (set_history) lose that much Q per occurrence.

Two cores with the same results (tests/search/test_mcts.py compares whole trees):
* "jit" (quoridor.jit.ENABLED, report 0082): the tree is numpy arrays and every
  simulation below the root runs in quoridor.kernels.mcts_run (selection, eval,
  expansion, backup); the root is expanded here in Python (its extras need the searcher).
  Simulations run in batches of a few milliseconds; the clock, should_stop and
  on_progress are checked between batches. Not available with leaf_depth, rollout,
  tree_defense or the searcher options combo_exact / combo_stock: then "py" runs.
* "py": the reference, a tree of _Node objects with the child states.

Deterministic with a simulation limit and no clock. No I/O.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from quoridor import jit as _jit
from quoridor.bitpaths import m8
from quoridor.eval import WIN
from quoridor.rules import apply_move_unchecked, pawn_moves, winner
from quoridor.search import (
    INF,
    REP_WINDOW,
    TEMPO,
    Searcher,
    opponent_winning_moves,
)
from quoridor.state import GameState, H, Move, PawnMove, WallMove, goal_row
from quoridor.timeman import move_budget

_K = _jit.kernels()  # quoridor.kernels or None (pure Python)
if _K is not None:
    import numpy as np

# Default options (see the module docstring); MCTSBot(**opts) overrides them.
MCTS_DEFAULTS: dict[str, float] = {
    "c_puct": 1.5,
    "scale": 400.0,  # centi-steps: tanh(score / scale)
    "fpu": 0.2,
    "pawn": 1.0,  # logit per step our pawn move gains (2.0 / 3.0 / 0.5 lost, report 0076)
    "wall": -1.0,  # base logit of a wall (it costs a move)
    "gain": 0.8,  # logit per step of a wall's net gain
    "behind": 1.0,  # extra wall logit while we are behind in the race
    "special": -0.5,  # logit of root extras (defensive / combination walls)
    "saving": 2.0,  # extra logit of walls that stop the opponent's immediate win
    "leaf_depth": 0,
    "rollout": 0,
    "tree_defense": 0,
    "rep_penalty": 0.0,
    "expand_visits": 1,  # 2: a leaf is evaluated on its first visit, expanded on the second
}
CORES = ("jit", "py")

PROVEN_SCORE = WIN - 2  # reported score of a proven win (loss: the negative)
_PROVEN_SCORE = PROVEN_SCORE  # old name
PROGRESS_EVERY = 0.2  # seconds between on_progress calls (default)

# jit core: tree arrays (grown by doubling when full, up to the caps; then the search
# stops early). Per node 19 bytes, per edge 14 bytes: the caps are ~0.6 GB together.
_NODES0 = 1 << 15
_EDGES0 = 1 << 19
MAX_NODES = 1 << 24
MAX_EDGES = 1 << 25
_BATCH_SECONDS = 0.004  # target length of one mcts_run call

_M64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_MIX1 = 0xBF58476D1CE4E5B9
_MIX2 = 0x94D049BB133111EB


def _move_code(mv: Move) -> int:
    """Move code of quoridor.kernels: 0..80 pawn cell, 128 + wall code."""
    if isinstance(mv, PawnMove):
        return mv.row * 9 + mv.col
    return 128 + (mv.y * 8 + mv.x) * 2 + (0 if mv.orient == H else 1)


_CODE_MOVES: list[Move | None] = [None] * 256
for _i in range(81):
    _CODE_MOVES[_i] = PawnMove(_i % 9, _i // 9)
for _w in range(128):
    _CODE_MOVES[128 + _w] = WallMove((_w >> 1) % 8, (_w >> 1) // 8, H if _w & 1 == 0 else "v")
del _i, _w


@dataclass(frozen=True)
class MCTSResult:
    move: Move
    score: int  # centi-steps for the side to move (with tempo), or +-PROVEN_SCORE
    depth: int  # length of the most visited line from the root
    nodes: int  # simulations of this search
    elapsed: float
    visits: int  # root visits (with reused ones)
    max_depth: int  # deepest node reached in this search

    @property
    def nps(self) -> float:
        return self.nodes / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def display_score(self) -> int:
        """Already includes the tempo of the side to move (no odd/even swing)."""
        return self.score


@dataclass(frozen=True)
class RootChild:
    """A root move of the last search (MCTS.root_children), from the root's side."""

    move: Move
    visits: int  # simulations through this move (with reused ones)
    value: float | None  # mean value in [-1, 1] (None: not visited)
    proven: int  # +1 a proven win, -1 a proven loss, 0 unknown
    score: int  # centi-steps (like MCTSResult.score), +-PROVEN_SCORE if proven; 0 unvisited
    depth: int  # plies of the most visited line through this move (1 + the line below)
    penalty: float  # rep_penalty of this move (value units)


class MCTSCancelled(Exception):
    """search() stopped by should_stop(); `sims` simulations had run."""

    def __init__(self, sims: int) -> None:
        super().__init__(f"MCTS cancelled after {sims} simulations")
        self.sims = sims


class _Node:
    __slots__ = ("state", "moves", "states", "priors", "kids", "n", "w", "proven")

    def __init__(self, state: GameState) -> None:
        self.state = state
        self.moves: list[Move] | None = None  # None: not expanded
        self.states: list[GameState] = []
        self.priors: list[float] = []
        self.kids: list[_Node | None] = []
        self.n = 0
        self.w = 0.0  # sum of values for the side to move at this node
        self.proven = 0  # +1 won / -1 lost for the side to move, 0 unknown


class _Tree:
    """jit core: the tree as arrays (see quoridor.kernels, MCTS section)."""

    def __init__(self, nodes: int, edges: int) -> None:
        self.nn = np.zeros(nodes, dtype=np.int32)  # visits
        self.nw = np.zeros(nodes, dtype=np.float64)  # value sum (side to move there)
        self.npv = np.zeros(nodes, dtype=np.int8)  # proven
        self.ne0 = np.full(nodes, -1, dtype=np.int32)  # first edge, -1: not expanded
        self.nne = np.zeros(nodes, dtype=np.int16)  # number of edges
        self.emv = np.zeros(edges, dtype=np.int16)  # move code
        self.epr = np.zeros(edges, dtype=np.float64)  # prior
        self.ekid = np.full(edges, -1, dtype=np.int32)  # child node, -1: none yet
        self.ctr = np.zeros(3, dtype=np.int64)  # nodes, edges, deepest path
        self.rng = np.zeros(1, dtype=np.uint64)

    def new_node(self) -> int:
        i = int(self.ctr[0])
        if i >= self.nn.size:
            self.grow()
        self.ctr[0] = i + 1
        self.nn[i] = 0
        self.nw[i] = 0.0
        self.npv[i] = 0
        self.ne0[i] = -1
        self.nne[i] = 0
        return i

    def grow(self) -> bool:
        """Double the full arrays; False at the caps."""
        nodes, edges = self.nn.size, self.emv.size
        need_n = self.ctr[0] + 2 > nodes
        need_e = self.ctr[1] + 256 > edges
        if (need_n and nodes >= MAX_NODES) or (need_e and edges >= MAX_EDGES):
            return False
        if need_n:
            for name, fill in (("nn", 0), ("nw", 0.0), ("npv", 0), ("ne0", -1), ("nne", 0)):
                old = getattr(self, name)
                new = np.full(min(nodes * 2, MAX_NODES), fill, dtype=old.dtype)
                new[:nodes] = old
                setattr(self, name, new)
        if need_e:
            for name, fill in (("emv", 0), ("epr", 0.0), ("ekid", -1)):
                old = getattr(self, name)
                new = np.full(min(edges * 2, MAX_EDGES), fill, dtype=old.dtype)
                new[:edges] = old
                setattr(self, name, new)
        return True

    def edges_of(self, node: int) -> range:
        e0 = int(self.ne0[node])
        return range(e0, e0 + int(self.nne[node])) if e0 >= 0 else range(0)


def _cut(searcher: Searcher) -> list[str]:
    """Searcher options the jit core does not implement."""
    return [k for k in ("combo_exact", "combo_stock") if getattr(searcher, k)]


class MCTS:
    """The search. One per bot; keeps the tree between calls for reuse.

    core: "jit" (numba arrays), "py" (the reference) or None = "jit" when available
    (quoridor.jit.ENABLED and supported options), else "py"."""

    def __init__(
        self,
        seed: int = 0,
        searcher_settings: dict | None = None,
        core: str | None = None,
        **opts: float,
    ) -> None:
        unknown = set(opts) - set(MCTS_DEFAULTS)
        if unknown:
            raise TypeError(f"MCTS: unknown options {', '.join(sorted(unknown))}")
        if core is not None and core not in CORES:
            raise ValueError(f"MCTS: core must be one of {CORES}, not {core!r}")
        o = {**MCTS_DEFAULTS, **opts}
        self.c_puct = float(o["c_puct"])
        self.scale = float(o["scale"])
        self.fpu = float(o["fpu"])
        self.k_pawn = float(o["pawn"])
        self.k_wall = float(o["wall"])
        self.k_gain = float(o["gain"])
        self.k_behind = float(o["behind"])
        self.k_special = float(o["special"])
        self.k_saving = float(o["saving"])
        self.leaf_depth = int(o["leaf_depth"])
        self.rollout = int(o["rollout"])
        self.tree_defense = int(o["tree_defense"])
        self.rep_penalty = float(o["rep_penalty"])
        self.expand_visits = int(o["expand_visits"])
        if self.expand_visits not in (1, 2):
            raise ValueError("MCTS: expand_visits must be 1 or 2")
        settings = dict(searcher_settings or {})
        settings["tt_bits"] = 0  # small alpha-beta at the leaves only: no table
        self.s = Searcher(seed, **settings)
        self._rs = seed & _M64  # splitmix64 state (order of equal children)
        self._own: tuple[list, list] = ([], [])
        self._max_depth = 0
        # py core
        self._root: _Node | None = None
        # jit core: arrays, the root node and its position / child states
        self._tree: _Tree | None = None
        self._jroot = 0
        self._jstate: GameState | None = None
        self._jkid_states: list[GameState] = []
        self._pen: list[float] | None = None  # root penalties of the last search
        self._memo: tuple | None = None  # jit: the _race_best / _wall_race memo (kept)
        self._keep_on_cancel = False
        why = self._jit_unsupported()
        if core == "jit" and why:
            raise ValueError(f"MCTS: the jit core is not available ({why})")
        self.core = core or ("py" if why else "jit")

    def _jit_unsupported(self) -> str:
        if _K is None:
            return _jit.REASON or "numba is off"
        bad = [k for k in ("leaf_depth", "rollout", "tree_defense") if getattr(self, k)]
        bad += _cut(self.s)
        return ", ".join(bad)

    # ---------- history ----------

    def set_history(self, states: list[GameState]) -> None:
        own: tuple[list, list] = ([], [])
        for st in states:
            p = st.turn
            own[p].append((st.pawns[p], st.walls_left[p]))
        self._own = own

    # ---------- policy ----------

    def _rand(self) -> int:
        """splitmix64 (quoridor.kernels._rand)."""
        s = (self._rs + _GOLDEN) & _M64
        self._rs = s
        z = ((s ^ (s >> 30)) * _MIX1) & _M64
        z = ((z ^ (z >> 27)) * _MIX2) & _M64
        return z ^ (z >> 31)

    def _expand_lists(
        self, state: GameState, pawns: list[PawnMove], root: bool
    ) -> tuple[list[Move], list[GameState], list[float]]:
        """(moves, child states, priors) of a node (not terminal, no winning pawn move),
        best prior first."""
        s = self.s
        me = state.turn
        m = s.masks(state)
        my_cell, op_cell = state.pawns[me], state.pawns[1 - me]
        d_me = s.dist_m(m, my_cell, me)
        d_op = s.dist_m(m, op_cell, 1 - me)
        assert d_me is not None and d_op is not None
        saving = bool(state.walls_left[me]) and bool(opponent_winning_moves(state))
        moves: list[Move] = []
        states: list[GameState] = []
        logits: list[float] = []
        for mv in pawns:
            d = s.dist_m(m, mv.cell, me)
            assert d is not None
            moves.append(mv)
            states.append(apply_move_unchecked(state, mv))
            logits.append(self.k_pawn * (d_me - d))
        base = self.k_wall + (self.k_behind if d_me > d_op else 0.0)
        if saving:
            base += self.k_saving
        seen: set[Move] = set(moves)
        for gain, w, child in s.candidate_walls(state):
            seen.add(w)
            moves.append(w)
            states.append(child)
            logits.append(base + self.k_gain * gain)
        extras: list[tuple[Move, GameState]] = []
        if root:
            extras = [(mv, c) for mv, c in s._root_moves(state, pawns) if mv not in seen]
        elif self.tree_defense and not saving:
            extras = s._defensive_walls(state, seen)
        for mv, c in extras:
            if mv in seen:
                continue
            seen.add(mv)
            cm = s.masks(c)
            me_after = s.dist_m(cm, my_cell, me)
            op_after = s.dist_m(cm, op_cell, 1 - me)
            if me_after is None or op_after is None:
                continue
            gain = (op_after - d_op) - (me_after - d_me)
            moves.append(mv)
            states.append(c)
            logits.append(base + self.k_special + self.k_gain * gain)
        # seeded order of equal children (ties in selection keep the list order)
        order = list(range(len(moves)))
        for i in range(len(order) - 1, 0, -1):
            j = self._rand() % (i + 1)
            order[i], order[j] = order[j], order[i]
        order.sort(key=lambda i: -logits[i])
        if not order:
            return [], [], []
        top = logits[order[0]]
        ex = [math.exp(logits[i] - top) for i in order]
        tot = 0.0
        for e in ex:  # plain left-to-right sum, as the kernel (not math.fsum / sum())
            tot += e
        return [moves[i] for i in order], [states[i] for i in order], [e / tot for e in ex]

    def _expand(self, node: _Node, pawns: list[PawnMove], root: bool) -> None:
        node.moves, node.states, node.priors = self._expand_lists(node.state, pawns, root)
        node.kids = [None] * len(node.moves)

    # ---------- value ----------

    def _squash(self, score: int) -> float:
        return math.tanh((score + TEMPO) / self.scale)

    def _static(self, state: GameState) -> float:
        s = self.s
        if self.leaf_depth > 0:
            return self._squash(s._negamax(state, self.leaf_depth, -INF, INF, 0))
        return self._squash(s.evaluate(state))

    def _rollout_value(self, state: GameState) -> float:
        """`rollout` plies of the greedy policy, then the eval (for the leaf's side)."""
        s = self.s
        sign = 1.0
        for _ in range(self.rollout):
            me = state.turn
            pawns = pawn_moves(state)
            goal = goal_row(me)
            if any(p.row == goal for p in pawns):
                return sign
            mv: Move | None = None
            if state.walls_left[me]:
                walls = s.candidate_walls(state)
                if walls and (walls[0][0] >= 2 or opponent_winning_moves(state)):
                    mv = walls[0][1]
            if mv is None:
                m = s.masks(state)
                mv = min(pawns, key=lambda p: s.dist_m(m, p.cell, me))  # type: ignore[arg-type, return-value]
            state = apply_move_unchecked(state, mv)
            sign = -sign
        return sign * self._static(state)

    # ---------- py core ----------

    def _reuse(self, state: GameState) -> _Node:
        old = self._root
        self._root = None
        if old is not None and old.moves is not None:
            if old.state == state:
                return old
            for kid in old.kids:
                if kid is None or kid.moves is None:
                    continue
                if kid.state == state:
                    return kid
                for gk in kid.kids:
                    if gk is not None and gk.state == state:
                        return gk
        return _Node(state)

    def _select(self, node: _Node, pen: list[float] | None) -> int:
        sq = self.c_puct * math.sqrt(node.n)
        fpu = node.w / node.n - self.fpu if node.n else 0.0
        best_i = -1
        best = -INF
        kids, priors = node.kids, node.priors
        for i in range(len(kids)):
            k = kids[i]
            if k is None or k.n == 0:
                q, n = fpu, 0
            else:
                if k.proven == 1:
                    continue  # the opponent wins there
                q, n = -k.w / k.n, k.n
            if pen is not None:
                q -= pen[i]
            u = q + sq * priors[i] / (1 + n)
            if u > best:
                best, best_i = u, i
        return best_i

    def _penalties_of(self, state: GameState, kid_states: list[GameState]) -> list[float] | None:
        if not self.rep_penalty:
            return None
        me = state.turn
        recent = self._own[me][-REP_WINDOW:]
        if not recent:
            return None
        return [
            self.rep_penalty * recent.count((c.pawns[me], c.walls_left[me])) for c in kid_states
        ]

    def _penalties(self, root: _Node) -> list[float] | None:
        return self._penalties_of(root.state, root.states)

    def _simulate(self, root: _Node, pen: list[float] | None) -> None:
        node = root
        path = [root]
        lazy = self.expand_visits >= 2
        while node.proven == 0:
            if node.moves is None:
                if not lazy or node.n == 0:
                    break  # a new leaf
                # expand_visits = 2: the second visit expands the leaf, then descends
                self._expand(node, pawn_moves(node.state), False)
            i = self._select(node, pen if node is root else None)
            if i < 0:  # every child loses: proven lost
                node.proven = -1
                break
            kid = node.kids[i]
            if kid is None:
                kid = node.kids[i] = _Node(node.states[i])
            node = kid
            path.append(node)
            if kid.n == 0:
                break
        if len(path) - 1 > self._max_depth:
            self._max_depth = len(path) - 1
        if node.proven:
            v = float(node.proven)
        else:
            state = node.state
            if winner(state) is not None:
                node.proven = -1
                v = -1.0
            else:
                pawns = pawn_moves(state)
                goal = goal_row(state.turn)
                if any(p.row == goal for p in pawns):
                    node.proven = 1
                    v = 1.0
                else:
                    v = self._rollout_value(state) if self.rollout else self._static(state)
                    if not lazy or node.n > 0:
                        self._expand(node, pawns, node is root)
        # backpropagation (values for each node's side to move) and proof propagation
        child_proven = node.proven
        for nd in reversed(path):
            nd.n += 1
            nd.w += v
            v = -v
            if nd is not node and nd.proven == 0 and child_proven:
                if child_proven == -1:
                    nd.proven = 1
                elif all(k is not None and k.proven == 1 for k in nd.kids):
                    nd.proven = -1
            child_proven = nd.proven

    def _add_root_extras(self, root: _Node, pawns: list[PawnMove]) -> None:
        """A reused root lacks the root-only walls: rebuild its move list with them,
        keeping the existing children (and their statistics)."""
        old = {mv: (k, st) for mv, k, st in zip(root.moves, root.kids, root.states, strict=True)}  # type: ignore[arg-type]
        self._expand(root, pawns, True)
        for i, mv in enumerate(root.moves):  # type: ignore[arg-type]
            hit = old.get(mv)
            if hit is not None:
                root.kids[i] = hit[0]

    def _run_py(self, state, pawns, sims, deadline, should_stop, on_progress, every) -> int:
        root = self._reuse(state)
        if root.moves is None or root.proven:
            root = _Node(state)
            root.n = 1
            root.w = self._static(state)
            self._expand(root, pawns, True)
        else:
            # the reused root was expanded as an inner node: add the root extras
            self._add_root_extras(root, pawns)
        self._root = root
        pen = self._pen = self._penalties(root)
        done = 0
        last = time.perf_counter()
        while root.proven == 0:
            if sims is not None and done >= sims:
                break
            if deadline is not None and done and time.perf_counter() >= deadline:
                break
            if should_stop is not None and should_stop():
                if not self._keep_on_cancel:
                    self._root = None
                raise MCTSCancelled(done)
            self._simulate(root, pen)
            done += 1
            if on_progress is not None:
                now = time.perf_counter()
                if now - last >= every:
                    last = now
                    on_progress(self._pv_len(root), done)
            if len(root.moves or ()) == 1 and done >= 2:
                break
        return done

    def _kids_py(self) -> list[tuple[Move, bool, int, float, int, int]]:
        """(move, expanded child exists, visits, value sum, proven, line below) per root
        child, for the side to move at the child."""
        root = self._root
        out = []
        if root is None or root.moves is None:
            return out
        for mv, k in zip(root.moves, root.kids, strict=True):
            if k is None:
                out.append((mv, False, 0, 0.0, 0, 0))
            else:
                below = self._pv_len(k) if k.moves is not None and k.n > 1 else 0
                out.append((mv, True, k.n, k.w, k.proven, below))
        return out

    # ---------- jit core ----------

    def _jit_find(self, state: GameState) -> int | None:
        """The node of `state` in the last tree (as _reuse: the root, an expanded child
        or a grandchild), None if absent."""
        t, old = self._tree, self._jstate
        if t is None or old is None or t.ne0[self._jroot] < 0:
            return None
        if old == state:
            return self._jroot
        for e in t.edges_of(self._jroot):
            kid = int(t.ekid[e])
            if kid < 0 or t.ne0[kid] < 0:
                continue
            ks = apply_move_unchecked(old, _CODE_MOVES[t.emv[e]])  # type: ignore[arg-type]
            if ks == state:
                return kid
            for g in t.edges_of(kid):
                gk = int(t.ekid[g])
                if gk >= 0 and apply_move_unchecked(ks, _CODE_MOVES[t.emv[g]]) == state:  # type: ignore[arg-type]
                    return gk
        return None

    def _jit_root(self, state: GameState, pawns: list[PawnMove]) -> None:
        """Root of the jit tree: the reused subtree (compacted, root extras added) or a
        new root expanded here."""
        assert _K is not None
        found = self._jit_find(state)
        self._jstate = None
        t = self._tree
        if t is not None and found is not None and t.ne0[found] >= 0 and t.npv[found] == 0:
            nodes, edges = _K.mcts_count(found, t.ne0, t.nne, t.ekid)
            nt = _Tree(max(_NODES0, 2 * nodes), max(_EDGES0, 2 * edges + 256))
            n2, e2 = _K.mcts_compact(
                found, t.nn, t.nw, t.npv, t.ne0, t.nne, t.emv, t.epr, t.ekid,
                nt.nn, nt.nw, nt.npv, nt.ne0, nt.nne, nt.emv, nt.epr, nt.ekid,
            )  # fmt: skip
            nt.ctr[0], nt.ctr[1] = n2, e2
            self._tree = t = nt
            # the reused root was expanded as an inner node: add the root extras
            old = {int(t.emv[e]): int(t.ekid[e]) for e in t.edges_of(0)}
            moves, states, priors = self._expand_lists(state, pawns, True)
            self._jit_write_edges(0, moves, priors, old)
        else:
            if t is None:
                self._tree = t = _Tree(_NODES0, _EDGES0)
            t.ctr[:] = 0
            r = t.new_node()
            t.nn[r] = 1
            t.nw[r] = self._static(state)
            moves, states, priors = self._expand_lists(state, pawns, True)
            self._jit_write_edges(r, moves, priors, None)
        self._jroot = 0
        self._jstate = state
        self._jkid_states = states

    def _jit_write_edges(
        self, node: int, moves: list[Move], priors: list[float], kids: dict[int, int] | None
    ) -> None:
        t = self._tree
        assert t is not None
        while t.ctr[1] + len(moves) + 256 > t.emv.size:
            if not t.grow():
                raise MemoryError("MCTS tree arrays at their caps")
        start = int(t.ctr[1])
        for i, (mv, p) in enumerate(zip(moves, priors, strict=True)):
            code = _move_code(mv)
            t.emv[start + i] = code
            t.epr[start + i] = p
            t.ekid[start + i] = -1 if kids is None else kids.get(code, -1)
        t.ne0[node] = start
        t.nne[node] = len(moves)
        t.ctr[1] = start + len(moves)

    def _jit_params(self) -> tuple:
        s = self.s
        assert _K is not None
        ip = np.zeros(_K.IP_SIZE, dtype=np.int64)
        ip[_K.IP_WALL_WEIGHT] = s.wall_weight
        ip[_K.IP_V4] = s.v4
        ip[_K.IP_THREAT] = s.threat
        ip[_K.IP_RESERVE] = s.reserve
        ip[_K.IP_COMBO] = s.combo
        ip[_K.IP_COMBO_K] = s.combo_k
        ip[_K.IP_COMBO_BEAM] = s.combo_beam
        ip[_K.IP_COMBO_NARROW] = s.combo_narrow
        ip[_K.IP_COMBO_COST] = s.combo_cost
        ip[_K.IP_RACE_PLAN] = s.race_plan
        ip[_K.IP_RACE_SOFT] = s.race_soft
        ip[_K.IP_RACE_MIN_GAIN] = s.race_min_gain
        ip[_K.IP_WALL_LIMIT] = s.wall_limit
        ip[_K.IP_EXPAND] = self.expand_visits
        fp = np.zeros(_K.FP_SIZE, dtype=np.float64)
        fp[_K.FP_C_PUCT] = self.c_puct
        fp[_K.FP_SCALE] = self.scale
        fp[_K.FP_FPU] = self.fpu
        fp[_K.FP_PAWN] = self.k_pawn
        fp[_K.FP_WALL] = self.k_wall
        fp[_K.FP_GAIN] = self.k_gain
        fp[_K.FP_BEHIND] = self.k_behind
        fp[_K.FP_SAVING] = self.k_saving
        fp[_K.FP_TEMPO] = TEMPO
        return ip, fp

    def _run_jit(self, state, pawns, sims, deadline, should_stop, on_progress, every) -> int:
        assert _K is not None
        self._jit_root(state, pawns)
        t = self._tree
        assert t is not None
        t.ctr[2] = 0
        pen_l = self._pen = self._penalties_of(state, self._jkid_states)
        pen = np.array(pen_l if pen_l else [0.0], dtype=np.float64)
        use_pen = pen_l is not None
        ip, fp = self._jit_params()
        rm = np.array(m8(self.s.masks(state)), dtype=np.uint64)
        hb = vb = 0
        for x, y in state.h_walls:
            hb |= 1 << (y * 8 + x)
        for x, y in state.v_walls:
            vb |= 1 << (y * 8 + x)
        rpawn = np.array([c[1] * 9 + c[0] for c in state.pawns], dtype=np.int64)
        rwl = np.array(state.walls_left, dtype=np.int64)
        ct = np.zeros((2, 81, 3 + self.s.combo_k), dtype=np.int64)
        rhb, rvb = np.uint64(hb), np.uint64(vb)
        if self._memo is None:
            size = 1 << _K.CACHE_BITS
            self._memo = (
                np.zeros((size, _K.CACHE_WORDS), dtype=np.uint64),
                np.zeros((size, 3), dtype=np.int64),
            )
        ck, cv = self._memo
        done = 0
        batch = 16
        last = time.perf_counter()
        while True:
            if sims is not None and done >= sims:
                break
            now = time.perf_counter()
            if deadline is not None and done and now >= deadline:
                break
            if should_stop is not None and should_stop():
                if not self._keep_on_cancel:
                    self._jstate = None
                raise MCTSCancelled(done)
            if on_progress is not None and done and now - last >= every:
                last = now
                on_progress(_K.mcts_pv(0, t.nn, t.ne0, t.nne, t.ekid), done)
            k = batch if sims is None else min(batch, sims - done)
            t.rng[0] = self._rs
            ran, status = _K.mcts_run(
                t.nn, t.nw, t.npv, t.ne0, t.nne, t.emv, t.epr, t.ekid, t.ctr, t.rng,
                0, rm, rhb, rvb, rpawn, rwl, state.turn, fp, ip, pen, use_pen, ct, ck, cv, k, done,
            )  # fmt: skip
            self._rs = int(t.rng[0])
            done += ran
            if status == _K.ST_FULL:
                if not t.grow():
                    break  # at the caps: play with what we have
                continue
            if status != _K.ST_DONE:
                break
            el = time.perf_counter() - now
            if ran and el > 0:
                target = _BATCH_SECONDS
                if deadline is not None:
                    target = min(target, max(deadline - time.perf_counter(), 0.0))
                batch = max(1, min(8192, int(target * ran / el) + 1))
        self._max_depth = int(t.ctr[2])
        return done

    def _kids_jit(self) -> list[tuple[Move, bool, int, float, int, int]]:
        t = self._tree
        out = []
        if t is None or self._jstate is None:
            return out
        assert _K is not None
        for e in t.edges_of(self._jroot):
            mv = _CODE_MOVES[t.emv[e]]
            k = int(t.ekid[e])
            if k < 0:
                out.append((mv, False, 0, 0.0, 0, 0))
            else:
                n = int(t.nn[k])
                below = _K.mcts_pv(k, t.nn, t.ne0, t.nne, t.ekid) if t.ne0[k] >= 0 and n > 1 else 0
                out.append((mv, True, n, float(t.nw[k]), int(t.npv[k]), below))
        return out  # type: ignore[return-value]

    # ---------- search ----------

    def _kids(self) -> list[tuple[Move, bool, int, float, int, int]]:
        return self._kids_jit() if self.core == "jit" else self._kids_py()

    def search(
        self,
        state: GameState,
        sims: int | None = None,
        time_limit: float | None = None,
        *,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        progress_every: float = PROGRESS_EVERY,
        keep_on_cancel: bool = False,
    ) -> MCTSResult:
        """Search `state` for `sims` simulations and/or `time_limit` seconds.

        should_stop(): checked between simulations (jit: between batches of a few ms);
        True raises MCTSCancelled; the tree is dropped unless keep_on_cancel (it is cut
        between simulations, so it stays consistent and the next search may reuse it).
        on_progress(line, sims): called at most every `progress_every` seconds with the
        length of the most visited line and the simulations so far. After the search
        root_children() describes the root."""
        if winner(state) is not None:
            raise ValueError("no legal moves (terminal position)")
        if sims is None and time_limit is None:
            raise ValueError("need sims or time_limit")
        t0 = time.perf_counter()
        me = state.turn
        goal = goal_row(me)
        pawns = pawn_moves(state)
        for p in pawns:
            if p.row == goal:
                self._root = None
                self._jstate = None
                self._pen = None
                return MCTSResult(p, PROVEN_SCORE, 1, 1, time.perf_counter() - t0, 1, 1)
        s = self.s
        s._reset(state)
        self._keep_on_cancel = keep_on_cancel
        try:
            self._max_depth = 0
            deadline = None if time_limit is None else t0 + time_limit
            run = self._run_jit if self.core == "jit" else self._run_py
            done = run(state, pawns, sims, deadline, should_stop, on_progress, progress_every)
            kids = self._kids()
            i = self._best(kids, self._pen)
            move, exists, kn, kw, kp, _ = kids[i]
            root_proven, root_n = self._root_stats()
            if root_proven == 1 and exists and kp == -1:
                score = PROVEN_SCORE
            elif exists and kp == 1 and root_proven == -1:
                score = -PROVEN_SCORE
            elif exists and kn:
                score = self._score(kw, kn)
            else:
                score = 0
            return MCTSResult(
                move,
                int(score),
                self.pv_length(),
                done,
                time.perf_counter() - t0,
                root_n,
                self._max_depth,
            )
        finally:
            s._free()

    def _score(self, kw: float, kn: int) -> int:
        """Centi-steps of a child (value sum kw over kn visits, child's side) for the
        root's side; the leaf values already carry the tempo of the side to move."""
        q = max(-0.999, min(0.999, -kw / kn))
        return round(self.scale * math.atanh(q))

    def _root_stats(self) -> tuple[int, int]:
        """(proven, visits) of the last root."""
        if self.core == "jit":
            t = self._tree
            assert t is not None
            return int(t.npv[self._jroot]), int(t.nn[self._jroot])
        assert self._root is not None
        return self._root.proven, self._root.n

    @staticmethod
    def _best(kids: list, pen: list[float] | None) -> int:
        for i, (_, exists, _, _, kp, _) in enumerate(kids):
            if exists and kp == -1:
                return i  # a won line
        best_i, best_key = 0, None
        for i, (_, exists, kn, kw, kp, _) in enumerate(kids):
            n = kn if exists else 0
            q = -kw / kn if exists and kn else -2.0
            lost = exists and kp == 1
            if pen is not None:
                q -= pen[i]
                n -= int(pen[i] * n)  # a repetition also costs visits in the choice
            key = (not lost, n, q)
            if best_key is None or key > best_key:
                best_i, best_key = i, key
        return best_i

    def pv_length(self) -> int:
        """Length of the most visited line of the last search (at least 1)."""
        if self.core == "jit":
            t = self._tree
            if t is None or self._jstate is None:
                return 1
            assert _K is not None
            return int(_K.mcts_pv(self._jroot, t.nn, t.ne0, t.nne, t.ekid))
        return self._pv_len(self._root) if self._root is not None else 1

    def root_children(self) -> list[RootChild]:
        """The root moves of the last search in the root's move order (best prior first),
        unvisited ones included. Empty if the last search found a win in one move."""
        out = []
        pen = self._pen
        for i, (mv, exists, kn, kw, kp, below) in enumerate(self._kids()):
            visited = exists and kn > 0
            proven = -kp if exists else 0
            if proven:
                score = proven * PROVEN_SCORE
            else:
                score = self._score(kw, kn) if visited else 0
            out.append(
                RootChild(
                    mv,
                    kn if exists else 0,
                    -kw / kn if visited else None,
                    proven,
                    score,
                    1 + below,
                    pen[i] if pen is not None else 0.0,
                )
            )
        return out

    @staticmethod
    def _pv_len(root: _Node) -> int:
        d = 0
        node: _Node | None = root
        while node is not None and node.moves is not None and node.n > 1:
            best = None
            for k in node.kids:
                if k is not None and (best is None or k.n > best.n):
                    best = k
            if best is None:
                break
            d += 1
            node = best
        return max(1, d)

    def _iter_tree(self):
        """(state, moves) of every expanded node of the last tree (tests)."""
        if self.core == "py":
            stack = [self._root]
            while stack:
                node = stack.pop()
                if node is None or node.moves is None:
                    continue
                yield node.state, list(node.moves)
                stack.extend(node.kids)
            return
        t = self._tree
        if t is None or self._jstate is None:
            return
        todo = [(self._jroot, self._jstate)]
        while todo:
            x, st = todo.pop()
            if t.ne0[x] < 0:
                continue
            moves = [_CODE_MOVES[t.emv[e]] for e in t.edges_of(x)]
            yield st, moves
            for e, mv in zip(t.edges_of(x), moves, strict=True):
                k = int(t.ekid[e])
                if k >= 0:
                    todo.append((k, apply_move_unchecked(st, mv)))  # type: ignore[arg-type]


class MCTSBot:
    """Bot wrapper: limits like SearchBot (sims / move_time / game_time, the tightest
    wins), the game history for rep_penalty, `last` = MCTSResult."""

    def __init__(
        self,
        seed: int = 0,
        sims: int | None = None,
        move_time: float | None = None,
        game_time: float | None = None,
        searcher_settings: dict | None = None,
        core: str | None = None,
        **opts: float,
    ) -> None:
        if sims is None and move_time is None and game_time is None:
            raise ValueError("MCTSBot needs sims, move_time or game_time")
        self.sims = sims
        self.move_time = move_time
        self.game_time = game_time
        self.depth: int | None = None  # interface of SearchBot (tests.positions.score)
        self._mcts = MCTS(seed, searcher_settings, core, **opts)
        self.used = 0.0
        self.moves = 0
        self.last: MCTSResult | None = None
        self._history: list[GameState] = []
        self._after: GameState | None = None

    @property
    def mcts(self) -> MCTS:
        return self._mcts

    def set_history(self, states: list[GameState]) -> None:
        self._history = list(states)
        self._after = None

    def _time_limit(self) -> float | None:
        limit = self.move_time
        if self.game_time is not None:
            budget = move_budget(self.game_time - self.used, self.moves)
            limit = budget if limit is None else min(limit, budget)
        return limit

    def choose_move(self, state: GameState) -> Move:
        from quoridor.bots import _follows  # bots imports this module

        t0 = time.perf_counter()
        if self._after is not None:
            if _follows(self._after, state):
                self._history.append(self._after)
            else:
                self._history = []
        self._after = None
        self._mcts.set_history(self._history)
        sims = self.sims
        limit = self._time_limit()
        if sims is None and limit is None:  # depth-only settings from outside
            sims = 1
        res = self._mcts.search(state, sims, limit)
        self.used += time.perf_counter() - t0
        self.moves += 1
        self.last = res
        self._history.append(state)
        self._after = apply_move_unchecked(state, res.move)
        return res.move
