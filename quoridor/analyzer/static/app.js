"use strict";
// Только отрисовка и ввод. Правила, легальность ходов и оценки приходят с сервера:
// клиент держит список ходов в нотации сайта и просит сервер проиграть его с начала.
const N = 9, COLS = "abcdefghi", ANALYZE_SECONDS = 5, DEEP_SECONDS = 20;
const $ = id => document.getElementById(id);
const svg = $("board"), NS = "http://www.w3.org/2000/svg";

let line = [];            // ходы партии в нотации сайта (вся партия, не только до текущей позиции)
let ply = 0;              // текущая позиция: первые ply ходов line
let mainLine = null;      // исходная партия, если от неё отошли веткой («Вернуть партию»)
let fromLoad = false;     // список загружен текстом (тогда отмена хода тоже создаёт ветку)
let rv = null;            // разбор партии (см. «Разбор партии»)
let pos = null;           // позиция от сервера (/api/state)
let legal = new Map();    // легальные ходы от сервера: ключ геометрии -> ход {name, k, ...}
let ana = null;           // последний результат /api/analyze для текущей позиции
let anaState = "idle";    // idle | busy | done
let lastMate = false;     // оценка доказанный выигрыш (флаг с сервера)
let lastEval = null;      // последняя известная оценка (для шкалы, пока считается новая)
let flipped = false, hintOn = true, hover = null;
let seq = 0, aseq = 0, abortCtl = null;  // seq: запросы /api/state, aseq: запросы /api/analyze
let deep = null;          // «Глубокий анализ» в работе: {t0, timer}; null, когда не идёт
let okLine = [], okPly = 0;  // последний подтверждённый сервером список ходов и позиция

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
  if (pos.last && pos.last.k === "w") { const R = wallRect(pos.last.x, pos.last.y, pos.last.o); el("rect", { x: R.x - 2, y: R.y - 2, width: R.w + 4, height: R.h + 4, rx: 4, fill: "none", stroke: css("--fg"), "stroke-width": 1.5, opacity: .8, "pointer-events": "none" }); }
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
const sleep = ms => new Promise(r => setTimeout(r, ms));
// Позиция = первые ply ходов списка line. Сервер сам проигрывает их с начала.
async function refresh() {
  const my = ++seq;
  stopAnalysis(!hintOn);  // подсказка выключена: нового анализа не будет, старый расчёт на сервере не нужен
  let res;
  try { res = await api("/api/state", { moves: line.slice(0, ply) }); } catch (_) { if (my === seq) { toast("Нет связи с сервером"); rollback(); } return; }
  if (my !== seq) return;
  if (!res.ok) {
    toast(res.error);
    // Откатываем только при ошибке в конкретном ходе; иначе (403, 400 без номера) стоп, без цикла.
    if (res.bad_move != null) { line = line.slice(0, res.bad_move - 1); ply = Math.min(ply, line.length); return refresh(); }
    rollback();
    return;
  }
  okLine = line.slice(); okPly = ply;
  pos = res.position; legal = new Map(pos.legal.map(m => [keyOf(m), m]));
  render();
  if (hintOn) showHint();
}
// Неподтверждённый ход не должен остаться в списке: возвращаем последнее подтверждённое.
function rollback() { line = okLine.slice(); ply = okPly; if (pos) render(); }
// Останавливает анализ: клиентский запрос и (cancel) вычисление на сервере.
function stopAnalysis(cancel) {
  aseq++;
  if (abortCtl) abortCtl.abort();
  abortCtl = null; ana = null; anaState = "idle";
  clearDeep();
  if (cancel) api("/api/cancel", {}).catch(() => {});
}
// Подсказка для текущей позиции: из разбора, если он её уже посчитал; пока идёт разбор, не мешаем ему.
function showHint() {
  if (!pos || pos.winner !== null) return;
  if (applyReviewHint()) { api("/api/cancel", {}).catch(() => {}); return; }
  if (reviewRunning()) { api("/api/cancel", {}).catch(() => {}); anaState = "review"; render(); return; }
  analyze();
}
function applyReviewHint() {
  const st = reviewStep(ply);
  if (!st || !st.top.length || !hintOn) return false;
  if (abortCtl) abortCtl.abort();
  aseq++; abortCtl = null;
  ana = { top: st.top, eval: st.eval, eval_mate: st.eval_mate, depth: st.depth, elapsed: st.elapsed, review: true };
  anaState = "done"; lastEval = st.eval; lastMate = st.eval_mate;
  render();
  return true;
}
async function analyze(seconds = ANALYZE_SECONDS, isDeep = false) {
  const my = ++aseq;
  anaState = "busy"; renderEval(); renderBest();
  abortCtl = new AbortController();
  try {
    const res = await api("/api/analyze", { moves: line.slice(0, ply), time: seconds }, abortCtl.signal);
    if (my !== aseq || res.cancelled) return;
    clearDeep();
    if (!res.ok) { anaState = "idle"; toast(res.error); render(); return; }
    ana = res; anaState = "done"; lastEval = res.eval; lastMate = res.eval_mate;
    if (isDeep) ana.deep = seconds;
    render();
  } catch (e) { if (e.name !== "AbortError" && my === aseq) { clearDeep(); anaState = "idle"; toast("Анализ не удался"); render(); } }
}
// ---------- Глубокий анализ ----------
// Тот же /api/analyze, но time=20 и по явной кнопке. Живой глубины сервер по ходу поиска не отдаёт,
// поэтому показываем прошедшее время; глубина и «глубокий, 20 с» появляются в результате.
function clearDeep() { if (deep) { clearInterval(deep.timer); deep = null; renderDeep(); } }
function deepElapsed() { return deep ? Math.min(DEEP_SECONDS, (Date.now() - deep.t0) / 1000) : 0; }
function renderDeep() {
  const prog = $("deepProg"), btn = $("bDeep");
  prog.hidden = !deep;
  if (deep) { prog.max = DEEP_SECONDS; prog.value = deepElapsed(); }
  btn.classList.toggle("busy", !!deep);
  $("deepLbl").textContent = deep ? " Отменить" : " Глубокий анализ";
  if (deep) $("anaMsg").textContent = `Глубокий анализ: ${deepElapsed().toFixed(0)} из ${DEEP_SECONDS} с. Esc отменяет.`;
}
function startDeep() {
  if (deep) { cancelDeep(); return; }
  if (!pos || pos.winner !== null) { toast("Ходов нет: партия окончена"); return; }
  if (!hintOn) { toast("Включите подсказку (H)"); return; }
  if (reviewRunning()) { toast("Идёт разбор партии"); return; }
  stopAnalysis(true);  // прежний расчёт (на клиенте и на сервере) больше не нужен
  deep = { t0: Date.now(), timer: setInterval(renderDeep, 250) };
  renderDeep();
  analyze(DEEP_SECONDS, true);
}
// Отмена: прекращаем поиск и возвращаем обычную подсказку для этой позиции.
function cancelDeep() {
  if (!deep) return;
  stopAnalysis(true);
  render();
  if (hintOn) showHint();
}
// Отрезает хвост списка (ветка): исходная партия запоминается для «Вернуть партию».
function cut(n) {
  if (!mainLine && line.length > n && (fromLoad || ply < line.length)) mainLine = line.slice();
  line = line.slice(0, n);
}
function play(m) {
  if (!pos) return;
  if (pos.winner !== null) { toast("Партия окончена"); return; }
  const mv = legal.get(keyOf(m));
  if (!mv) {
    if (m.k === "w") toast(pos.walls_left[pos.turn] <= 0 ? "Стенки закончились" : "Здесь стенку поставить нельзя");
    return;
  }
  hover = null;
  if (ply < line.length) {
    if (line[ply] === mv.name) { ply++; refresh(); return; }  // тот же ход, что в партии: не ветка
    cut(ply);
  }
  line.push(mv.name); ply = line.length; refresh();
}
function goto(p) {
  p = Math.max(0, Math.min(line.length, p));
  if (p === ply) return;
  ply = p; hover = null; refresh();
}

// ---------- Разбор партии ----------
// rv: {id, moves, total, steps, summary, finished, cancelled, error}. Метки и график относятся к rv.moves;
// в ветке они остаются у общего начала (commonLen), дальше ходы подсвечены как ветка.
function reviewRunning() { return !!rv && !rv.finished; }
function commonLen() {
  if (!rv) return 0;
  let k = 0;
  while (k < rv.moves.length && k < line.length && rv.moves[k] === line[k]) k++;
  return k;
}
function reviewStep(p) { return rv && p <= commonLen() && rv.steps[p] ? rv.steps[p] : null; }
async function startReview() {
  if (!line.length) { toast("Нет ходов для разбора"); return; }
  if (reviewRunning()) return;
  const moves = line.slice();
  const t = Math.min(10, Math.max(0.1, parseFloat($("revTime").value) || 1));
  $("revTime").value = t;
  const mine = rv = { id: null, moves, total: moves.length + 1, steps: [], summary: null, finished: false, cancelled: false, error: null };
  stopAnalysis(true); render();
  let res;
  try { res = await api("/api/review", { moves, time: t }); } catch (_) { res = { ok: false, error: "Нет связи с сервером" }; }
  if (rv !== mine) return;
  if (!res.ok) { rv = null; toast(res.error); render(); if (hintOn) showHint(); return; }
  mine.id = res.id; mine.total = res.total;
  pollReview(mine);
}
async function pollReview(mine) {
  while (rv === mine && !mine.finished) {
    await sleep(350);
    if (rv !== mine) return;
    let res;
    try { res = await api("/api/review/status", { id: mine.id, since: mine.steps.length }); } catch (_) { res = { ok: false, error: "Нет связи с сервером" }; }
    if (rv !== mine) return;
    if (!res.ok) { mine.error = res.error; mine.finished = true; break; }
    mine.steps.push(...res.results); mine.summary = res.summary;
    mine.finished = res.finished; mine.cancelled = res.cancelled; mine.error = res.error;
    if (pos && !ana && hintOn) applyReviewHint();
    renderReview();
  }
  if (rv !== mine) return;
  if (mine.error) toast("Разбор: " + mine.error);
  render();
  if (hintOn && !ana && anaState !== "busy") showHint();
}
function stopReview() {
  if (!reviewRunning()) return;
  rv.cancelled = true;
  api("/api/review/cancel", {}).catch(() => {});
  renderReview();
}
// Сводка по текущему разбору: все данные считает сервер.
function renderSummary() {
  const el_ = $("summary");
  if (!rv || !rv.summary || !rv.steps.length) { el_.innerHTML = ""; return; }
  const w = rv.summary.white, b = rv.summary.black, acc = x => x === null ? "—" : x.toFixed(0) + "%";
  el_.innerHTML =
    `<span></span><span class="h">Белые</span><span class="h">Чёрные</span>` +
    `<span class="h">Точность</span><span>${acc(w.accuracy)}</span><span>${acc(b.accuracy)}</span>` +
    `<span class="h mk g">Лучший единств. !</span><span>${w.g}</span><span>${b.g}</span>` +
    `<span class="h mk b">Грубые ??</span><span>${w.b}</span><span>${b.b}</span>` +
    `<span class="h mk m">Ошибки ?</span><span>${w.m}</span><span>${b.m}</span>` +
    `<span class="h mk i">Неточности ?!</span><span>${w.i}</span><span>${b.i}</span>`;
}
function renderReviewUi() {
  const prog = $("revProg"), btn = $("bReview"), msg = $("loadMsg");
  prog.hidden = !rv;
  if (rv) { prog.max = rv.total; prog.value = rv.steps.length; }
  btn.textContent = reviewRunning() ? (rv.cancelled ? "Останавливаю…" : "Остановить разбор") : "Разобрать партию";
  btn.disabled = reviewRunning() && rv.cancelled;
  if (rv) {
    const n = rv.steps.length;
    msg.className = "msg" + (rv.error ? " err" : "");
    msg.textContent = rv.error ? "Ошибка разбора: " + rv.error
      : !rv.finished ? `Разбор: ${n} из ${rv.total} позиций`
      : rv.cancelled && n < rv.total ? `Разбор остановлен: ${n} из ${rv.total} позиций`
      : `Разбор готов: ${rv.total} позиций, ${rv.steps.reduce((a, s) => a + s.elapsed, 0).toFixed(0)} с`;
  }
}
function renderReview() { renderMoves(); renderSummary(); renderReviewUi(); drawGraph(); renderMoveInfo(); }
const MK = { b: "??", m: "?", i: "?!", g: "!" };
function renderMoveInfo() {
  const el_ = $("mvInfo"), st = ply > 0 ? reviewStep(ply - 1) : null;
  if (!st || !st.move || !hintOn) { el_.textContent = ""; return; }
  const best = st.top.length ? st.top[0].name : "";
  const loss = (st.loss / 100).toFixed(1);
  el_.innerHTML = st.loss === 0 || best === st.move
    ? `Ход <b>${st.move}</b>${st.mark ? " " + MK[st.mark] : ""}: лучший по оценке бота.`
    : `Ход <b>${st.move}</b>${st.mark ? " " + MK[st.mark] : ""}: потеря ${loss} шага, лучше было <b>${best}</b>.`;
}
// График относится к ходам разбора: в ветке клик правее общего начала не уводит на ходы ветки.
function graphGoto(i) { goto(Math.min(i, commonLen())); }
function drawGraph() {
  const g = $("graph"); g.innerHTML = "";
  const Wd = 600, Hd = 120, P = 8, LIM = 800;
  const mk = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); g.appendChild(e); return e; };
  mk("rect", { x: 0, y: 0, width: Wd, height: Hd / 2, fill: "#2a2e35" });
  mk("rect", { x: 0, y: Hd / 2, width: Wd, height: Hd / 2, fill: "#14161a" });
  mk("line", { x1: 0, y1: Hd / 2, x2: Wd, y2: Hd / 2, stroke: css("--line") });
  mk("text", { x: Wd - 6, y: 14, "text-anchor": "end", "font-size": 10, fill: css("--muted") }).textContent = "перевес белых";
  mk("text", { x: Wd - 6, y: Hd - 6, "text-anchor": "end", "font-size": 10, fill: css("--muted") }).textContent = "перевес чёрных";
  if (!rv || !rv.steps.length) {
    mk("text", { x: Wd / 2, y: Hd / 2 - 6, "text-anchor": "middle", "font-size": 12, fill: css("--faint") }).textContent = "График появится после разбора партии";
    g.onclick = null; return;
  }
  const n = rv.moves.length + 1, S_ = rv.steps;
  const xs = i => P + (n > 1 ? i * (Wd - 2 * P) / (n - 1) : 0);
  const ys = v => Hd / 2 - Math.max(-LIM, Math.min(LIM, v)) * (Hd / 2 - P) / LIM;
  let d = `M${xs(0)},${Hd / 2}`; S_.forEach((s, i) => { d += ` L${xs(i)},${ys(s.eval)}`; }); d += ` L${xs(S_.length - 1)},${Hd / 2} Z`;
  mk("path", { d, fill: "rgba(242,242,242,.18)" });
  mk("polyline", { points: S_.map((s, i) => `${xs(i)},${ys(s.eval)}`).join(" "), fill: "none", stroke: css("--fg"), "stroke-width": 1.5 });
  S_.forEach((s, i) => {
    if ((s.mark === "b" || s.mark === "m") && S_[i + 1]) mk("circle", { cx: xs(i + 1), cy: ys(S_[i + 1].eval), r: 3.5, fill: s.mark === "b" ? css("--blunder") : css("--mistake") });
  });
  if (ply <= commonLen()) mk("line", { x1: xs(ply), y1: 0, x2: xs(ply), y2: Hd, stroke: css("--hint"), "stroke-width": 2 });
  g.onclick = e => {
    const pt = g.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(g.getScreenCTM().inverse());
    graphGoto(Math.round((p.x - P) / ((Wd - 2 * P) / Math.max(1, n - 1))));
  };
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
  if (!ana) { best.innerHTML = `<div class="hidden-hint">${anaState === "busy" ? (deep ? "Глубокий анализ…" : "Считаю…") : anaState === "review" ? "Идёт разбор партии, подсказка появится для разобранных позиций." : ""}</div>`; msg.textContent = ""; return; }
  best.innerHTML = ana.top.map((x, i) => `<div class="bm${i ? "" : " top"}" data-i="${i}" tabindex="0" role="button"><span class="rk">${i + 1}</span><span class="mv">${x.name}</span><span class="why" title="${x.why} · глубина ${x.depth}">${x.why} · гл. ${x.depth}</span><span class="ev">${x.exact || x.mate ? "" : "≤ "}${fmtE(x.score, x.mate)}</span></div>`).join("");
  best.querySelectorAll(".bm").forEach(d => { const go = () => play(ana.top[+d.dataset.i]); d.onclick = go; d.onkeydown = ev => { if (ev.key === "Enter") go(); }; });
  msg.textContent = `${ana.deep ? `Глубокий, ${ana.deep} с: г` : ana.review ? "Из разбора: г" : "Г"}лубина ${ana.depth}, ${ana.elapsed.toFixed(1)} с. Оценка в шагах, плюс — белым; «≤» — оценка не лучше указанной.`;
}
function renderMoves() {
  const k = commonLen();
  let h = "";
  for (let i = 0; i < line.length; i += 2) {
    h += `<span class="n">${i / 2 + 1}.</span>`;
    for (const j of [i, i + 1]) {
      if (j >= line.length) { h += "<span></span>"; continue; }
      const isVar = mainLine && (j >= mainLine.length || mainLine[j] !== line[j]);
      const st = rv && j < k ? rv.steps[j] : null;
      const mk = st && st.mark ? `<span class="mk ${st.mark}" title="потеря ${(st.loss / 100).toFixed(1)} шага">${MK[st.mark]}</span>` : "";
      h += `<span class="mvc${j === ply - 1 ? " cur" : ""}${isVar ? " var" : ""}" data-p="${j + 1}">${line[j]}${mk}</span>`;
    }
  }
  const box = $("moves");
  box.innerHTML = h || `<span class="n"></span><span class="hidden-hint">Сделайте ход на доске или загрузите партию.</span>`;
  box.querySelectorAll(".mvc").forEach(d => { d.onclick = () => goto(+d.dataset.p); });
  const cur = box.querySelector(".cur"); if (cur) cur.scrollIntoView({ block: "nearest" });
  $("bRestore").hidden = !mainLine;
}
function render() {
  drawBoard(); renderEval(); renderBest(); renderMoves(); renderSummary(); renderReviewUi(); drawGraph(); renderMoveInfo(); renderDeep();
  if (!pos) return;
  const [dW, dB] = pos.dist, bar = n => "▮".repeat(n) + "<i>" + "▯".repeat(10 - n) + "</i>";
  $("status").innerHTML =
    `<span class="dot b"></span><span>Чёрные</span><span class="dist">до финиша ${dB}</span>` +
    `<span></span><span class="walls">${bar(pos.walls_left[1])}</span><span class="dist">${pos.walls_left[1]} стен.</span>` +
    `<span class="dot w"></span><span>Белые</span><span class="dist">до финиша ${dW}</span>` +
    `<span></span><span class="walls">${bar(pos.walls_left[0])}</span><span class="dist">${pos.walls_left[0]} стен.</span>`;
  $("turn").innerHTML = pos.winner !== null ? `<b>Победили ${pos.winner === 0 ? "белые" : "чёрные"}</b>` : `Ход: <b>${pos.turn === 0 ? "белые" : "чёрные"}</b> · позиция ${ply} из ${line.length}`;
  ["bFirst", "bPrev"].forEach(id => { $(id).disabled = ply === 0; });
  ["bNext", "bLast"].forEach(id => { $(id).disabled = ply >= line.length; });
}

// ---------- Управление ----------
function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("show"), 1800); }
$("bFirst").onclick = () => goto(0);
$("bPrev").onclick = () => goto(ply - 1);
$("bNext").onclick = () => goto(ply + 1);
$("bLast").onclick = () => goto(line.length);
$("bUndo").onclick = () => { if (ply > 0) { cut(ply - 1); ply = line.length; refresh(); } };
$("bFlip").onclick = () => { flipped = !flipped; render(); toast(flipped ? "Чёрные снизу" : "Белые снизу"); };
$("bHint").onclick = () => {
  hintOn = !hintOn; $("bHint").classList.toggle("on", hintOn);
  $("hintLbl").textContent = " Подсказка: " + (hintOn ? "вкл" : "выкл");
  if (hintOn) refresh();
  else { stopAnalysis(true); render(); }
};
$("bDeep").onclick = startDeep;
$("bNew").onclick = () => {
  if (rv) { if (reviewRunning()) api("/api/review/cancel", {}).catch(() => {}); rv = null; }
  line = []; ply = 0; mainLine = null; fromLoad = false; lastEval = null; $("loadMsg").textContent = ""; refresh();
};
$("bRestore").onclick = () => { if (!mainLine) return; line = mainLine; mainLine = null; ply = Math.min(ply, line.length); refresh(); toast("Партия восстановлена"); };
$("bCopy").onclick = () => {
  const t = line.join(" "), done = () => toast("Ходы скопированы");
  try { navigator.clipboard.writeText(t).then(done, () => { $("pgn").value = t; $("pgn").select(); toast("Ходы в поле ниже: Ctrl+C"); }); } catch (_) { $("pgn").value = t; $("pgn").select(); toast(t ? "Ходы в поле ниже: Ctrl+C" : "Нет ходов"); }
};
$("bLoad").onclick = async () => {
  const msg = $("loadMsg");
  let res;
  try { res = await api("/api/parse", { text: $("pgn").value }); } catch (_) { res = { ok: false, error: "Нет связи с сервером" }; }
  if (!res.ok) { msg.className = "msg err"; msg.textContent = res.error; return; }
  if (reviewRunning()) api("/api/review/cancel", {}).catch(() => {});
  rv = null; line = res.moves; ply = line.length; mainLine = null; fromLoad = true;
  msg.className = "msg"; msg.textContent = `Загружено ${line.length} полуходов`;
  startReview();
  refresh();
};
$("bReview").onclick = () => { if (reviewRunning()) stopReview(); else startReview(); };
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "Escape" && deep) { cancelDeep(); e.preventDefault(); return; }
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  const k = e.key;
  if (k === "ArrowLeft") { $("bPrev").click(); e.preventDefault(); }
  else if (k === "ArrowRight") { $("bNext").click(); e.preventDefault(); }
  else if (k === "Home") { $("bFirst").click(); e.preventDefault(); }
  else if (k === "End") { $("bLast").click(); e.preventDefault(); }
  else if (k === "Backspace") { $("bUndo").click(); e.preventDefault(); }
  else if (k === "f" || k === "F" || k === "а" || k === "А") $("bFlip").click();
  else if (k === "h" || k === "H" || k === "р" || k === "Р") $("bHint").click();
});
refresh();
