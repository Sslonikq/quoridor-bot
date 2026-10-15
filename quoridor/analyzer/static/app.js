"use strict";
// Только отрисовка и ввод. Правила, легальность ходов и оценки приходят с сервера:
// клиент держит список ходов в нотации сайта и просит сервер проиграть его с начала.
const N = 9, COLS = "abcdefghi", ANALYZE_SECONDS = 2;
const $ = id => document.getElementById(id);
const svg = $("board"), NS = "http://www.w3.org/2000/svg";

let line = [];            // ходы в нотации сайта
let pos = null;           // позиция от сервера (/api/state)
let legal = new Map();    // легальные ходы от сервера: ключ геометрии -> ход {name, k, ...}
let ana = null;           // последний результат /api/analyze для текущей позиции
let anaState = "idle";    // idle | busy | done
let lastMate = false;     // оценка доказанный выигрыш (флаг с сервера)
let lastEval = null;      // последняя известная оценка (для шкалы, пока считается новая)
let flipped = false, hintOn = true, hover = null;
let seq = 0, abortCtl = null;

// ---------- Геометрия (только отрисовка; имена ходов и геометрию присылает сервер) ----------
// Ключ хода по геометрии: клиент ищет ход среди legal, имён не разбирает и не собирает.
const keyOf = m => m.k === "p" ? `p:${m.c}:${m.r}` : `w:${m.x}:${m.y}:${m.o}`;

const PAD = 24, S = 52, G = 10, STEP = S + G;
const MONO = "ui-monospace, Consolas, monospace";
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const el = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); svg.appendChild(e); return e; };
const sc = c => flipped ? 8 - c : c, sr = r => flipped ? r : 8 - r;
const gxS = g => flipped ? 9 - g : g, gyS = g => flipped ? g : 9 - g;
const X = g => PAD + g * STEP - G / 2, cellX = c => PAD + sc(c) * STEP, cellY = r => PAD + sr(r) * STEP;
function wallRect(x, y, o) {
  if (o === "h") { const yy = X(gyS(y + 1)), a = X(gxS(x)), b = X(gxS(x + 2)); const l = Math.min(a, b) + G / 2 + 1, r = Math.max(a, b) - G / 2 - 1; return { x: l, y: yy - G / 2 + 1, w: r - l, h: G - 2 }; }
  const xx = X(gxS(x + 1)), a = X(gyS(y)), b = X(gyS(y + 2)); const t = Math.min(a, b) + G / 2 + 1, bt = Math.max(a, b) - G / 2 - 1; return { x: xx - G / 2 + 1, y: t, w: G - 2, h: bt - t };
}
function pick(evt) {
  const pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  let gx = (p.x - PAD + G / 2) / STEP, gy = (p.y - PAD + G / 2) / STEP;
  if (gx < 0 || gx > 9 || gy < 0 || gy > 9) return null;
  gx = flipped ? 9 - gx : gx; gy = flipped ? gy : 9 - gy;
  const thr = (G / 2 + 5) / STEP, dv = Math.abs(gx - Math.round(gx)), dh = Math.abs(gy - Math.round(gy));
  const clamp = v => Math.max(0, Math.min(7, v));
  if (dv < thr && dv <= dh && Math.round(gx) > 0 && Math.round(gx) < 9) return { k: "w", o: "v", x: Math.round(gx) - 1, y: clamp(Math.round(gy - 1)) };
  if (dh < thr && Math.round(gy) > 0 && Math.round(gy) < 9) return { k: "w", o: "h", y: Math.round(gy) - 1, x: clamp(Math.round(gx - 1)) };
  return { k: "p", c: Math.min(8, Math.floor(gx)), r: Math.min(8, Math.floor(gy)) };
}

// ---------- Доска ----------
function drawBoard() {
  svg.innerHTML = "";
  el("rect", { x: 0, y: 0, width: 600, height: 600, rx: 8, fill: css("--gap") });
  for (let c = 0; c < N; c++) for (let r = 0; r < N; r++)
    el("rect", { x: cellX(c), y: cellY(r), width: S, height: S, rx: 3, fill: (c + r) % 2 ? css("--cell") : css("--cell-alt") });
  for (let c = 0; c < N; c++) el("text", { x: cellX(c) + S - 5, y: cellY(flipped ? 8 : 0) + S - 5, "text-anchor": "end", "font-size": 11, "font-family": MONO, fill: css("--faint") }).textContent = COLS[c];
  for (let r = 0; r < N; r++) el("text", { x: cellX(flipped ? 8 : 0) + 5, y: cellY(r) + 14, "font-size": 11, "font-family": MONO, fill: css("--faint") }).textContent = r + 1;
  if (!pos) return;
  if (pos.last) {  // последний ход
    if (pos.last.k === "p") { const { c, r } = pos.last; el("rect", { x: cellX(c) + 1, y: cellY(r) + 1, width: S - 2, height: S - 2, rx: 3, fill: "none", stroke: "#4a5562", "stroke-width": 2 }); }
  }
  for (const m of legal.values()) if (m.k === "p") { const { c, r } = m; el("circle", { cx: cellX(c) + S / 2, cy: cellY(r) + S / 2, r: 6, fill: "#ffffff", opacity: .16, "pointer-events": "none" }); }
  for (const w of pos.walls) { const R = wallRect(w.x, w.y, w.o); el("rect", { x: R.x, y: R.y, width: R.w, height: R.h, rx: 2, fill: css("--wall") }); }
  drawHint();
  pos.pawns.forEach((p, i) => { const { c, r } = p; el("circle", { cx: cellX(c) + S / 2, cy: cellY(r) + S / 2, r: 19, fill: css(i ? "--black-pawn" : "--white-pawn"), stroke: i ? "#5d636b" : "#9aa0a6", "stroke-width": 2 }); });
  if (hover && pos.winner === null) {
    const mv = legal.get(keyOf(hover)), ok = !!mv, name = ok ? mv.name : "";
    if (hover.k === "w") {
      const R = wallRect(hover.x, hover.y, hover.o);
      el("rect", { x: R.x, y: R.y, width: R.w, height: R.h, rx: 2, fill: ok ? css("--wall") : css("--bad"), opacity: ok ? .55 : .45, "pointer-events": "none" });
      el("text", { x: R.x + R.w / 2, y: R.y - 6, "text-anchor": "middle", "font-size": 12, "font-family": MONO, fill: ok ? css("--fg") : css("--bad"), "pointer-events": "none" }).textContent = ok ? name : "✕";
    } else if (ok) el("rect", { x: cellX(hover.c) + 1, y: cellY(hover.r) + 1, width: S - 2, height: S - 2, rx: 3, fill: "none", stroke: css("--fg"), "stroke-width": 2, opacity: .5, "pointer-events": "none" });
  }
}
function drawHint() {
  if (!hintOn || !ana || !ana.top.length) return;
  const best = ana.top[0];
  if (best.k === "w") {
    const R = wallRect(best.x, best.y, best.o);
    el("rect", { x: R.x - 3, y: R.y - 3, width: R.w + 6, height: R.h + 6, rx: 5, fill: css("--hint-soft") });
    el("rect", { x: R.x, y: R.y, width: R.w, height: R.h, rx: 2, fill: css("--hint"), opacity: .85 });
    return;
  }
  const { c: c0, r: r0 } = pos.pawns[pos.turn], { c: c1, r: r1 } = best;
  const x1 = cellX(c0) + S / 2, y1 = cellY(r0) + S / 2, x2 = cellX(c1) + S / 2, y2 = cellY(r1) + S / 2;
  const L = Math.hypot(x2 - x1, y2 - y1), ux = (x2 - x1) / L, uy = (y2 - y1) / L;
  el("circle", { cx: x2, cy: y2, r: 20, fill: "none", stroke: css("--hint"), "stroke-width": 4 });
  el("line", { x1: x1 + ux * 22, y1: y1 + uy * 22, x2: x2 - ux * 26, y2: y2 - uy * 26, stroke: css("--hint"), "stroke-width": 5, "stroke-linecap": "round", opacity: .9 });
  const hx = x2 - ux * 24, hy = y2 - uy * 24;
  el("polygon", { points: `${hx},${hy} ${hx - ux * 12 - uy * 8},${hy - uy * 12 + ux * 8} ${hx - ux * 12 + uy * 8},${hy - uy * 12 - ux * 8}`, fill: css("--hint") });
}
svg.addEventListener("mousemove", e => { const h = pick(e); if (JSON.stringify(h) !== JSON.stringify(hover)) { hover = h; drawBoard(); } });
svg.addEventListener("mouseleave", () => { hover = null; drawBoard(); });
svg.addEventListener("click", e => { const h = pick(e); if (h) play(h); });

// ---------- Обмен с сервером ----------
async function api(path, body, signal) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  return r.json();
}
async function refresh() {
  const my = ++seq;
  if (abortCtl) abortCtl.abort();
  abortCtl = null; ana = null; anaState = "idle";
  let res;
  try { res = await api("/api/state", { moves: line }); } catch (_) { toast("Нет связи с сервером"); return; }
  if (my !== seq) return;
  if (!res.ok) {
    toast(res.error);
    // Откатываем только при ошибке в конкретном ходе; иначе (403, 400 без номера) стоп, без цикла.
    if (res.bad_move != null) { line = line.slice(0, res.bad_move - 1); return refresh(); }
    return;
  }
  pos = res.position; legal = new Map(pos.legal.map(m => [keyOf(m), m]));
  render();
  if (hintOn && pos.winner === null) analyze(my);
}
async function analyze(my) {
  anaState = "busy"; renderEval(); renderBest();
  abortCtl = new AbortController();
  try {
    const res = await api("/api/analyze", { moves: line, time: ANALYZE_SECONDS }, abortCtl.signal);
    if (my !== seq || res.cancelled) return;
    if (!res.ok) { anaState = "idle"; toast(res.error); return; }
    ana = res; anaState = "done"; lastEval = res.eval; lastMate = res.eval_mate;
    render();
  } catch (e) { if (e.name !== "AbortError") { anaState = "idle"; toast("Анализ не удался"); renderBest(); } }
}
function play(m) {
  if (!pos) return;
  if (pos.winner !== null) { toast("Партия окончена"); return; }
  const mv = legal.get(keyOf(m));
  if (!mv) {
    if (m.k === "w") toast(pos.walls_left[pos.turn] <= 0 ? "Стенки закончились" : "Здесь стенку поставить нельзя");
    return;
  }
  line.push(mv.name); hover = null; refresh();
}

// ---------- Панели ----------
function fmtE(e, mate) {
  if (e === null || e === undefined) return "—";
  if (mate) return (e > 0 ? "Б+" : "Ч+") + "∞";
  const v = e / 100;
  return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);
}
function renderEval() {
  const e = hintOn ? lastEval : null, bar = $("evalbar"), num = $("evalNum");
  bar.classList.toggle("stale", anaState === "busy");
  const steps = e === null ? 0 : Math.max(-60, Math.min(60, e / 100));
  $("evalW").style.height = (50 + 50 * Math.tanh(steps / 5)) + "%";
  num.textContent = fmtE(e, lastMate);
  if (e === null || e >= 0) { num.style.bottom = "4px"; num.style.top = ""; num.style.color = "#111"; }
  else { num.style.top = "4px"; num.style.bottom = ""; num.style.color = "#eee"; }
}
function renderBest() {
  const best = $("best"), msg = $("anaMsg");
  if (!hintOn) { best.innerHTML = `<div class="hidden-hint">Подсказка скрыта. Нажмите <kbd>H</kbd>, когда подумаете сами.</div>`; msg.textContent = ""; return; }
  if (pos && pos.winner !== null) { best.innerHTML = `<div class="hidden-hint">Ходов нет: партия окончена.</div>`; msg.textContent = ""; return; }
  if (!ana) { best.innerHTML = `<div class="hidden-hint">${anaState === "busy" ? "Считаю…" : ""}</div>`; msg.textContent = ""; return; }
  best.innerHTML = ana.top.map((x, i) => `<div class="bm${i ? "" : " top"}" data-i="${i}" tabindex="0" role="button"><span class="rk">${i + 1}</span><span class="mv">${x.name}</span><span class="why">${x.why}</span><span class="ev">${fmtE(x.score, x.mate)}</span></div>`).join("");
  best.querySelectorAll(".bm").forEach(d => { const go = () => play(ana.top[+d.dataset.i]); d.onclick = go; d.onkeydown = ev => { if (ev.key === "Enter") go(); }; });
  msg.textContent = `Глубина ${ana.depth} (от корня), ${ana.elapsed.toFixed(1)} с. Оценка в шагах, плюс — белым.`;
}
function render() {
  drawBoard(); renderEval(); renderBest();
  if (!pos) return;
  const [dW, dB] = pos.dist, bar = n => "▮".repeat(n) + "<i>" + "▯".repeat(10 - n) + "</i>";
  $("status").innerHTML =
    `<span class="dot b"></span><span>Чёрные</span><span class="dist">до финиша ${dB}</span>` +
    `<span></span><span class="walls">${bar(pos.walls_left[1])}</span><span class="dist">${pos.walls_left[1]} стен.</span>` +
    `<span class="dot w"></span><span>Белые</span><span class="dist">до финиша ${dW}</span>` +
    `<span></span><span class="walls">${bar(pos.walls_left[0])}</span><span class="dist">${pos.walls_left[0]} стен.</span>`;
  $("turn").innerHTML = pos.winner !== null ? `<b>Победили ${pos.winner === 0 ? "белые" : "чёрные"}</b>` : `Ход: <b>${pos.turn === 0 ? "белые" : "чёрные"}</b> · полуход ${line.length + 1}`;
  let h = "";
  for (let i = 0; i < line.length; i += 2) {
    h += `<span class="n">${i / 2 + 1}.</span>`;
    for (const j of [i, i + 1]) h += j < line.length ? `<span class="mvc${j === line.length - 1 ? " cur" : ""}">${line[j]}</span>` : "<span></span>";
  }
  $("moves").innerHTML = h || `<span class="n"></span><span class="hidden-hint">Сделайте ход на доске.</span>`;
  const cur = $("moves").querySelector(".cur"); if (cur) cur.scrollIntoView({ block: "nearest" });
}

// ---------- Управление ----------
function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("show"), 1800); }
$("bUndo").onclick = () => { if (line.length) { line.pop(); refresh(); } };
$("bFlip").onclick = () => { flipped = !flipped; render(); toast(flipped ? "Чёрные снизу" : "Белые снизу"); };
$("bHint").onclick = () => {
  hintOn = !hintOn; $("bHint").classList.toggle("on", hintOn);
  $("hintLbl").textContent = " Подсказка: " + (hintOn ? "вкл" : "выкл");
  if (hintOn) refresh();
  else { seq++; if (abortCtl) abortCtl.abort(); ana = null; anaState = "idle"; render(); }
};
$("bNew").onclick = () => { line = []; lastEval = null; refresh(); };
$("bCopy").onclick = () => {
  const t = line.join(" "), done = () => toast("Ходы скопированы");
  try { navigator.clipboard.writeText(t).then(done, () => toast(t || "Нет ходов")); } catch (_) { toast(t || "Нет ходов"); }
};
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  if (k === "Backspace") { $("bUndo").click(); e.preventDefault(); }
  else if (k === "f" || k === "F" || k === "а" || k === "А") $("bFlip").click();
  else if (k === "h" || k === "H" || k === "р" || k === "Р") $("bHint").click();
});
refresh();
