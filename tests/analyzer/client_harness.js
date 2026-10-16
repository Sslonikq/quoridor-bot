const vm = require("vm"), fs = require("fs");
const calls = [], bodies = [];
let pendingDeep = null, keyHandler = null;
const POS = { pawns: [{c: 4, r: 0}, {c: 4, r: 8}], walls: [], walls_left: [10, 10], dist: [8, 8],
              turn: 0, winner: null, last: null, legal: [] };
function stub() {
  const t = { style: {}, classList: { toggle() {}, add() {}, remove() {} }, dataset: {}, hidden: false,
              value: "1", children: [], querySelectorAll: () => [], querySelector: () => null,
              appendChild() {}, setAttribute() {}, addEventListener() {}, scrollIntoView() {},
              createSVGPoint: () => ({ matrixTransform: () => ({ x: globalThis.__x, y: 0 }) }),
              getScreenCTM: () => ({ inverse: () => ({}) }) };
  return t;
}
const els = {};
const doc = { getElementById: id => els[id] || (els[id] = stub()), createElementNS: () => stub(),
              documentElement: {}, addEventListener: (t, f) => { if (t === "keydown") keyHandler = f; } };
const ctx = { document: doc, getComputedStyle: () => ({ getPropertyValue: () => "#000" }),
  navigator: {}, setTimeout, clearTimeout, setInterval, clearInterval, Date, console, AbortController, Math, JSON,
  fetch: async (path, o) => {
    calls.push(path);
    const req = o && o.body ? JSON.parse(o.body) : {};
    bodies.push([path, req]);
    if (path === "/api/analyze" && req.time === 20)  // deep analysis: the reply is released by the test
      return new Promise(res => { pendingDeep = () => res({ json: async () => ({ ok: true, top: DEEP_TOP, eval: 0, eval_mate: false, depth: 12, elapsed: 20 }) }); });
    const body = path === "/api/state" ? { ok: true, position: POS } : { ok: true, cancelled: true };
    return { json: async () => body };
  } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const run = s => vm.runInContext(s, ctx);
const tick = () => new Promise(r => setTimeout(r, 20));
const DEEP_TOP = [{ name: "e2", k: "p", c: 4, r: 1, score: 0, why: "x", depth: 12, exact: true, mate: false }];
const step = { eval: 0, mark: null, loss: 0, move: "e2", elapsed: 0, depth: 3, eval_mate: false,
               top: [{ name: "e2", k: "p", c: 4, r: 1, score: 0, why: "x", depth: 3, exact: true }] };
const out = {};
(async () => {
  await tick();
  const reset = () => { calls.length = 0; };
  // 1. no review, hint on: analysis starts, no cancel
  reset(); run("hintOn = true; rv = null; ply = 0; line = []; ana = null;"); run("refresh()"); await tick();
  out.plain = calls.slice();
  // 2. review running: no new analysis, server calculation is cancelled
  reset(); run("rv = {finished: false, moves: [], steps: [], total: 1}"); run("refresh()"); await tick();
  out.running = calls.slice();
  // 3. hint taken from a finished review
  reset(); run("rv = {finished: true, moves: [], steps: [" + JSON.stringify(step) + "], total: 1}");
  run("refresh()"); await tick();
  out.fromReview = calls.slice();
  // 4. hint off
  reset(); run("rv = null; hintOn = false"); run("refresh()"); await tick();
  out.hintOff = calls.slice();
  // 5. graph click in a branch does not go past the common start
  const steps = [0, 1, 2, 3].map(() => step);
  run("hintOn = true; rv = {finished: true, moves: ['e2','e8','e3'], steps: " + JSON.stringify(steps) + ", total: 4}");
  run("line = ['e2','e8','d3']; ply = 1; mainLine = null; drawGraph()");
  globalThis.__x = 592;  // the right edge: position 3
  els.graph.onclick({ clientX: 0, clientY: 0 });
  out.branchPly = run("ply");
  // ... and in the main line it reaches the end
  run("line = ['e2','e8','e3']; ply = 0; drawGraph()");
  els.graph.onclick({ clientX: 0, clientY: 0 });
  out.mainPly = run("ply");
  // 6. default hint time is 5 s
  reset(); bodies.length = 0; run("rv = null; hintOn = true; line = []; ply = 0; ana = null"); run("refresh()"); await tick();
  out.defaultTime = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  // 7. deep analysis: time=20, in progress, cancelled by a position change
  reset(); bodies.length = 0; run("startDeep()"); await tick();
  out.deepTime = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  out.deepRunning = run("deep !== null"); out.deepProgShown = els.deepProg.hidden === false;
  reset(); run("refresh()"); await tick();
  out.deepAfterMove = run("deep !== null"); out.deepMoveCalls = calls.slice();
  // 8. Esc cancels the server search and brings back the normal hint
  reset(); bodies.length = 0; run("startDeep()"); await tick(); reset(); bodies.length = 0;
  keyHandler({ key: "Escape", target: {}, preventDefault() {} }); await tick();
  out.escDeep = run("deep !== null"); out.escCalls = calls.slice();
  out.escTimes = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  // 9. a new deep request replaces the old one (click on the busy button cancels)
  run("startDeep()"); await tick(); reset(); run("startDeep()"); await tick();
  out.clickCancel = run("deep !== null"); out.clickCalls = calls.slice();
  // 10. the result is marked
  run("startDeep()"); await tick(); pendingDeep(); await tick();
  out.doneDeep = run("deep !== null"); out.doneMark = run("ana && ana.deep");
  out.doneMsg = els.anaMsg.textContent;
  // 11. no deep analysis while the hint is off or a review is running
  reset(); run("hintOn = false; ana = null"); run("startDeep()"); await tick(); out.deepHintOff = run("deep !== null");
  run("hintOn = true; rv = {finished: false, moves: [], steps: [], total: 1}"); run("startDeep()"); await tick(); out.deepReview = run("deep !== null");
  console.log(JSON.stringify(out));
  process.exit(0);
})();
