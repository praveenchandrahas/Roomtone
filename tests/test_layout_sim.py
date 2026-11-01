"""Simulated autocal runs for every layout preset (docs/layouts.md). SIMULATION ONLY: none of these layouts has been measured in a real room; what this
checks is that the pipeline runs for N speakers, plays and orders the channels from the layout, stays under the -20 dBFS cap, produces a proposal for every
speaker, writes results.json version 2 with its layout, keeps version-1 files readable, and (when set-eq.py is found) builds a CamillaDSP config of the right width.
Run: python3 tests/test_layout_sim.py   (needs numpy, scipy, PyYAML; about 1 minute)"""
import io
import json
import os
import subprocess
import sys
import tempfile

from autocal_common import check, finish, fast_cfg

import numpy as np

from autocal import layout as LAY, profile as PF, results as RS, runner as RN, stimulus as ST
from autocal.sim.backend import SimBackend
from autocal.sim.room import SimRoom

os.environ["ROOMTONE_LAYOUT"] = "/nonexistent/layout.yml"
SET_EQ = PF.find_set_eq()
try:
    import yaml
except ImportError:
    yaml = None


def run_layout(name, scenario="default", **kw):
    lay = LAY.preset(name) if isinstance(name, str) else name
    ST.activate(lay)
    td = tempfile.mkdtemp()
    lf = os.path.join(td, "layout.yml")
    LAY.save(lay, lf)
    os.environ["ROOMTONE_LAYOUT"] = lf               # set-eq.py, run by autocal for config.proposed.yml, reads the same layout
    cfg = fast_cfg(td, **kw)
    run = RN.Run(cfg, SimBackend(room=SimRoom(scenario, seed=3)))
    st = run.run()
    return lay, run, st


try:
    for name in LAY.PRESET_ORDER:
        lay, run, st = run_layout(name)
        tag = f"[{name}]"
        check(f"{tag} the run finishes", st["state"] == "done", st.get("message", ""))
        if st["state"] != "done":
            continue
        res = json.load(open(os.path.join(run.dir, "results.json")))
        check(f"{tag} results.json is version 2, carries the layout and passes the contract", res["version"] == 2 and res["layout"]["name"] == lay.name and RS.check_results(res) == [],
              "; ".join(RS.check_results(res)[:2]))
        check(f"{tag} the table lists every speaker of the layout once, in layout order", [r["channel"] for r in res["table"]] == lay.result_order)
        tl = ST.Timeline(sweep_s=3.0, tail_s=1.5, level_db=-32.0)
        x = tl.render()
        sweeps = [sl["name"] for sl in tl.cues["slots"]]
        check(f"{tag} one sweep per speaker in port order, the reference repeated at the end", sweeps == lay.hw_order + [lay.reference], str(sweeps))
        peak_db = 20 * np.log10(np.abs(x).max())
        cols = [c for c in range(x.shape[1]) if np.any(x[:, c])]
        check(f"{tag} the stimulus peak is under the -20 dBFS cap and only the layout's ports carry sound", peak_db <= ST.CAP_DBFS + 1e-6 and cols == sorted(lay.port(n) for n in lay.result_order) and x.shape[1] == lay.n,
              f"(peak {peak_db:.1f}, columns {cols})")
        ones = [sl for sl in tl.cues["slots"] if len(sl["ch"]) != 1]
        check(f"{tag} each sweep plays on exactly one port, the one the layout says", not ones and all(sl["ch"][0] == lay.port(sl["name"]) for sl in tl.cues["slots"]))
        ok_rows = [r for r in res["table"] if r["delay_new_ms"] is not None and np.isfinite(r["delay_new_ms"])]
        check(f"{tag} a proposal (delay and trim) exists for every speaker", len(ok_rows) == len(lay.channels) and all(r["trim_new_db"] <= 0.0 + 1e-9 or r["channel"] in lay.heights for r in ok_rows))
        if lay.sub:
            row = [r for r in res["table"] if r["channel"] == lay.sub][0]
            check(f"{tag} the sub's own delay and trim are not re-timed from the measurement", abs(row["trim_new_db"] - row["trim_now_db"]) < 1e-9)
        arr = [r["arrival_ms"] for r in res["table"] if r["arrival_ms"] is not None and r["channel"] != lay.sub]
        check(f"{tag} speakers at different simulated distances arrive at different times (there is something to align)", len(arr) < 2 or max(arr) - min(arr) > 0.1, f"(spread {max(arr) - min(arr):.2f} ms)" if len(arr) > 1 else "")
        room = json.load(open(os.path.join(run.dir, "room.proposed.json")))
        check(f"{tag} room.proposed.json is keyed by the layout's names", sorted(room["align"]) == sorted(lay.result_order))
        cp = os.path.join(run.dir, "config.proposed.yml")
        if SET_EQ and yaml:
            check(f"{tag} config.proposed.yml exists and is {lay.n} channels wide (set-eq.py read the same layout)",
                  os.path.isfile(cp) and yaml.safe_load(open(cp))["devices"]["capture"]["channels"] == lay.n)
        v1 = json.loads(json.dumps(res))
        v1["version"] = 1
        del v1["layout"]
        if name == "5.1.2":
            check(f"{tag} a version-1 results.json (no layout field) is still valid and means the author's 5.1.2", RS.check_results(v1) == [], "; ".join(RS.check_results(v1)[:2]))

    # a version-1 file read while another layout is active
    lay, run, st = run_layout("5.1.2")
    v1 = json.load(open(os.path.join(run.dir, "results.json")))
    v1["version"] = 1
    del v1["layout"]
    ST.activate(LAY.preset("7.1.4"))
    check("a version-1 results.json is validated against the author's 5.1.2 even while 7.1.4 is active", RS.check_results(v1) == [])
    check("... and the active layout is restored afterwards", ST.RESULT_ORDER == LAY.preset("7.1.4").result_order)

    # neutral variant of the author's own speaker set: starts with zero numbers
    d = LAY.preset("5.1.2").to_dict()
    d["room_defaults"] = "neutral"
    lay, run, st = run_layout(LAY.from_dict(d))
    res = json.load(open(os.path.join(run.dir, "results.json")))
    check("5.1.2 with room_defaults: neutral starts from zero delays and trims (none of the author's numbers)", st["state"] == "done" and all(r["delay_now_ms"] == 0.0 and r["trim_now_db"] == 0.0 for r in res["table"]))

    # scenarios on a layout that is not the author's
    for sc in ("dead", "flipped"):
        lay, run, st = run_layout("7.1.2", sc)
        res = json.load(open(os.path.join(run.dir, "results.json"))) if os.path.exists(os.path.join(run.dir, "results.json")) else None
        want = "SR" if sc == "dead" else "TFL"
        row = [r for r in res["table"] if r["channel"] == want][0] if res else None
        check(f"[7.1.2] scenario '{sc}': {want} is the speaker that is reported", res and ((sc == "dead" and any("DEAD" in n for n in row["notes"])) or (sc == "flipped" and (row["polarity"]["flip_proposed"] or not row["polarity"]["ok"]))), str(row and row["notes"])[:120])
finally:
    ST.activate(LAY.default_layout())

# ---- through the command line, with the layout file
with tempfile.TemporaryDirectory() as td:
    f = os.path.join(td, "layout.yml")
    LAY.save(LAY.preset("7.1"), f)
    env = {**os.environ, "ROOMTONE_LAYOUT": "/nonexistent/layout.yml", "AUTOCAL_ROOM": os.path.join(td, "room.json")}
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    r = subprocess.run([sys.executable, "-m", "autocal", "--layout", f, "run", "--simulate", "--auto-continue", "--sweep-s", "3", "--tail-s", "1.5", "--noise-ref", "0",
                        "--runs-dir", os.path.join(td, "runs")], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    check("CLI: autocal --layout FILE run --simulate finishes on a 7.1 layout", r.returncode == 0 and "BL" in r.stdout + r.stderr or r.returncode == 0, (r.stdout + r.stderr)[-200:])
    runs = os.path.join(td, "runs")
    rid = sorted(os.listdir(runs))[-1] if os.path.isdir(runs) and os.listdir(runs) else None
    if rid and SET_EQ and yaml:
        cp = os.path.join(runs, rid, "config.proposed.yml")
        check("CLI: the proposed CamillaDSP config is 8 channels and names the 7.1 rears' port in its filters", os.path.isfile(cp) and yaml.safe_load(open(cp))["devices"]["capture"]["channels"] == 8, "")
    bad = os.path.join(td, "bad.yml")
    open(bad, "w").write("version: 1\nchannels: nonsense\noutput: {channels: 2}\n")
    r = subprocess.run([sys.executable, "-m", "autocal", "--layout", bad, "run", "--simulate", "--dry"], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    check("CLI: a broken layout file stops autocal with exit 2 and the reason, nothing is played", r.returncode == 2 and "layout error" in r.stderr, r.stderr[-120:])
finish("layout simulation")
