/* Hand-written SVG charts for the autocal GUI. No libraries. Colours come from CSS classes (c0..c7, tgt, ...). */
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
let _uid = 0;

function svgEl(tag, attrs, parent, text) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

function niceStep(span, target) {
  const raw = span / target, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  return (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
}
function niceTicks(lo, hi, target) {
  const step = niceStep(hi - lo, target || 6);
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toPrecision(10));
  return out;
}
function niceRange(lo, hi, pad) {
  if (!(hi > lo)) { hi = lo + 1; }
  const step = niceStep(hi - lo, 6);
  return [Math.floor((lo - (pad || 0)) / step) * step, Math.ceil((hi + (pad || 0)) / step) * step];
}
function fmtHz(f) {
  return f >= 1000 ? (f / 1000 % 1 === 0 ? (f / 1000) + "k" : (f / 1000).toFixed(1) + "k") : String(f);
}
const LOG_CANDIDATES = [10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100, 125, 150, 200, 250, 300, 400, 500, 700, 1000, 1500, 2000, 3000, 4000, 5000, 7000, 10000, 15000, 20000];
function logTicks(lo, hi, widthPx) {
  const minGap = 34;
  let out = [], lastX = -1e9;
  const span = Math.log(hi / lo);
  for (const c of LOG_CANDIDATES) {
    if (c < lo * 0.999 || c > hi * 1.001) continue;
    const x = Math.log(c / lo) / span * widthPx;
    if (x - lastX >= minGap) { out.push(c); lastX = x; }
  }
  return out;
}
function bisect(arr, v) {   // nearest index in ascending array
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m] <= v) lo = m; else hi = m; }
  return Math.abs(arr[lo] - v) <= Math.abs(arr[hi] - v) ? lo : hi;
}

/* Marker shapes so series differ by more than colour. Returns an SVG element at (x,y). */
const SHAPES = ["circle", "square", "triangle", "diamond", "cross", "plus", "star", "hex"];
function marker(parent, shape, x, y, r, cls) {
  const a = { class: cls };
  switch (shape) {
    case "square": return svgEl("rect", Object.assign(a, { x: x - r, y: y - r, width: 2 * r, height: 2 * r }), parent);
    case "triangle": return svgEl("path", Object.assign(a, { d: `M${x},${y - r * 1.2}L${x + r * 1.1},${y + r * 0.9}L${x - r * 1.1},${y + r * 0.9}Z` }), parent);
    case "diamond": return svgEl("path", Object.assign(a, { d: `M${x},${y - r * 1.3}L${x + r * 1.3},${y}L${x},${y + r * 1.3}L${x - r * 1.3},${y}Z` }), parent);
    case "cross": return svgEl("path", Object.assign(a, { d: `M${x - r},${y - r}L${x + r},${y + r}M${x + r},${y - r}L${x - r},${y + r}`, class: cls.replace("mk ", "mkl ") }), parent);
    case "plus": return svgEl("path", Object.assign(a, { d: `M${x - r * 1.2},${y}L${x + r * 1.2},${y}M${x},${y - r * 1.2}L${x},${y + r * 1.2}`, class: cls.replace("mk ", "mkl ") }), parent);
    case "star": return svgEl("path", Object.assign(a, { d: `M${x},${y - r * 1.4}L${x + r * 0.4},${y - r * 0.4}L${x + r * 1.4},${y}L${x + r * 0.4},${y + r * 0.4}L${x},${y + r * 1.4}L${x - r * 0.4},${y + r * 0.4}L${x - r * 1.4},${y}L${x - r * 0.4},${y - r * 0.4}Z` }), parent);
    case "hex": return svgEl("path", Object.assign(a, { d: `M${x - r},${y}L${x - r / 2},${y - r * 0.9}L${x + r / 2},${y - r * 0.9}L${x + r},${y}L${x + r / 2},${y + r * 0.9}L${x - r / 2},${y + r * 0.9}Z` }), parent);
    default: return svgEl("circle", Object.assign(a, { cx: x, cy: y, r: r }), parent);
  }
}

/* spec: {xlog, xmin, xmax, ymin, ymax, xlabel, ylabel, height, xticks?, yticks?, xfmt?, yfmt?, series:[...], readout?:fn(x)->string, aria}
   series items:
     {type:"line", x:[], y:[], cls:"c0", dash:"6 3", width:2, label:"..."}
     {type:"band", x:[], lo:[], hi:[], cls:"c0"}
     {type:"points", pts:[[x,y],...], cls:"c0", shape:"circle", r:4, label}
     {type:"vline", x:v, cls:"tgt", dash, label:"text", labelTop:true}
     {type:"hline", y:v, cls:"tgt", dash, label}
     {type:"vspan", x0, x1, cls:"span", label}
   Returns {redraw()}. Re-renders on container resize. */
function makeChart(container, spec) {
  container.textContent = "";
  const wrap = document.createElement("div");
  wrap.className = "chart";
  container.appendChild(wrap);
  const readout = document.createElement("div");
  readout.className = "readout";
  readout.setAttribute("aria-live", "off");
  readout.textContent = spec.readout ? "Hover or touch the chart for values." : "";
  container.appendChild(readout);
  let lastW = 0;

  function draw() {
    const W = Math.max(260, Math.floor(wrap.clientWidth || container.clientWidth || 600));
    lastW = W;
    const H = spec.height || (W < 520 ? 260 : 330);
    const m = { l: 52, r: 12, t: 14, b: 44 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    wrap.textContent = "";
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": spec.aria || spec.ylabel + " versus " + spec.xlabel }, wrap);
    if (spec.aria) svgEl("title", null, svg, spec.aria);
    const cid = "clip" + (++_uid);
    const defs = svgEl("defs", null, svg);
    svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph }, svgEl("clipPath", { id: cid }, defs));
    const xs = spec.xlog ? (v) => m.l + Math.log(v / spec.xmin) / Math.log(spec.xmax / spec.xmin) * pw : (v) => m.l + (v - spec.xmin) / (spec.xmax - spec.xmin) * pw;
    const xi = spec.xlog ? (px) => spec.xmin * Math.exp((px - m.l) / pw * Math.log(spec.xmax / spec.xmin)) : (px) => spec.xmin + (px - m.l) / pw * (spec.xmax - spec.xmin);
    const ys = (v) => m.t + (1 - (v - spec.ymin) / (spec.ymax - spec.ymin)) * ph;
    svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph, class: "plot-bg" }, svg);
    // grid + ticks
    const xt = spec.xticks || (spec.xlog ? logTicks(spec.xmin, spec.xmax, pw) : niceTicks(spec.xmin, spec.xmax, Math.max(3, Math.floor(pw / 80))));
    const yt = spec.yticks || niceTicks(spec.ymin, spec.ymax, Math.max(3, Math.floor(ph / 40)));
    const gx = svgEl("g", { class: "grid" }, svg), ax = svgEl("g", { class: "axis" }, svg);
    for (const t of xt) {
      const x = xs(t);
      svgEl("line", { x1: x, x2: x, y1: m.t, y2: m.t + ph }, gx);
      svgEl("line", { x1: x, x2: x, y1: m.t + ph, y2: m.t + ph + 4 }, ax);
      svgEl("text", { x: x, y: m.t + ph + 17, "text-anchor": "middle" }, ax, spec.xfmt ? spec.xfmt(t) : (spec.xlog ? fmtHz(t) : String(t)));
    }
    for (const t of yt) {
      const y = ys(t);
      svgEl("line", { x1: m.l, x2: m.l + pw, y1: y, y2: y, class: t === 0 ? "zero" : "" }, gx);
      svgEl("line", { x1: m.l - 4, x2: m.l, y1: y, y2: y }, ax);
      svgEl("text", { x: m.l - 7, y: y + 4, "text-anchor": "end" }, ax, spec.yfmt ? spec.yfmt(t) : String(t));
    }
    svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph, class: "plot-frame" }, svg);
    svgEl("text", { x: m.l + pw / 2, y: H - 6, "text-anchor": "middle", class: "axlabel" }, svg, spec.xlabel);
    svgEl("text", { transform: `translate(13,${m.t + ph / 2}) rotate(-90)`, "text-anchor": "middle", class: "axlabel" }, svg, spec.ylabel);
    const g = svgEl("g", { "clip-path": `url(#${cid})` }, svg);
    const labels = svgEl("g", { class: "annot" }, svg);
    const order = { vspan: 0, band: 1, hline: 2, vline: 2, line: 3, points: 4 };
    const items = spec.series.slice().sort((a, b) => order[a.type] - order[b.type]);
    for (const s of items) {
      if (s.hidden) continue;
      if (s.type === "line") {
        let d = "";
        for (let i = 0; i < s.x.length; i++) {
          if (s.y[i] === null || s.y[i] === undefined || isNaN(s.y[i])) continue;
          d += (d ? "L" : "M") + xs(s.x[i]).toFixed(1) + "," + ys(s.y[i]).toFixed(1);
        }
        const a = { d: d, class: "ln " + (s.cls || "c0"), "stroke-width": s.width || 2 };
        if (s.dash) a["stroke-dasharray"] = s.dash;
        svgEl("path", a, g);
      } else if (s.type === "band") {
        let d = "";
        for (let i = 0; i < s.x.length; i++) d += (i ? "L" : "M") + xs(s.x[i]).toFixed(1) + "," + ys(s.hi[i]).toFixed(1);
        for (let i = s.x.length - 1; i >= 0; i--) d += "L" + xs(s.x[i]).toFixed(1) + "," + ys(s.lo[i]).toFixed(1);
        svgEl("path", { d: d + "Z", class: "band " + (s.cls || "c0") }, g);
      } else if (s.type === "points") {
        for (const p of s.pts) marker(g, s.shape || "circle", xs(p[0]), ys(p[1]), s.r || 4, "mk " + (s.cls || "c0"));
      } else if (s.type === "vline") {
        const x = xs(s.x);
        const a = { x1: x, x2: x, y1: m.t, y2: m.t + ph, class: "ref " + (s.cls || "tgt") };
        if (s.dash) a["stroke-dasharray"] = s.dash;
        svgEl("line", a, g);
        if (s.label) svgEl("text", { x: x + 3, y: m.t + 11 + (s.row || 0) * 11, class: "ann " + (s.cls || "tgt") }, labels, s.label);
      } else if (s.type === "hline") {
        const y = ys(s.y);
        const a = { x1: m.l, x2: m.l + pw, y1: y, y2: y, class: "ref " + (s.cls || "tgt") };
        if (s.dash) a["stroke-dasharray"] = s.dash;
        svgEl("line", a, g);
        if (s.label) svgEl("text", { x: s.align === "start" ? m.l + 4 : m.l + pw - 4, y: y - 4, "text-anchor": s.align === "start" ? "start" : "end", class: "ann " + (s.cls || "tgt") }, labels, s.label);
      } else if (s.type === "vspan") {
        svgEl("rect", { x: xs(s.x0), y: m.t, width: Math.max(0, xs(s.x1) - xs(s.x0)), height: ph, class: "span " + (s.cls || "") }, g);
        if (s.label) svgEl("text", { x: xs(s.x0) + 4, y: m.t + ph - 6, class: "ann span-label" }, labels, s.label);
      }
    }
    // hover readout
    if (spec.readout) {
      const cross = svgEl("line", { y1: m.t, y2: m.t + ph, class: "cross", visibility: "hidden" }, svg);
      const hit = svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent", class: "hit" }, svg);
      const move = (ev) => {
        const r = svg.getBoundingClientRect();
        const px = Math.min(m.l + pw, Math.max(m.l, (ev.clientX - r.left) * (W / r.width)));
        const xv = xi(px);
        cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
        readout.textContent = spec.readout(xv);
      };
      hit.addEventListener("pointermove", move);
      hit.addEventListener("pointerdown", move);
      hit.addEventListener("pointerleave", () => cross.setAttribute("visibility", "hidden"));
    }
  }
  draw();
  let timer = null;
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      const w = Math.floor(wrap.clientWidth);
      if (w && Math.abs(w - lastW) > 2) { clearTimeout(timer); timer = setTimeout(draw, 60); }
    }).observe(container);
  }
  return { redraw: draw };
}

/* Legend: items [{label, cls, dash, shape, kind:"line"|"band"|"marker"}] */
function makeLegend(container, items) {
  container.textContent = "";
  const ul = document.createElement("ul");
  ul.className = "legend";
  for (const it of items) {
    const li = document.createElement("li");
    const sw = svgEl("svg", { width: 34, height: 14, viewBox: "0 0 34 14", "aria-hidden": "true" }, li);
    if (it.kind === "band") svgEl("rect", { x: 2, y: 2, width: 30, height: 10, class: "band " + it.cls }, sw);
    else if (it.kind === "marker") marker(sw, it.shape || "circle", 17, 7, 4.5, "mk " + it.cls);
    else {
      const a = { x1: 1, x2: 33, y1: 7, y2: 7, class: "ln " + it.cls, "stroke-width": it.width || 2.5 };
      if (it.dash) a["stroke-dasharray"] = it.dash;
      svgEl("line", a, sw);
    }
    li.appendChild(document.createTextNode(" " + it.label));
    ul.appendChild(li);
  }
  container.appendChild(ul);
}
