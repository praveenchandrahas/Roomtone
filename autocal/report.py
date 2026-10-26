"""report.html: one static, self-contained page per run (inline CSS and SVG, no scripts, no external requests)."""
import html
import os

import numpy as np

W, H = 760, 300
COLS = ["#1b6ec2", "#d9480f", "#2b8a3e", "#862e9c", "#c2255c", "#0b7285", "#e67700", "#495057"]


def _fmt(v, nd=2, suffix=""):
    return "n/a" if v is None else f"{v:.{nd}f}{suffix}"


def svg_chart(series, xlim, ylim, title, xlog=True, xticks=None, width=W, height=H):
    """series: list of (label, xs, ys, colour, dashed). Returns an <svg> string with axes in Hz and dB."""
    ml, mr, mt, mb = 52, 12, 24, 38
    pw, ph = width - ml - mr, height - mt - mb

    def X(x):
        return ml + (np.log(x / xlim[0]) / np.log(xlim[1] / xlim[0]) if xlog else (x - xlim[0]) / (xlim[1] - xlim[0])) * pw

    def Y(y):
        return mt + (1 - (min(max(y, ylim[0]), ylim[1]) - ylim[0]) / (ylim[1] - ylim[0])) * ph
    o = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}" class="chart">',
         f'<text x="{ml}" y="15" class="ct">{html.escape(title)}</text>']
    yt = np.arange(np.ceil(ylim[0] / 10) * 10, ylim[1] + 1, 10)
    for v in yt:
        o.append(f'<line x1="{ml}" x2="{width - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/><text x="{ml - 6}" y="{Y(v) + 4:.1f}" class="ax" text-anchor="end">{v:.0f}</text>')
    for v in (xticks or [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]):
        if xlim[0] <= v <= xlim[1]:
            lab = f"{v / 1000:g}k" if v >= 1000 else f"{v:g}"
            o.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{mt}" y2="{mt + ph}" class="grid"/><text x="{X(v):.1f}" y="{height - 20}" class="ax" text-anchor="middle">{lab}</text>')
    o.append(f'<text x="{ml + pw / 2}" y="{height - 4}" class="ax" text-anchor="middle">Hz</text><text x="12" y="{mt + ph / 2}" class="ax" transform="rotate(-90 12 {mt + ph / 2})" text-anchor="middle">dB re bed mids</text>')
    for i, (lab, xs, ys, col, dash) in enumerate(series):
        pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys) if xlim[0] <= x <= xlim[1])
        o.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.6"{" stroke-dasharray=\"5 3\"" if dash else ""} points="{pts}"><title>{html.escape(lab)}</title></polyline>')
    lx = ml + 6
    for lab, xs, ys, col, dash in series[:10]:
        o.append(f'<rect x="{lx}" y="{mt + 2}" width="10" height="3" fill="{col}"/><text x="{lx + 13}" y="{mt + 8}" class="ax">{html.escape(lab)}</text>')
        lx += 22 + 6.2 * len(lab)
    o.append("</svg>")
    return "".join(o)


def write_report(run_dir, res):
    f = np.array(res["freq"])
    ch = res["channels"]
    tgt = res["target_db"]
    bass_series = [("target", f, tgt, "#212529", True)]
    sel = (f >= 25) & (f <= 300)
    b = res["bass"]
    bf = np.array(b["freq"]); cur = np.array(b["current_db"]); ext = np.array(b["proposed_extra_db"])
    bchart = svg_chart([("target", bf, np.interp(bf, f, tgt), "#212529", True), ("now", bf, cur, COLS[0], False), ("after (predicted)", bf, cur + ext, COLS[2], False),
                        ("proposed extra", bf, ext, COLS[1], True)], (25, 300), (-30, 40), "Bass: average of the Z906 channels", xticks=[30, 40, 50, 63, 80, 100, 125, 160, 200, 250])
    allseries = [(n, f, res["response_db"][n]["avg"], COLS[i % 8], False) for i, n in enumerate(ch) if n in res["response_db"] and n != "LFE"]
    fchart = svg_chart(allseries + [("target", f, tgt, "#212529", True)], (30, 20000), (-30, 40), "All speakers through the current profile (measured)")
    pchart = svg_chart([(n, f, res["predicted_after_db"][n], COLS[i % 8], False) for i, n in enumerate(ch) if n in res["predicted_after_db"] and n != "LFE"] +
                       [("target", f, tgt, "#212529", True)], (30, 20000), (-30, 40), "All speakers after the proposal (predicted)")
    rows = ""
    for r in res["table"]:
        p = r["polarity"]
        pol = "not assessed" if p["margin_db"] is None else (f"FLIP? ({p['margin_db']:+.1f} dB)" if p["flip_proposed"] else f"ok ({p['margin_db']:+.1f} dB)")
        dch = abs(r["delay_new_ms"] - r["delay_now_ms"]) > 1e-9; tch = abs(r["trim_new_db"] - r["trim_now_db"]) > 1e-9
        rows += (f"<tr><th>{r['channel']}</th><td>{_fmt(r['arrival_ms'], 2)}</td><td>{r['delay_now_ms']:.2f}</td><td class='{'chg' if dch else ''}'>{r['delay_new_ms']:.2f}</td>"
                 f"<td>{_fmt(r['level_db'], 1)}</td><td>{r['trim_now_db']:+.1f}</td><td class='{'chg' if tch else ''}'>{r['trim_new_db']:+.1f}</td><td class='{'bad' if p['flip_proposed'] else ''}'>{pol}</td>"
                 f"<td>{_fmt(r['snr_db'], 0)}</td><td class='n'>{html.escape('; '.join(r['notes']))}</td></tr>")
    bf_rows = "".join(f"<li>{x['freq']:.1f} Hz, {x['gain']:+.2f} dB, Q {x['q']:.2f}</li>" for x in b["filters_extra_proposed"]) or "<li>none</li>"
    hf_rows = "".join(f"<li>{x['freq']:g} Hz, {x['gain']:+.2f} dB change, Q {x['q']:g}</li>" for x in res["heights"]["tone_filters_proposed"]) or "<li>no filter change</li>"
    warns = "".join(f"<li>{html.escape(w)}</li>" for w in res["warnings"]) or "<li>none</li>"
    ver = ""
    if res.get("verify"):
        v = res["verify"]
        ver = "<h2>Verification</h2><table><tr><th>check</th><th>value</th><th>limit</th><th>result</th></tr>" + "".join(
            f"<tr><td>{html.escape(c['name'])}</td><td>{c['value']}</td><td>{c['limit']}</td><td class='{c['status'].lower()}'>{c['status']}</td></tr>" for c in v["checks"]) + f"</table><p>Overall: <b>{v['overall']}</b></p>"
    sim = '<p class="sim">SIMULATED RUN: no audio was played; the numbers describe a computer model of a room.</p>' if res["simulated"] else ""
    one = "" if res["mode"] == "five" else "<p>One microphone position: a one-point tuning. Five-position mode averages over a few cm around the seat and checks the fit against held-out positions.</p>"
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>autocal report {res['id']}</title><style>
:root{{--bg:#fff;--fg:#1d2125;--mut:#5f6b76;--line:#d0d7de;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--chg:#0550ae;--grid:#e6eaee}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14171a;--fg:#e6e9ec;--mut:#9aa6b2;--line:#343b42;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--chg:#79c0ff;--grid:#2a3036}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:900px;padding:16px}}
h1{{font-size:1.4rem;margin:.2em 0}}h2{{font-size:1.1rem;margin:1.4em 0 .4em}}table{{border-collapse:collapse;width:100%;font-size:.88rem;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid var(--line);padding:4px 7px;text-align:right;white-space:nowrap}}th:first-child,td.n{{text-align:left}}td.n{{white-space:normal;min-width:14em;color:var(--mut)}}
.PASS{{color:var(--ok)}}.WARN{{color:var(--warn)}}.FAIL{{color:var(--bad)}}.pass{{color:var(--ok)}}.warn{{color:var(--warn)}}.fail,.bad{{color:var(--bad);font-weight:600}}.chg{{color:var(--chg);font-weight:600}}
.v{{font-size:1.6rem;font-weight:700}}.sim{{border:2px solid var(--warn);padding:6px 10px;border-radius:6px}}code{{background:var(--grid);padding:1px 5px;border-radius:4px}}
svg.chart{{width:100%;height:auto;margin:6px 0}}svg .grid{{stroke:var(--grid)}}svg .ax{{fill:var(--mut);font-size:10px}}svg .ct{{fill:var(--fg);font-size:12px;font-weight:600}}
</style></head><body>
<h1>autocal report <small>{res['id']}</small></h1>{sim}
<p><span class="v {res['verdict']}">{res['verdict']}</span> &nbsp; {html.escape(res['summary'])}</p>
<p>Mode: {res['mode']} ({len(res['positions'])} position{'s' if len(res['positions']) != 1 else ''}). Target: {res['target']['name']} ({res['target']['hump_db']:g} dB hump). Created {html.escape(res['created'])}.</p>{one}
<h2>Warnings</h2><ul>{warns}</ul>
<h2>Delay, trim, polarity</h2>
<table><tr><th>speaker</th><td>arrival ms re FL</td><td>delay now</td><td>delay new</td><td>level dB</td><td>trim now</td><td>trim new</td><td>polarity</td><td>SNR dB</td><td>notes</td></tr>{rows}</table>
<p>Levels are transfer gains (mic / digital), relative only; there is no SPL claim anywhere. Changes under 0.15 ms or 1 dB are not proposed.</p>
<h2>Bass EQ</h2>{bchart}<p>rms distance to target, 30-250 Hz: {b['rms_to_target_before_db']:.1f} dB now, {b['rms_to_target_after_db']:.1f} dB predicted after. Proposed extra cuts (added to the current set):</p><ul>{bf_rows}</ul>
<h2>Heights</h2><ul>{hf_rows}</ul><p>Heights trim {res['heights']['trim_new_db']:+.2f} dB; rms to the bed {_fmt(res['heights']['rms_to_bed_before_db'], 1)} dB now, {_fmt(res['heights']['rms_to_bed_after_db'], 1)} dB predicted after.
Matched above 5 kHz: {'yes' if res['heights']['above_5k_matched'] else 'no (not by default)'}.</p>
<h2>Responses</h2>{fchart}{pchart}{ver}
<h2>Apply (nothing has been changed yet)</h2>
<p><code>{html.escape(res['commands']['apply'])}</code> backs up the live profile, installs this proposal and starts a 60 s trial; it reverts unless you run <code>{html.escape(res['commands']['keep'])}</code>.
Undo later with <code>{html.escape(res['commands']['rollback'])}</code>.</p>
</body></html>"""
    path = os.path.join(run_dir, "report.html")
    with open(path, "w") as fh:
        fh.write(doc)
    return path
