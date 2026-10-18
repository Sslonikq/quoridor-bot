const vm = require("vm"), fs = require("fs");
const calls = [], bodies = [];
globalThis.__okAnalyze = true;
let pendingDeep = null, keyHandler = null;
const store = new Map();
const localStorage = { getItem: k => { if (globalThis.__lsThrow) throw new Error("denied"); return store.has(k) ? store.get(k) : null; },
                       setItem: (k, v) => { if (globalThis.__lsThrow) throw new Error("denied"); store.set(k, v); } };
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
  navigator: {}, localStorage, setTimeout, clearTimeout, setInterval, clearInterval, Date, console, AbortController, Math, JSON,
  fetch: async (path, o) => {
    calls.push(path);
    const req = o && o.body ? JSON.parse(o.body) : {};
    bodies.push([path, req]);
    if (path === "/api/analyze" && req.time === 20)  // deep analysis: the reply is released by the test
      return new Promise(res => { pendingDeep = () => res({ json: async () => ({ ok: true, top: DEEP_TOP, eval: 0, eval_mate: false, depth: 12, elapsed: 20 }) }); });
    if (path === "/api/progress") return { json: async () => ({ ok: true, busy: true, depth: 7 }) };
    if (path === "/api/analyze" && globalThis.__staleCancels > 0) {  // a late foreign cancel kills this request
      globalThis.__staleCancels--;
      return { json: async () => ({ ok: true, cancelled: true }) };
    }
    if (path === "/api/analyze" && globalThis.__okAnalyze)
      return { json: async () => ({ ok: true, top: DEEP_TOP, eval: 0, eval_mate: false, depth: 5, elapsed: 1 }) };
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
  out.initialMode = run("mode"); out.initialStore = store.size;  // no storage value yet: the default is "normal"
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
  // 7. deep mode: time=20, in progress, replaced by a new analysis on a position change
  reset(); bodies.length = 0; run('setMode("deep")'); await tick();
  out.deepTime = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  out.deepRunning = run("live !== null"); out.deepProgShown = els.deepProg.hidden === false;
  reset(); bodies.length = 0; run("refresh()"); await tick();
  out.deepAfterMove = run("live !== null"); out.deepMoveCalls = calls.slice();
  out.deepMoveTimes = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  // 8. Esc cancels the search on the server; no new analysis is started
  reset(); bodies.length = 0;
  keyHandler({ key: "Escape", target: {}, preventDefault() {} }); await tick();
  out.escLive = run("live !== null"); out.escCalls = calls.slice(); out.escState = run("anaState");
  // 9. a click on the busy mode cancels; a click on an idle mode recalculates
  run('setMode("deep")'); await tick(); reset(); run('setMode("deep")'); await tick();
  out.clickCancel = run("live !== null"); out.clickCalls = calls.slice();
  reset(); bodies.length = 0; run('setMode("deep")'); await tick();
  out.clickRestart = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
  // 10. the result is marked with the mode
  pendingDeep(); await tick();
  out.doneLive = run("live !== null"); out.doneMark = run("ana && ana.mode");
  out.doneMsg = els.anaMsg.textContent;
  // 11. a mode change does not start analysis while the hint is off or a review is running
  run('setMode("normal")'); await tick();
  reset(); bodies.length = 0; run("hintOn = false; ana = null"); run('setMode("fast")'); await tick(); out.modeHintOff = bodies.length;
  run("hintOn = true; rv = {finished: false, moves: [], steps: [], total: 1}"); run('setMode("deep")'); await tick(); out.modeReview = bodies.length;
  // 12. every mode sends its own time, no racing /api/cancel; live depth is polled
  run("hintOn = true; rv = null; clearLive(); ana = null");
  out.modeTimes = {};
  for (const m of ["fast", "normal"]) {
    reset(); bodies.length = 0; run(`setMode("${m}")`); await tick();
    out.modeTimes[m] = bodies.filter(b => b[0] === "/api/analyze").map(b => b[1].time);
    out.modeCalls = calls.slice();
  }
  reset(); run('setMode("deep")');  // the reply stays pending: the progress poll runs
  await new Promise(r => setTimeout(r, 650));
  out.polled = calls.includes("/api/progress"); out.liveMsg = els.anaMsg.textContent;
  // 13. a foreign cancel kills the current request: it is repeated once and the result arrives
  run("cancelAnalysis()"); run('setMode("normal")'); run("cancelAnalysis()"); await tick();
  globalThis.__okAnalyze = true; globalThis.__staleCancels = 1; reset();
  run("analyze()"); await tick();
  out.raceCalls = calls.filter(c => c === "/api/analyze").length; out.raceState = run("anaState");
  // 14. cancelled twice in a row: no hang in the busy state
  globalThis.__staleCancels = 5; run("clearLive(); ana = null"); run("analyze()"); await tick();
  out.stuckState = run("anaState");
  // 15. the mode is remembered in localStorage; a broken or missing storage does not break anything
  out.reloaded = run("loadMode()");
  out.stored = store.get("quoridor.analyzer.mode");
  store.set("quoridor.analyzer.mode", "deep"); out.loadedDeep = run("loadMode()");
  store.set("quoridor.analyzer.mode", "bogus"); out.loadedBogus = run("loadMode()");
  store.set("quoridor.analyzer.mode", "constructor"); out.loadedProto = run("loadMode()");
  globalThis.__lsThrow = true;
  out.loadedThrow = run("loadMode()"); run('setMode("fast")'); await tick(); out.modeAfterThrow = run("mode");
  globalThis.__lsThrow = false;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
