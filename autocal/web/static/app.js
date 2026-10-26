/* autocal GUI: hash-routed single page. All data is inserted with textContent / createElement (no innerHTML). */
"use strict";
const ID_RE = /^[0-9]{8}-[0-9]{6}$/;
const main = document.getElementById("main");
const banner = document.getElementById("banner");
const runSelect = document.getElementById("runselect");
let runsCache = [];
let pollToken = 0;

/* ---------- helpers ---------- */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  return e;
}
const fx = (v, d) => (v === null || v === undefined || isNaN(v)) ? "n/a" : Number(v).toFixed(d === undefined ? 1 : d);
const sgn = (v, d) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d === undefined ? 1 : d);

async function api(method, path, body) {
  const opt = { method, headers: {}, cache: "no-store" };
  if (method === "POST") { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body || {}); }
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new Error("cannot reach the server"); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) { const err = new Error((j && j.error) || ("HTTP " + r.status)); err.status = r.status; throw err; }
  return j;
}
function showBanner(msg, isErr) {
  if (!msg) { banner.hidden = true; return; }
  banner.hidden = false; banner.textContent = msg; banner.className = "banner" + (isErr ? " err" : "");
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function chColor(ch) { const i = ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"].indexOf(ch); return "c" + (i < 0 ? 7 : i); }
function chShape(ch) { const i = ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"].indexOf(ch); return SHAPES[i < 0 ? 0 : i]; }
function pmean(arrs) {
  const n = arrs[0].length, out = new Array(n);
  for (let i = 0; i < n; i++) { let s = 0; for (const a of arrs) s += Math.pow(10, a[i] / 10); out[i] = 10 * Math.log10(s / arrs.length); }
  return out;
}
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  const i = bisect(xs, x), j = xs[i] <= x ? i : i - 1, k = Math.min(j + 1, xs.length - 1);
  if (k === j) return ys[j];
  return ys[j] + (ys[k] - ys[j]) * (x - xs[j]) / (xs[k] - xs[j]);
}
function swatch(cls, shape) {
  const s = svgEl("svg", { width: 22, height: 12, viewBox: "0 0 22 12", "aria-hidden": "true" });
  svgEl("line", { x1: 0, x2: 22, y1: 6, y2: 6, class: "ln " + cls, "stroke-width": 2.5 }, s);
  if (shape) marker(s, shape, 11, 6, 3.6, "mk " + cls);
  return s;
}
function chipRow(items, selected, onChange, opts) {
  const row = h("div", { class: "chips", role: "group", "aria-label": (opts && opts.label) || "Choose" });
  const refresh = () => { for (const lab of row.querySelectorAll("label.chip")) lab.classList.toggle("on", lab.firstChild.checked); };
  for (const it of items) {
    const cb = h("input", { type: "checkbox" });
    cb.checked = selected.has(it.key);
    cb.addEventListener("change", () => { if (cb.checked) selected.add(it.key); else selected.delete(it.key); refresh(); onChange(); });
    row.appendChild(h("label", { class: "chip" }, cb, it.swatch || null, it.label));
  }
  refresh();
  row.refresh = () => { for (const lab of row.querySelectorAll("label.chip")) { const k = lab.dataset.key; } };
  return row;
}
function sectionCard(id, title, ...kids) {
  return h("section", { class: "card", id: id, "aria-labelledby": id + "-h" }, h("h2", { id: id + "-h", text: title }), ...kids);
}
function copyBtn(text) {
  const b = h("button", { class: "secondary small", type: "button", "aria-label": "Copy command" }, "Copy");
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy"; }
    setTimeout(() => { b.textContent = "Copy"; }, 1500);
  });
  return b;
}

/* ---------- routing ---------- */
function parseRoute() {
  const m = location.hash.match(/^#\/(run|results)\/([0-9]{8}-[0-9]{6})$/);
  if (m) return { view: m[1], id: m[2] };
  return { view: "home" };
}
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }
window.addEventListener("hashchange", route);

async function refreshRuns() {
  try {
    const j = await api("GET", "/api/runs");
    runsCache = j.runs;
    const r = parseRoute(), sel = r.id || (runSelect.value || "");
    clear(runSelect);
    runSelect.appendChild(h("option", { value: "" }, runsCache.length ? "Choose a run..." : "No runs yet"));
    for (const x of runsCache) {
      const tag = x.verdict ? x.verdict : (x.state || "?");
      const o = h("option", { value: x.id }, `${x.id}  ${x.mode || ""}  ${tag}${x.simulated ? "  simulated" : ""}`);
      if (x.id === sel) o.selected = true;
      runSelect.appendChild(o);
    }
  } catch (e) { /* the poller reports connection trouble */ }
  updateNav();
}
runSelect.addEventListener("change", () => {
  const x = runsCache.find(r => r.id === runSelect.value);
  if (!x) return go("#/");
  go((x.has_results ? "#/results/" : "#/run/") + x.id);
});
function updateNav() {
  const r = parseRoute();
  const id = r.id || runSelect.value || (runsCache[0] && runsCache[0].id) || "";
  document.getElementById("nav-run").setAttribute("href", id ? "#/run/" + id : "#/");
  document.getElementById("nav-results").setAttribute("href", id ? "#/results/" + id : "#/");
  document.getElementById("nav-run").classList.toggle("cur", r.view === "run");
  document.getElementById("nav-results").classList.toggle("cur", r.view === "results");
}
async function route() {
  pollToken++;
  showBanner("");
  const r = parseRoute();
  updateNav();
  clear(main);
  document.title = "autocal" + (r.id ? " " + r.id : "");
  if (r.view === "run") renderRun(r.id);
  else if (r.view === "results") renderResults(r.id);
  else renderHome();
  main.focus({ preventScroll: true });
}

/* ---------- home ---------- */
async function renderHome() {
  const mode = h("select", { id: "newmode", "aria-label": "Mode" }, h("option", { value: "five" }, "Five positions (about 15 min)"), h("option", { value: "single" }, "One position (about 3 min)"));
  const sim = h("input", { type: "checkbox", id: "newsim" });
  sim.checked = true;
  const msg = h("p", { class: "note", role: "status" });
  const start = h("button", { type: "button" }, "Start run");
  start.addEventListener("click", async () => {
    if (!sim.checked && !confirm("This is a real run. It plays test sweeps through your speakers at up to -20 dBFS. Continue?")) return;
    start.disabled = true; msg.textContent = "Starting...";
    try {
      const j = await api("POST", "/api/runs", { mode: mode.value, simulate: sim.checked });
      await refreshRuns();
      if (j.id) go("#/run/" + j.id); else { msg.textContent = "Started. Waiting for the run directory to appear..."; setTimeout(async () => { await refreshRuns(); if (runsCache[0]) go("#/run/" + runsCache[0].id); }, 1500); }
    } catch (e) { msg.textContent = "Could not start: " + e.message; }
    start.disabled = false;
  });
  const list = h("ul", { class: "runlist" });
  main.appendChild(h("h1", { text: "autocal" }));
  main.appendChild(h("section", { class: "card" }, h("h2", { text: "Start a run" }),
    h("div", { class: "row" }, h("label", null, "Mode ", mode), h("label", null, sim, " Simulated (no sound, no hardware)"), start), msg,
    h("p", { class: "note" }, "A real run plays sweeps at up to -20 dBFS and needs the rig and the mic. Nothing is changed on your system by a run; applying is a separate step.")));
  main.appendChild(h("section", { class: "card" }, h("h2", { text: "Runs" }), list));
  await refreshRuns();
  if (!runsCache.length) list.appendChild(h("li", { class: "muted", text: "No runs in this directory yet." }));
  for (const x of runsCache) {
    list.appendChild(h("li", null, h("code", { text: x.id }), h("span", { class: "pill" }, x.mode || "?"), x.simulated ? h("span", { class: "pill sim" }, "simulated") : null,
      h("span", { class: "muted" }, x.verdict ? "verdict " + x.verdict : (x.state || "")),
      h("a", { href: "#/run/" + x.id }, "Live view"), x.has_results ? h("a", { href: "#/results/" + x.id }, "Results") : null));
  }
}

/* ---------- run page ---------- */
const STATE_NAME = { idle: "Idle", preflight: "Preflight", ramp: "Level ramp", channel_id: "Channel check", sweeping: "Sweeping", waiting_user: "Waiting for you", analysing: "Analysing", done: "Done", aborted: "Aborted", error: "Error" };
const STATE_GLYPH = { idle: "○", preflight: "◔", ramp: "▲", channel_id: "♪", sweeping: "∿", waiting_user: "⏸", analysing: "⚙", done: "✔", aborted: "■", error: "⚠" };
const PHASES = [["preflight", "Preflight"], ["ramp", "Level ramp"], ["channel_id", "Channel check"], ["sweeping", "Sweeps"], ["analysing", "Analysis"], ["done", "Done"]];
const TERMINAL = ["done", "aborted", "error"];
const DB_MIN = -80;

function drawMeter(box, mic, level) {
  clear(box);
  const W = Math.max(260, Math.floor(box.clientWidth || 600)), H = 156, L = 10, R = W - 10;
  const x = (db) => L + (Math.min(0, Math.max(DB_MIN, db)) - DB_MIN) / (0 - DB_MIN) * (R - L);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Level meter in dBFS: playback level against the minus 20 dBFS cap, mic peak and RMS against the noise floor" }, box);
  const peak = mic ? mic.peak_dbfs : null, rms = mic ? mic.rms_dbfs : null, floor = mic ? mic.noise_floor_dbfs : null;
  // row 1: playback level
  svgEl("text", { x: L, y: 14, class: "meter-text" }, svg, "Playback level (generator)");
  svgEl("rect", { x: L, y: 22, width: R - L, height: 18, class: "meter-box" }, svg);
  if (level !== null && level !== undefined) svgEl("rect", { x: L, y: 22, width: Math.max(0, x(level) - L), height: 18, class: "meterbar level" }, svg);
  svgEl("line", { x1: x(-20), x2: x(-20), y1: 18, y2: 44, class: "meter-cap" }, svg);
  svgEl("text", { x: x(-20) - 5, y: 55, "text-anchor": "end", class: "meter-text" }, svg, "hard cap −20 dBFS");
  // row 2: mic
  svgEl("text", { x: L, y: 70, class: "meter-text" }, svg, "Mic input: peak (wide bar) and RMS (narrow bar)");
  svgEl("rect", { x: L, y: 78, width: R - L, height: 22, class: "meter-box" }, svg);
  if (peak !== null && peak !== undefined) svgEl("rect", { x: L, y: 78, width: Math.max(0, x(peak) - L), height: 22, class: "meterbar" + (peak > -6 ? " hot" : "") }, svg);
  if (rms !== null && rms !== undefined) svgEl("rect", { x: L, y: 85, width: Math.max(0, x(rms) - L), height: 8, class: "meterbar rms" }, svg);
  if (floor !== null && floor !== undefined) {
    svgEl("line", { x1: x(floor), x2: x(floor), y1: 74, y2: 106, class: "meter-floor" }, svg);
    svgEl("text", { x: x(floor) - 4, y: 118, "text-anchor": x(floor) < 110 ? "start" : "end", class: "meter-text" }, svg, "noise floor " + fx(floor, 0));
  }
  svgEl("line", { x1: x(-6), x2: x(-6), y1: 74, y2: 106, class: "meter-stop" }, svg);
  svgEl("text", { x: x(-6) + 4, y: 118, "text-anchor": x(-6) + 90 > R ? "end" : "start", class: "meter-text" }, svg, "stop at −6");
  // axis
  for (let d = DB_MIN; d <= 0; d += 20) {
    svgEl("line", { x1: x(d), x2: x(d), y1: 126, y2: 131, class: "meter-tick" }, svg);
    svgEl("text", { x: x(d), y: 143, "text-anchor": d === DB_MIN ? "start" : d === 0 ? "end" : "middle", class: "meter-text" }, svg, String(d));
  }
  svgEl("text", { x: R, y: 155, "text-anchor": "end", class: "meter-text" }, svg, "dBFS (0 = digital full scale)");
}

function renderRun(id) {
  const token = pollToken;
  const hist = [], stepLog = [];
  let lastStepKey = "", promptSent = "", lastStatus = null;
  const glyph = h("span", { class: "glyph", "aria-hidden": "true" });
  const stateName = h("span");
  const pills = h("div", { class: "row" });
  const bar = h("span");
  const progress = h("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0", "aria-label": "Progress" }, bar);
  const stepText = h("div", { class: "muted" });
  const msg = h("div", { role: "status" });
  const statEl = h("div", { class: "state" }, h("div", { class: "big" }, glyph, stateName), pills);
  const stepper = h("ol", { class: "steps", "aria-label": "Phases" });
  const log = h("ul", { class: "steplog", "aria-label": "Step history" });
  const promptBox = h("div", { hidden: true });
  const meterBox = h("div", { class: "meterbox" });
  const histBox = h("div");
  const nums = h("div", { class: "meter-label" });
  const abort = h("button", { class: "danger", type: "button" }, "Abort and stop sound");
  const resultsLink = h("a", { class: "btn", href: "#/results/" + id, hidden: true }, "View results");
  abort.addEventListener("click", async () => {
    abort.disabled = true;
    try { await api("POST", `/api/runs/${id}/abort`, {}); abort.textContent = "Abort requested"; } catch (e) { showBanner("Abort failed: " + e.message, true); abort.disabled = false; }
  });
  main.appendChild(h("h1", null, "Run ", h("code", { text: id })));
  main.appendChild(h("section", { class: "card", "aria-live": "off" }, statEl, progress, stepText, msg, stepper,
    h("div", { class: "row", style: null }, abort, resultsLink)));
  main.appendChild(promptBox);
  main.appendChild(h("section", { class: "card" }, h("h2", { text: "Levels" }), nums, meterBox, h("h3", { text: "Mic peak over the last minute" }), histBox));
  main.appendChild(h("section", { class: "card" }, h("h2", { text: "Steps so far" }), log));

  function update(st) {
    lastStatus = st;
    const state = st.state || "idle";
    document.getElementById("main").dataset.state = state;
    statEl.className = "state " + state;
    glyph.textContent = STATE_GLYPH[state] || "○";
    stateName.textContent = STATE_NAME[state] || state;
    clear(pills);
    if (st.mode) pills.appendChild(h("span", { class: "pill" }, st.mode === "five" ? "five positions" : "one position"));
    if (st.positions_total) pills.appendChild(h("span", { class: "pill" }, `Position ${st.position || 1} of ${st.positions_total}`));
    pills.appendChild(st.simulated ? h("span", { class: "pill sim" }, "Simulated: no sound") : h("span", { class: "pill live" }, "Real: sound is playing"));
    const pct = Math.round(Math.max(0, Math.min(1, st.progress || 0)) * 100);
    bar.style.width = pct + "%";
    progress.setAttribute("aria-valuenow", String(pct));
    progress.classList.toggle("done", state === "done");
    stepText.textContent = (st.step || "") + (st.step ? "  (" + pct + "% of this step)" : "");
    msg.textContent = st.error ? "Error: " + st.error : (st.message || "");
    // phases
    clear(stepper);
    let ci = PHASES.findIndex(p => p[0] === (state === "waiting_user" ? "sweeping" : state));
    for (let i = 0; i < PHASES.length; i++) stepper.appendChild(h("li", { class: ci < 0 ? "" : (i < ci || state === "done" ? "past" : i === ci ? "cur" : "") }, PHASES[i][1]));
    // log
    const key = `${st.position}|${st.step}|${state}`;
    if (key !== lastStepKey) {
      lastStepKey = key;
      stepLog.unshift(`${new Date().toLocaleTimeString()}  ${STATE_NAME[state] || state}: ${st.step || ""}${st.positions_total > 1 ? "  [pos " + st.position + "/" + st.positions_total + "]" : ""}`);
      if (stepLog.length > 60) stepLog.pop();
      clear(log); for (const s of stepLog) log.appendChild(h("li", { text: s }));
    }
    // prompt
    const p = st.prompt;
    if (p && state !== "done") {
      if (promptBox.dataset.pid !== p.id) {
        promptBox.dataset.pid = p.id; clear(promptBox); promptBox.hidden = false;
        const btn = h("button", { type: "button", id: "continue-btn" }, "Continue");
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          try { await api("POST", `/api/runs/${id}/continue`, { prompt_id: p.id }); promptSent = p.id; btn.textContent = "Sent, waiting for the run..."; }
          catch (e) { showBanner("Continue failed: " + e.message, true); btn.disabled = false; }
        });
        promptBox.appendChild(h("div", { class: "prompt", role: "alert" }, h("p", { class: "t" }, "⏸ " + p.text), h("p", { class: "note" }, "Do it, keep the mic at ear height, then press Continue. Direction is not verified; the run checks that the arrival times moved."), btn));
      }
    } else { promptBox.hidden = true; delete promptBox.dataset.pid; clear(promptBox); }
    // meter
    const mic = st.mic || null;
    drawMeter(meterBox, mic, st.level_dbfs);
    clear(nums);
    nums.appendChild(h("span", null, "Playback ", h("span", { class: "meter-num" }, st.level_dbfs === null || st.level_dbfs === undefined ? "silent" : fx(st.level_dbfs) + " dBFS"), " (cap −20)"));
    nums.appendChild(h("span", null, "Mic peak ", h("span", { class: "meter-num" }, mic ? fx(mic.peak_dbfs) + " dBFS" : "n/a"), " RMS ", h("span", { class: "meter-num" }, mic ? fx(mic.rms_dbfs) : "n/a"), " floor ", h("span", { class: "meter-num" }, mic ? fx(mic.noise_floor_dbfs, 0) : "n/a")));
    if (mic) {
      hist.push({ t: Date.now() / 1000, peak: mic.peak_dbfs, lvl: st.level_dbfs, floor: mic.noise_floor_dbfs });
      while (hist.length > 130) hist.shift();
      drawHist();
    }
    const active = !TERMINAL.includes(state) && state !== "idle";
    abort.hidden = !active;
    if (active && !abort.disabled === false && !st.prompt) { /* keep disabled until the state changes */ }
    if (!TERMINAL.includes(state)) { abort.textContent = abort.disabled ? abort.textContent : "Abort and stop sound"; }
    resultsLink.hidden = !st.has_results;
    if (st.stale) showBanner("No status update for " + Math.round(st.age_s) + " s. The run process may have stopped.", true);
    else if (banner.textContent.startsWith("No status update")) showBanner("");
  }
  function drawHist() {
    const now = hist[hist.length - 1].t;
    const series = [
      { type: "hline", y: -20, cls: "c1", align: "start", dash: "6 3", label: "playback cap −20" },
      { type: "line", x: hist.map(p => p.t - now), y: hist.map(p => p.floor), cls: "tgt", dash: "2 4", width: 1.5 },
      { type: "line", x: hist.map(p => p.t - now), y: hist.map(p => p.lvl), cls: "c4", dash: "7 4", width: 2 },
      { type: "line", x: hist.map(p => p.t - now), y: hist.map(p => p.peak), cls: "c0", width: 2.5 }];
    makeChart(histBox, { xmin: -60, xmax: 0, ymin: -90, ymax: 0, xlabel: "Seconds ago", ylabel: "dBFS", height: 190, series, aria: "Mic peak (solid), playback level (dashed) and noise floor (dotted) over the last minute" });
    const lg = h("div"); histBox.appendChild(lg);
    makeLegend(lg, [{ label: "Mic peak", cls: "c0" }, { label: "Playback level", cls: "c4", dash: "7 4" }, { label: "Noise floor", cls: "tgt", dash: "2 4" }, { label: "Playback cap", cls: "c1", dash: "6 3" }]);
  }

  async function loop() {
    let delay = 500;
    if (token !== pollToken) return;
    try {
      const st = await api("GET", `/api/runs/${id}/status`);
      if (token !== pollToken) return;
      update(st);
      if (banner.textContent.startsWith("Lost contact")) showBanner("");
      delay = 500;
      if (TERMINAL.includes(st.state)) { refreshRuns(); return; }
    } catch (e) {
      if (token !== pollToken) return;
      if (e.status === 404 || e.status === 400) { showBanner("Run not found: " + id, true); return; }
      showBanner("Lost contact with the server. Retrying...", true);
      delay = 2000;
    }
    setTimeout(loop, delay);
  }
  window.addEventListener("resize", () => { if (token === pollToken && lastStatus) drawMeter(meterBox, lastStatus.mic, lastStatus.level_dbfs); });
  loop();
}

/* ---------- results page ---------- */
async function renderResults(id) {
  let R;
  try { R = await api("GET", `/api/runs/${id}/results`); }
  catch (e) {
    main.appendChild(h("h1", null, "Results ", h("code", { text: id })));
    main.appendChild(h("section", { class: "card" }, h("p", { text: e.status === 404 ? "No results yet for this run." : "Could not load results: " + e.message }), h("a", { class: "btn", href: "#/run/" + id }, "Open the live view")));
    return;
  }
  const chs = R.channels, F = R.freq;
  // header, verdict
  main.appendChild(h("h1", null, "Results ", h("code", { text: R.id })));
  const warnList = h("ul", { class: "warnings" }, (R.warnings || []).map(w => h("li", { text: w })));
  const vglyph = { PASS: "✔", WARN: "▲", FAIL: "✖" }[R.verdict] || "?";
  main.appendChild(h("section", { class: "card", id: "sec-verdict" },
    h("div", { class: "verdict" }, h("span", { class: "vbadge " + R.verdict }, h("span", { "aria-hidden": "true" }, vglyph), "Verdict: " + R.verdict),
      h("span", { class: "pill" }, R.mode === "five" ? "five positions" : "one position"),
      R.simulated ? h("span", { class: "pill sim" }, "Simulated data") : h("span", { class: "pill live" }, "Real measurement"),
      h("span", { class: "pill" }, `Target: ${R.target.name}, ${R.target.hump_db} dB hump`),
      h("span", { class: "muted small" }, R.created || "")),
    h("p", { text: R.summary }),
    R.mode !== "five" ? h("p", { class: "note" }, "One position only: this is a one-point fit and says nothing about the rest of the seat.") : null,
    (R.warnings || []).length ? h("h3", { text: "Warnings" }) : h("p", { class: "note" }, "No warnings."), warnList));
  const nav = h("nav", { class: "navchips", "aria-label": "Sections" });
  for (const [a, t] of [["sec-response", "Responses"], ["sec-table", "Delay, trim, polarity"], ["sec-bass", "Bass filters"], ["sec-heights", "Heights"], ["sec-ir", "Impulse response"], ["sec-arrival", "Arrival times"], ["sec-apply", "Apply"]]) nav.appendChild(h("a", { href: "#" + a, "data-jump": a }, t));
  nav.addEventListener("click", (ev) => { const a = ev.target.closest("a[data-jump]"); if (!a) return; ev.preventDefault(); const el = document.getElementById(a.dataset.jump); if (el) el.scrollIntoView({ behavior: "smooth" }); });
  main.appendChild(nav);

  buildResponse(R);
  buildTable(R);
  const g = h("div", { class: "grid2" }); main.appendChild(g);
  g.appendChild(buildBass(R));
  g.appendChild(buildHeights(R));
  buildIR(R);
  buildArrival(R);
  buildApply(R);
}

function buildResponse(R) {
  const F = R.freq, chs = R.channels;
  const sel = new Set(["FL"]);
  const opt = { pred: true, spread: true, target: true };
  const box = h("div"), legendBox = h("div");
  let lo = 1e9, hi = -1e9;
  const scan = (a) => { for (const v of a) { if (v < lo) lo = v; if (v > hi) hi = v; } };
  scan(R.target_db);
  for (const c of chs) { scan(R.response_db[c].avg); if (R.predicted_after_db && R.predicted_after_db[c]) scan(R.predicted_after_db[c]); }
  const [ymin, ymax] = niceRange(Math.max(lo, hi - 45), hi, 3);
  const posKeys = (c) => Object.keys(R.response_db[c]).filter(k => /^pos\d+$/.test(k));
  let chart;
  const readout = (x) => {
    const i = bisect(F, x);
    const parts = [`${F[i] < 1000 ? F[i].toFixed(0) : (F[i] / 1000).toFixed(2) + "k"} Hz`];
    if (opt.target) parts.push(`target ${fx(R.target_db[i])} dB`);
    for (const c of chs) if (sel.has(c)) {
      let t = `${c} ${fx(R.response_db[c].avg[i])}`;
      if (opt.pred && R.predicted_after_db[c]) t += ` → ${fx(R.predicted_after_db[c][i])}`;
      parts.push(t + " dB");
    }
    return parts.join("   |   ");
  };
  function build() {
    const series = [];
    const picked = chs.filter(c => sel.has(c));
    if (opt.target) series.push({ type: "line", x: F, y: R.target_db, cls: "tgt", dash: "2 4", width: 3 });
    picked.forEach((c, n) => {
      const col = chColor(c);
      if (opt.spread && picked.length <= 3) {
        const pk = posKeys(c);
        if (pk.length > 1) {
          const lo_ = F.map((_, i) => Math.min(...pk.map(k => R.response_db[c][k][i])));
          const hi_ = F.map((_, i) => Math.max(...pk.map(k => R.response_db[c][k][i])));
          series.push({ type: "band", x: F, lo: lo_, hi: hi_, cls: col });
        }
      }
      if (opt.pred && R.predicted_after_db && R.predicted_after_db[c]) series.push({ type: "line", x: F, y: R.predicted_after_db[c], cls: col, dash: "7 4", width: 2 });
      series.push({ type: "line", x: F, y: R.response_db[c].avg, cls: col, width: 2.5 });
      if (picked.length > 1) series.push({ type: "points", pts: F.map((f, i) => [f, R.response_db[c].avg[i]]).filter((_, i) => (i + n * 3) % 24 === 0), cls: col, shape: chShape(c), r: 3.5 });
    });
    chart = makeChart(box, { xlog: true, xmin: 20, xmax: 20000, ymin: ymin, ymax: ymax, xlabel: "Frequency (Hz)", ylabel: "Level (dB, relative)", series, readout, aria: "Measured frequency response of the selected speakers with target and predicted response" });
    const items = [];
    if (opt.target) items.push({ label: "Target (owner hump)", cls: "tgt", dash: "2 4", width: 3 });
    items.push({ label: "Measured, through the current profile", cls: "c7" });
    if (opt.pred) items.push({ label: "Predicted after the proposal", cls: "c7", dash: "7 4" });
    if (opt.spread && picked.length <= 3 && R.mode === "five") items.push({ label: "Position spread (min to max)", cls: "c7", kind: "band" });
    makeLegend(legendBox, items);
  }
  const chips = chipRow(chs.map(c => ({ key: c, label: c, swatch: swatch(chColor(c), chShape(c)) })), sel, build, { label: "Speakers shown" });
  const all = h("button", { type: "button", class: "secondary small" }, "All"), none = h("button", { type: "button", class: "secondary small" }, "None");
  const setAll = (v) => { for (const cb of chips.querySelectorAll("input")) { cb.checked = v; cb.dispatchEvent(new Event("change")); } };
  all.addEventListener("click", () => setAll(true)); none.addEventListener("click", () => setAll(false));
  const mk = (key, label) => { const cb = h("input", { type: "checkbox" }); cb.checked = opt[key]; cb.addEventListener("change", () => { opt[key] = cb.checked; build(); }); return h("label", { class: "chip" }, cb, label); };
  main.appendChild(sectionCard("sec-response", "Frequency response per speaker",
    h("p", { class: "note" }, "Measured through CamillaDSP with the current profile. Dashed line: the prediction after the proposed correction. Dotted grey: the target. Pick one speaker to see the position spread."),
    h("div", { class: "row" }, chips, all, none), h("div", { class: "chips" }, mk("target", "Target"), mk("pred", "Predicted after"), mk("spread", "Position spread")), box, legendBox));
  build();
}

function buildTable(R) {
  const tb = h("tbody");
  let changes = 0;
  const cell = (label, changed, ...kids) => h("td", { "data-label": label, class: changed ? "chg" : "num" }, h("span", { class: "val" }, ...kids));
  const pair = (now, nw, unit, d) => {
    const ch = Math.abs(nw - now) > 1e-9;
    const kids = [fx(now, d), h("span", { class: "arrow", "aria-hidden": "true" }, "→"), fx(nw, d)];
    if (ch) { changes++; kids.push(h("span", { class: "chgtag" }, (nw > now ? "▲ " : "▼ ") + sgn(nw - now, d) + " " + unit)); }
    return [ch, kids];
  };
  for (const r of R.table) {
    const [dch, dk] = pair(r.delay_now_ms, r.delay_new_ms, "ms", 2);
    const [tch, tk] = pair(r.trim_now_db, r.trim_new_db, "dB", 1);
    const p = r.polarity;
    let ptxt, pcls;
    if (p.flip_proposed) { ptxt = "FLIP proposed"; pcls = "pol-flip"; changes++; }
    else if (p.ok && p.margin_db < 3) { ptxt = "no change (inconclusive)"; pcls = "pol-weak"; }
    else if (p.ok) { ptxt = "OK"; pcls = "pol-ok"; }
    else { ptxt = "check"; pcls = "pol-weak"; }
    tb.appendChild(h("tr", null,
      h("td", { class: "name", "data-label": "Speaker" }, r.channel),
      h("td", { class: "num", "data-label": "Arrival (ms)" }, fx(r.arrival_ms, 2)),
      cell("Delay now → new (ms)", dch, ...dk), cell("Trim now → new (dB)", tch, ...tk),
      h("td", { class: "num", "data-label": "Level (dB rel.)" }, fx(r.level_db)),
      h("td", { class: p.flip_proposed ? "chg" : "", "data-label": "Polarity" }, h("span", { class: "val" }, h("span", { class: pcls }, ptxt), h("span", { class: "note" }, " margin " + fx(p.margin_db) + " dB"))),
      h("td", { class: "num", "data-label": "SNR (dB)" }, fx(r.snr_db)),
      h("td", { class: "note" + ((r.notes || []).length ? "" : " empty"), "data-label": "Notes" }, h("span", { class: "val" }, (r.notes || []).join("; ")))));
  }
  const th = (t, c) => h("th", { class: c || "" }, t);
  main.appendChild(sectionCard("sec-table", "Delay, trim and polarity: current → proposed",
    h("p", { class: "note" }, changes ? `${changes} change${changes > 1 ? "s" : ""} proposed. Changed cells are shaded, carry a ▲/▼ tag with the amount, and the bar on their left edge. Changes under 0.15 ms or 1 dB are never proposed.` : "No changes proposed: every value stays as it is."),
    h("div", { class: "tablewrap" }, h("table", { class: "data cards" },
      h("thead", null, h("tr", null, th("Speaker"), th("Arrival (ms)", "num"), th("Delay now → new (ms)"), th("Trim now → new (dB)"), th("Level (dB rel.)", "num"), th("Polarity"), th("SNR (dB)", "num"), th("Notes"))), tb))));
}

function filterTable(list, title) {
  if (!list || !list.length) return h("p", { class: "note" }, title + ": none.");
  return h("div", { class: "tablewrap" }, h("table", { class: "data filterlist", "aria-label": title }, h("caption", { text: title }),
    h("thead", null, h("tr", null, h("th", null, "Name"), h("th", { class: "num" }, "Freq (Hz)"), h("th", { class: "num" }, "Gain (dB)"), h("th", { class: "num" }, "Q"))),
    h("tbody", null, list.map(f => h("tr", null, h("td", { text: f.name }), h("td", { class: "num", text: fx(f.freq, f.freq < 100 ? 1 : 0) }), h("td", { class: "num", text: sgn(f.gain) }), h("td", { class: "num", text: fx(f.q, 1) }))))));
}

function buildBass(R) {
  const b = R.bass, box = h("div"), lg = h("div");
  const net = b.current_db.map((v, i) => v + b.proposed_extra_db[i]);
  const tgt = b.freq.map(f => interp(f, R.freq, R.target_db));
  let lo = Math.min(...b.current_db, ...net, ...b.proposed_extra_db, ...tgt), hi = Math.max(...b.current_db, ...net, ...tgt);
  const [ymin, ymax] = niceRange(lo, hi, 2);
  const fmax = b.freq[b.freq.length - 1];
  const series = [{ type: "hline", y: 0, cls: "tgt", dash: "1 0", label: null },
    { type: "line", x: b.freq, y: tgt, cls: "tgt", dash: "2 4", width: 3 },
    { type: "line", x: b.freq, y: b.current_db, cls: "c0", width: 2 },
    { type: "line", x: b.freq, y: b.proposed_extra_db, cls: "c1", dash: "7 4", width: 2.5 },
    { type: "line", x: b.freq, y: net, cls: "c2", width: 3 }];
  for (const f of b.filters_extra_proposed || []) series.push({ type: "vline", x: f.freq, cls: "c1", dash: "1 3", label: null });
  const readout = (x) => { const i = bisect(b.freq, x); return `${b.freq[i].toFixed(0)} Hz   |   current ${fx(b.current_db[i])}   proposed extra ${sgn(b.proposed_extra_db[i])}   net ${fx(net[i])}   target ${fx(tgt[i])} dB`; };
  makeChart(box, { xlog: true, xmin: b.freq[0], xmax: fmax, ymin, ymax, xlabel: "Frequency (Hz)", ylabel: "Level / filter gain (dB)", series, readout, aria: "Bass response: current, proposed extra filters, and net result against the target" });
  makeLegend(lg, [{ label: "Current", cls: "c0" }, { label: "Proposed extra filters", cls: "c1", dash: "7 4", width: 2.5 }, { label: "Net (current + extra)", cls: "c2", width: 3 }, { label: "Target", cls: "tgt", dash: "2 4", width: 3 }]);
  return sectionCard("sec-bass", "Bass filters",
    h("p", { class: "note" }, "Common to all Z906 channels. Cut-only extra filters are added to the current set. Dotted vertical lines mark the proposed filter centres."),
    box, lg,
    h("dl", { class: "kv" }, h("dt", { text: "RMS to target, 30-250 Hz" }), h("dd", { text: `${fx(b.rms_to_target_before_db)} dB → ${fx(b.rms_to_target_after_db)} dB` })),
    filterTable(b.filters_extra_proposed, "Proposed extra filters"), filterTable(b.filters_current, "Current filters (kept)"));
}

function buildHeights(R) {
  const H = R.heights, F = R.freq, box = h("div"), lg = h("div");
  const BED = ["FL", "FR", "FC", "SL", "SR"].filter(c => R.response_db[c]), HT = ["RL", "RR"].filter(c => R.response_db[c]);
  let content;
  if (!BED.length || HT.length < 1) { content = h("p", { class: "note", text: "Heights or bed channels missing from this run." }); }
  else {
    const bed = pmean(BED.map(c => R.response_db[c].avg));
    const before = pmean(HT.map(c => R.response_db[c].avg));
    const after = pmean(HT.map(c => R.predicted_after_db[c]));
    const sub = (a) => a;
    const lo = Math.min(...bed.slice(0), ...before, ...after), hi = Math.max(...bed, ...before, ...after);
    const k0 = F.findIndex(f => f >= 100);
    const vals = [].concat(bed.slice(k0), before.slice(k0), after.slice(k0));
    const [ymin, ymax] = niceRange(Math.min(...vals), Math.max(...vals), 2);
    const readout = (x) => { const i = bisect(F, x); return `${F[i] < 1000 ? F[i].toFixed(0) : (F[i] / 1000).toFixed(2) + "k"} Hz   |   bed ${fx(bed[i])}   heights now ${fx(before[i])}   heights after ${fx(after[i])} dB`; };
    makeChart(box, { xlog: true, xmin: 100, xmax: 20000, ymin, ymax, xlabel: "Frequency (Hz)", ylabel: "Level (dB, power mean)", series: [
      { type: "vspan", x0: 170, x1: 5000, label: "fit range 170 Hz-5 kHz" },
      { type: "line", x: F, y: bed, cls: "c0", width: 3 },
      { type: "line", x: F, y: before, cls: "c1", width: 2 },
      { type: "line", x: F, y: after, cls: "c1", dash: "7 4", width: 2.5 }], readout, aria: "Ceiling heights against the bed speakers, before and after the proposed tone filters" });
    makeLegend(lg, [{ label: "Bed average (FL FR FC SL SR)", cls: "c0", width: 3 }, { label: "Heights now (RL RR)", cls: "c1" }, { label: "Heights after proposal", cls: "c1", dash: "7 4", width: 2.5 }]);
  }
  return sectionCard("sec-heights", "Heights against the bed",
    h("p", { class: "note" }, "Ceiling pair (RL, RR) compared with the bed speakers; the tone filters are fitted from 170 Hz to 5 kHz only."),
    box, lg,
    h("dl", { class: "kv" }, h("dt", { text: "RMS to bed" }), h("dd", { text: `${fx(H.rms_to_bed_before_db)} dB → ${fx(H.rms_to_bed_after_db)} dB` }),
      h("dt", { text: "Heights trim" }), h("dd", { text: sgn(H.trim_new_db) + " dB" }),
      h("dt", { text: "Above 5 kHz" }), h("dd", { text: H.above_5k_matched ? "matched to the bed" : "not matched (left as is)" })),
    filterTable(H.tone_filters_proposed, "Proposed heights tone filters"));
}

function buildIR(R) {
  const sel = new Set(["FL", "FR"].filter(c => R.ir[c]));
  if (!sel.size) sel.add(R.channels.find(c => R.ir[c]));
  const win = h("select", { "aria-label": "Time window" }, h("option", { value: "full" }, "Full window"), h("option", { value: "peak" }, "Zoom on the main peak"));
  const box = h("div"), lg = h("div");
  const arr = {}; for (const r of R.table) arr[r.channel] = r.arrival_ms;
  function build() {
    const picked = R.channels.filter(c => sel.has(c) && R.ir[c]);
    const series = [], items = [];
    let tmin = Infinity, tmax = -Infinity, amin = Infinity;
    picked.forEach(c => {
      const ir = R.ir[c];
      tmin = Math.min(tmin, ir.t_ms[0]); tmax = Math.max(tmax, ir.t_ms[ir.t_ms.length - 1]);
      if (arr[c] !== undefined) amin = Math.min(amin, arr[c]);
      series.push({ type: "line", x: ir.t_ms, y: ir.h, cls: chColor(c), width: 1.8 });
      if (arr[c] !== undefined) series.push({ type: "vline", x: arr[c], cls: chColor(c), dash: "2 3", label: c, row: picked.indexOf(c) });
      items.push({ label: c + " (dotted line: arrival)", cls: chColor(c) });
    });
    if (!picked.length) { box.textContent = "Pick at least one speaker."; clear(lg); return; }
    let x0 = tmin, x1 = tmax;
    if (win.value === "peak" && isFinite(amin)) { x0 = Math.max(tmin, amin - 2); x1 = Math.min(tmax, amin + 12); }
    const readout = (x) => `${x.toFixed(2)} ms   |   ` + picked.map(c => { const ir = R.ir[c], i = bisect(ir.t_ms, x); return `${c} ${fx(ir.h[i], 3)}`; }).join("   ");
    makeChart(box, { xmin: x0, xmax: x1, ymin: -1.1, ymax: 1.1, xlabel: "Time (ms)", ylabel: "Amplitude (normalised)", series, readout, aria: "Impulse response of the selected speakers" });
    makeLegend(lg, items);
  }
  const chips = chipRow(R.channels.filter(c => R.ir[c]).map(c => ({ key: c, label: c, swatch: swatch(chColor(c)) })), sel, build, { label: "Speakers shown" });
  win.addEventListener("change", build);
  main.appendChild(sectionCard("sec-ir", "Impulse response", h("p", { class: "note" }, "Normalised to each speaker's own peak. Position 1 or the average, as the run provides."), h("div", { class: "row" }, chips, h("label", null, "Window ", win)), box, lg));
  build();
}

function buildArrival(R) {
  const pos = R.positions, sc = R.arrival_scatter;
  const incl = h("input", { type: "checkbox" });
  const box = h("div"), lg = h("div"), summary = h("div");
  function build() {
    const chs = R.channels.filter(c => sc[c] && (incl.checked || c !== "LFE"));
    const series = [], items = [];
    let lo = Infinity, hi = -Infinity;
    chs.forEach((c, n) => {
      const jit = (n - (chs.length - 1) / 2) * 0.07;
      const pts = sc[c].map((v, i) => [pos[i].index + jit, v]);
      for (const p of pts) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
      series.push({ type: "points", pts, cls: chColor(c), shape: chShape(c), r: 4.5 });
      items.push({ label: c, cls: chColor(c), kind: "marker", shape: chShape(c) });
    });
    const [ymin, ymax] = niceRange(lo, hi, (hi - lo) * 0.08 + 0.05);
    const np = pos.length;
    makeChart(box, { xmin: 0.5, xmax: np + 0.5, ymin, ymax, xticks: pos.map(p => p.index), xfmt: (t) => String(t), xlabel: "Mic position (numbers in the list below)", ylabel: "Arrival time (ms)", series,
      readout: (x) => { const k = Math.min(np, Math.max(1, Math.round(x))); return `Position ${k} (${pos[k - 1].label}): ` + chs.map(c => `${c} ${fx(sc[c][k - 1], 2)}`).join("  "); }, aria: "Arrival time of each speaker at each mic position" });
    makeLegend(lg, items);
    clear(summary);
    summary.appendChild(h("p", { class: "note" }, "Positions: " + pos.map(p => `${p.index} ${p.label}`).join(", ") + "."));
    if (np > 1) {
      summary.appendChild(h("p", { class: "note" }, "Spread across positions (max minus min): " + chs.map(c => `${c} ${fx(Math.max(...sc[c]) - Math.min(...sc[c]), 2)} ms`).join(", ") + ". A mic move of 20 cm should shift the nearest speakers by roughly 0.3-0.6 ms."));
    }
  }
  incl.addEventListener("change", build);
  main.appendChild(sectionCard("sec-arrival", "Arrival time by mic position", h("div", { class: "chips" }, h("label", { class: "chip" }, incl, "Include LFE (the Z906 sub lags about 6.5 ms)")), box, lg, summary));
  build();
}

function buildApply(R) {
  const c = R.commands || {};
  const out = h("pre", { class: "out", hidden: true, "aria-live": "polite" });
  const trial = h("p", { class: "note", role: "status" });
  const confirm = h("input", { type: "text", id: "confirm", autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-describedby": "confirm-help", placeholder: "APPLY" });
  const fail = R.verdict === "FAIL" ? h("input", { type: "checkbox", id: "failack" }) : null;
  const applyBtn = h("button", { type: "button", class: "danger", disabled: true }, "Apply with 60 s trial");
  const keepBtn = h("button", { type: "button" }, "Keep");
  const rbBtn = h("button", { type: "button", class: "secondary" }, "Roll back");
  const gate = () => { applyBtn.disabled = !(confirm.value === "APPLY" && (!fail || fail.checked)); };
  confirm.addEventListener("input", gate); if (fail) fail.addEventListener("change", gate);
  let timer = null;
  function show(j) {
    out.hidden = false;
    out.textContent = (j.argv_display ? "$ " + j.argv_display + "\n" : "") + (j.running ? "(still running...)\n" : "") + (j.output || "") + (j.returncode !== null && j.returncode !== undefined ? "\n[exit code " + j.returncode + "]" : "");
  }
  async function act(path, body, btn) {
    btn.disabled = true; out.hidden = false; out.textContent = "Working...";
    try {
      const j = await api("POST", path, body);
      show(j);
      if (j.running) { const t = setInterval(async () => { const a = await api("GET", "/api/action"); show(a); if (!a.running) clearInterval(t); }, 1000); }
      return j;
    } catch (e) { out.textContent = "Failed: " + e.message; return null; }
    finally { if (btn !== applyBtn) btn.disabled = false; else gate(); }
  }
  applyBtn.addEventListener("click", async () => {
    const j = await act(`/api/runs/${R.id}/apply`, { confirm: confirm.value }, applyBtn);
    if (j && j.returncode === 0) {
      confirm.value = ""; gate();
      let left = 60; clearInterval(timer);
      timer = setInterval(() => { left--; trial.textContent = left > 0 ? `Trial running: the old setup returns in about ${left} s unless you press Keep.` : "Trial time is over: the setup should have reverted unless you pressed Keep in time."; if (left <= 0) clearInterval(timer); }, 1000);
      trial.textContent = "Trial running: the old setup returns in about 60 s unless you press Keep.";
    }
  });
  keepBtn.addEventListener("click", async () => { const j = await act("/api/keep", {}, keepBtn); if (j && j.returncode === 0) { clearInterval(timer); trial.textContent = "Kept."; } });
  rbBtn.addEventListener("click", async () => { const j = await act("/api/rollback", {}, rbBtn); if (j && j.returncode === 0) { clearInterval(timer); trial.textContent = "Rolled back."; } });
  const cmd = (label, text) => h("div", { class: "cmdrow" }, h("span", { class: "lbl", text: label }), h("code", { class: "cmd", text: text }), copyBtn(text));
  main.appendChild(sectionCard("sec-apply", "Apply, keep or roll back",
    h("p", null, "Nothing has been applied. The same commands work in a terminal:"),
    cmd("Apply", c.apply || ""), cmd("Keep", c.keep || ""), cmd("Roll back", c.rollback || ""),
    h("div", { class: "apply" },
      h("p", { id: "confirm-help", class: "note" }, "Apply backs up the live config, installs the proposal and starts a 60 s trial. It reverts by itself unless you press Keep."),
      fail ? h("label", null, fail, " The verdict is FAIL and I want to apply anyway") : null,
      h("label", { for: "confirm" }, "Type APPLY to enable the button"), h("div", { class: "row" }, confirm, applyBtn)),
    h("div", { class: "row", style: null }, keepBtn, rbBtn), trial, out));
  if (R.verify) main.appendChild(sectionCard("sec-verify", "Verification", h("pre", { class: "out", text: JSON.stringify(R.verify, null, 2) })));
}

/* ---------- start ---------- */
refreshRuns();
setInterval(() => { if (!document.hidden) refreshRuns(); }, 6000);
route();
