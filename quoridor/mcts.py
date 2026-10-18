"""Monte Carlo tree search (PUCT) with a wall-aware policy (phase 9, step 5: experiment).

Not the default anywhere: an alternative to the alpha-beta of quoridor.search, compared
with v4 / v5 at equal time (report 0076).

* Move set of a node: all pawn moves + the pruned walls of search.Searcher.candidate_walls
  (walls on the opponent's shortest path with a positive net gain, best `wall_limit`; if
  the opponent threatens to win next move, exactly the walls that stop it). At the root
  also Searcher._root_moves extras: defensive walls (root_defense) and the walls of the
  best combinations of quoridor.threat (combo_root). tree_defense=1 adds the defensive
  walls at every node (dearer).
* Policy (priors, softmax over logits): a pawn move by the change of our distance
  (pawn * steps gained: the shortest-path step first), a wall by its net gain
  (wall + gain * (opponent's path growth - ours), + behind when we are behind in the race),
  root extras get `special`, walls stopping an immediate win get `saving` on top.
* Value of a new leaf: the eval of the searcher (v4 / v5 terms: path race, wall stock,
  wall race, single-wall threat, combination potential) plus half a step of tempo for the
  side to move, squashed by tanh(score / scale) into [-1, 1]. leaf_depth > 0: a small
  alpha-beta (Searcher._negamax) of that depth instead of the static eval; rollout > 0:
  that many plies of the greedy policy (shortest-path step or the best wall if it gains
  at least 2) before the eval.
* Proven results: a side that can step onto its goal row wins (the node is not
  expanded); a node with a winning child is won, a node whose children all lose is lost
  (over the pruned move set, as in alpha-beta).
* Selection: PUCT  Q + c_puct * P * sqrt(N) / (1 + n), unvisited children with the
  parent's Q minus `fpu`. The move: most visits (ties: higher Q, then the move order).
* Tree reuse: the subtree of the new position is kept if it is a child or grandchild of
  the last root.
* rep_penalty (value units): root moves back to an own (cell, walls left) of the last
  REP_WINDOW own positions of the game (set_history) lose that much Q per occurrence.

Deterministic with a simulation limit and no clock (the seed only shuffles the order of
equal children). Pure Python; no I/O.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from quoridor.eval import WIN
from quoridor.rules import apply_move_unchecked, pawn_moves, winner
from quoridor.search import (
    INF,
    REP_WINDOW,
    TEMPO,
    Searcher,
    opponent_winning_moves,
)
from quoridor.state import GameState, Move, PawnMove, goal_row
from quoridor.timeman import move_budget

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
}

_PROVEN_SCORE = WIN - 2  # reported score of a proven win (loss: the negative)


@dataclass(frozen=True)
class MCTSResult:
    move: Move
    score: int  # centi-steps for the side to move (with tempo), or +-_PROVEN_SCORE
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


class MCTS:
    """The search. One per bot; keeps the tree between calls for reuse."""

    def __init__(self, seed: int = 0, searcher_settings: dict | None = None, **opts: float) -> None:
        unknown = set(opts) - set(MCTS_DEFAULTS)
        if unknown:
            raise TypeError(f"MCTS: unknown options {', '.join(sorted(unknown))}")
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
        settings = dict(searcher_settings or {})
        settings["tt_bits"] = 0  # small alpha-beta at the leaves only: no table
        self.s = Searcher(seed, **settings)
        self._rng = random.Random(seed)
        self._root: _Node | None = None
        self._own: tuple[list, list] = ([], [])
        self._max_depth = 0

    # ---------- history ----------

    def set_history(self, states: list[GameState]) -> None:
        own: tuple[list, list] = ([], [])
        for st in states:
            p = st.turn
            own[p].append((st.pawns[p], st.walls_left[p]))
        self._own = own

    # ---------- policy ----------

    def _expand(self, node: _Node, pawns: list[PawnMove], root: bool) -> None:
        """Move set and priors of `node` (not terminal, no winning pawn move)."""
        s = self.s
        state = node.state
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
        self._rng.shuffle(order)
        order.sort(key=lambda i: -logits[i])
        top = logits[order[0]]
        ex = [math.exp(logits[i] - top) for i in order]
        tot = sum(ex)
        node.moves = [moves[i] for i in order]
        node.states = [states[i] for i in order]
        node.priors = [e / tot for e in ex]
        node.kids = [None] * len(order)

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

    # ---------- search ----------

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

    def _penalties(self, root: _Node) -> list[float] | None:
        if not self.rep_penalty:
            return None
        me = root.state.turn
        recent = self._own[me][-REP_WINDOW:]
        if not recent:
            return None
        return [
            self.rep_penalty * recent.count((c.pawns[me], c.walls_left[me])) for c in root.states
        ]

    def _simulate(self, root: _Node, pen: list[float] | None) -> None:
        node = root
        path = [root]
        while node.moves is not None and node.proven == 0:
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

    def search(
        self, state: GameState, sims: int | None = None, time_limit: float | None = None
    ) -> MCTSResult:
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
                return MCTSResult(p, _PROVEN_SCORE, 1, 1, time.perf_counter() - t0, 1, 1)
        s = self.s
        s._reset(state)
        try:
            root = self._reuse(state)
            if root.moves is None or root.proven:
                root = _Node(state)
                root.n = 1
                root.w = self._static(state)
                self._expand(root, pawns, True)
            else:
                # the reused root was expanded as an inner node: add the root extras
                self._add_root_extras(root, pawns)
            pen = self._penalties(root)
            self._max_depth = 0
            done = 0
            deadline = None if time_limit is None else t0 + time_limit
            while root.proven == 0:
                if sims is not None and done >= sims:
                    break
                if deadline is not None and done and time.perf_counter() >= deadline:
                    break
                self._simulate(root, pen)
                done += 1
                if len(root.moves or ()) == 1 and done >= 2:
                    break
            i = self._best(root, pen)
            self._root = root
            move = root.moves[i]  # type: ignore[index]
            kid = root.kids[i]
            if root.proven == 1 and kid is not None and kid.proven == -1:
                score = _PROVEN_SCORE
            elif kid is not None and kid.proven == 1 and root.proven == -1:
                score = -_PROVEN_SCORE
            elif kid is not None and kid.n:
                # the leaf values already carry the tempo of the side to move
                q = max(-0.999, min(0.999, -kid.w / kid.n))
                score = round(self.scale * math.atanh(q))
            else:
                score = 0
            return MCTSResult(
                move,
                int(score),
                self._pv_len(root),
                done,
                time.perf_counter() - t0,
                root.n,
                self._max_depth,
            )
        finally:
            s._free()

    def _add_root_extras(self, root: _Node, pawns: list[PawnMove]) -> None:
        """A reused root lacks the root-only walls: rebuild its move list with them,
        keeping the existing children (and their statistics)."""
        old = {mv: (k, st) for mv, k, st in zip(root.moves, root.kids, root.states, strict=True)}  # type: ignore[arg-type]
        self._expand(root, pawns, True)
        for i, mv in enumerate(root.moves):  # type: ignore[arg-type]
            hit = old.get(mv)
            if hit is not None:
                root.kids[i] = hit[0]

    def _best(self, root: _Node, pen: list[float] | None) -> int:
        kids = root.kids
        for i, k in enumerate(kids):
            if k is not None and k.proven == -1:
                return i  # a won line
        best_i, best_key = 0, None
        for i, k in enumerate(kids):
            n = k.n if k is not None else 0
            q = -k.w / k.n if k is not None and k.n else -2.0
            lost = k is not None and k.proven == 1
            if pen is not None:
                q -= pen[i]
                n -= int(pen[i] * n)  # a repetition also costs visits in the choice
            key = (not lost, n, q)
            if best_key is None or key > best_key:
                best_i, best_key = i, key
        return best_i

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
        **opts: float,
    ) -> None:
        if sims is None and move_time is None and game_time is None:
            raise ValueError("MCTSBot needs sims, move_time or game_time")
        self.sims = sims
        self.move_time = move_time
        self.game_time = game_time
        self.depth: int | None = None  # interface of SearchBot (tests.positions.score)
        self._mcts = MCTS(seed, searcher_settings, **opts)
        self.used = 0.0
        self.moves = 0
        self.last: MCTSResult | None = None
        self._history: list[GameState] = []
        self._after: GameState | None = None

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
