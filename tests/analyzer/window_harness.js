// Second harness (does not touch client_harness.js): runs app.js with window, location,
// URLSearchParams, document.body and a mock window.pywebview.api, to cover the window block.
// usage: node window_harness.js app.js '{"search": "?nowin=error", "native": false, "script": ["wait:700", "snap:a"]}'
// Actions: wait:MS, snap:NAME, late (pywebview appears + pywebviewready), key:X, click:ID.
const vm = require("vm"), fs = require("fs");
const cfg = JSON.parse(process.argv[3] || "{}");
const fetchLog = [], apiCalls = [], store = new Map(), readyHandlers = [];
let keyHandler = null;

function stub(id) {
  const cls = new Set();
  const attrs = {};
  const t = {
    id, style: {}, dataset: {}, hidden: false, value: "1", children: [], textContent: "", onclick: null,
    classList: {
      toggle(c, force) { const on = force === undefined ? !cls.has(c) : !!force; if (on) cls.add(c); else cls.delete(c); return on; },
      add(c) { cls.add(c); }, remove(c) { cls.delete(c); }, contains: c => cls.has(c),
    },
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute: k => (k in attrs ? attrs[k] : null),
    click() { if (this.onclick) return this.onclick({}); },
    querySelectorAll: () => [], querySelector: () => null, appendChild() {}, addEventListener() {}, scrollIntoView() {},
    createSVGPoint: () => ({ matrixTransform: () => ({ x: 0, y: 0 }) }),
    getScreenCTM: () => ({ inverse: () => ({}) }),
  };
  return t;
}
const els = {};
const get = id => els[id] || (els[id] = stub(id));
// as in index.html: the pin and the note start hidden
get("bPin").hidden = true; get("winNote").hidden = true;
get("bPin").setAttribute("aria-pressed", "false"); get("bCompact").setAttribute("aria-pressed", "false");
const body = stub("body");
const doc = { getElementById: get, createElementNS: () => stub(), documentElement: {}, body,
              addEventListener: (t, f) => { if (t === "keydown") keyHandler = f; } };

const api = {
  get_settings: async () => { apiCalls.push(["get_settings"]); if (cfg.getSettingsThrows) throw new Error("boom");
    return Object.assign({ on_top: false, compact: false }, cfg.settings || {}); },
  set_compact: async on => { apiCalls.push(["set_compact", on]); return on; },
  set_on_top: async on => { apiCalls.push(["set_on_top", on]); if (cfg.setOnTopThrows) throw new Error("boom"); return on; },
};
const win = { addEventListener: (t, f) => { if (t === "pywebviewready") readyHandlers.push(f); } };
if (cfg.native) win.pywebview = { api };

const POS = { pawns: [{c: 4, r: 0}, {c: 4, r: 8}], walls: [], walls_left: [10, 10], dist: [8, 8],
              turn: 0, winner: null, last: null, legal: [] };
const ctx = { document: doc, window: win, location: { search: cfg.search || "" }, URLSearchParams,
  getComputedStyle: () => ({ getPropertyValue: () => "#000" }), navigator: {},
  localStorage: { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
  setTimeout, clearTimeout, setInterval, clearInterval, Date, console, AbortController, Math, JSON,
  fetch: async (path, o) => { fetchLog.push([path, o && o.body ? JSON.parse(o.body) : null]); return ({ json: async () => (path === "/api/state" ? { ok: true, position: POS } : { ok: true, cancelled: true }) }); } };
if (cfg.storedCompact !== undefined) store.set("quoridor.analyzer.compact", cfg.storedCompact);
vm.createContext(ctx);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const snap = () => ({
  pinHidden: get("bPin").hidden, pinPressed: get("bPin").getAttribute("aria-pressed"),
  noteHidden: get("winNote").hidden, noteText: get("winNote").textContent,
  compact: body.classList.contains("compact"), compactPressed: get("bCompact").getAttribute("aria-pressed"),
  stored: store.get("quoridor.analyzer.compact") === undefined ? null : store.get("quoridor.analyzer.compact"),
  calls: apiCalls.map(c => c.slice()),
  fetches: fetchLog.map(c => c.slice()),
  armed: get("bNew").classList.contains("armed"),
  state: vm.runInContext("JSON.stringify({line, ply, mainLine, aseq})", ctx),
});
(async () => {
  vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
  const snaps = {};
  for (const a of cfg.script || []) {
    const [op, arg] = [a.split(":")[0], a.slice(a.indexOf(":") + 1)];
    if (op === "wait") await sleep(Number(arg));
    else if (op === "snap") snaps[arg] = snap();
    else if (op === "late") { win.pywebview = { api }; readyHandlers.forEach(f => f()); await sleep(50); }
    else if (op === "key") { keyHandler({ key: arg, target: {}, preventDefault() {} }); await sleep(30); }
    else if (op === "eval") { vm.runInContext(arg, ctx); }
    else if (op === "click") { await get(arg).onclick({}); await sleep(30); }
  }
  console.log(JSON.stringify({ snaps, readyHandlers: readyHandlers.length }));
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
