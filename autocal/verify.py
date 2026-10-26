"""autocal verify: re-measure THROUGH the (now applied) profile at the centre and at one position that was NOT in the fit set, and grade it.

Thresholds are the plan's initial values (from the repo's history); they are guesses until the owner has seen real runs.
PASS if value <= limit, WARN up to 1.5 x limit, FAIL beyond.
"""
import json
import os

import numpy as np

from . import analysis as AN, eqfit, heights as HT, measure as M, profile as PF, results as RS, stimulus as ST
from .backend import FS
from .safety import SafetyMonitor

VERIFY_OFFSET_CM = (10.0, 10.0)          # an unfitted position: 10 cm right and 10 cm forward of the centre


def grade(value, limit):
    if value is None:
        return "WARN"
    return "PASS" if value <= limit else ("WARN" if value <= 1.5 * limit else "FAIL")


def run_verify(run_dir, backend, prompt=None, out=print):
    """Measures and grades. `prompt(text)` is called before the second position so the user can move the mic (sim: moves automatically)."""
    res = json.load(open(os.path.join(run_dir, "results.json")))
    det = json.load(open(os.path.join(run_dir, "details.json"))) if os.path.exists(os.path.join(run_dir, "details.json")) else {}
    level = det.get("sweep_level_dbfs", -32.0)
    sweep_s = det.get("sweep_s", 7.0); tail_s = det.get("tail_s", 2.0)
    pos = []
    for k, (label, off, w) in enumerate([("centre", (0.0, 0.0), 2.0), ("unfitted offset", VERIFY_OFFSET_CM, 1.0)]):
        if k:
            if prompt:
                prompt("Move the mic 10 cm RIGHT and 10 cm FORWARD of the centre position (a spot the fit never saw), then continue")
            backend.set_position(k + 1, off)
        tl = ST.Timeline(sweep_s=sweep_s, tail_s=tail_s, level_db=level)
        cap = backend.play_record(tl.render(), SafetyMonitor(), stage="verify", pre_s=1.0, post_s=1.0)
        if cap.aborted:
            raise RuntimeError(f"verify stopped: {cap.aborted}")
        pos.append(M.analyse_recording(cap.x, tl.cues, index=k + 1, label=label, offset_cm=off, weight=w))
    meas = AN.Measurement(pos)
    target = np.array(res["target_db"]); g = AN.GRID; v = (g >= eqfit.LO_HZ) & (g <= eqfit.HI_HZ)
    alive = meas.alive()
    bass_names = [n for n in PF.BASS_CH if n in alive]
    timed = [n for n in alive if n != "LFE"]
    checks = []

    def add(name, value, limit, unit=""):
        checks.append({"name": name, "value": None if value is None else round(float(value), 2), "limit": limit, "unit": unit, "status": grade(value, limit)})

    for k, p in enumerate(pos):
        tag = "centre" if k == 0 else "unfitted position"
        arr = [p.ch[n].arrival_ms for n in timed]
        # at the centre the delays are aligned (limit 0.3 ms); 10 cm away the geometry itself spreads arrivals by up to ~0.5 ms, so the limit there is a sanity bound
        add(f"arrival spread, {tag} (ms)", max(arr) - min(arr), 0.3 if k == 0 else 1.0)
        lv = [p.ch[n].level_db for n in ST.BED if n in alive]
        add(f"bed level spread, {tag} (dB)", max(lv) - min(lv), 2.0)
        bed = AN.pmean_db([meas.curve(n, k, 1 / 3) for n in ST.BED if n in alive])
        hts = AN.pmean_db([meas.curve(n, k, 1 / 3) for n in ST.HEIGHTS if n in alive]) if all(h in alive for h in ST.HEIGHTS) else None
        if hts is not None:
            band = (g >= HT.TRIM_LO) & (g <= HT.TRIM_HI)
            add(f"heights vs bed, 300 Hz-4 kHz mean, {tag} (dB)", abs(np.mean((hts - bed)[band])), 2.0)
        bc = AN.pmean_db([meas.curve(n, k, 1 / 6) for n in bass_names])
        add(f"30-250 Hz rms to target, {tag} (dB)", np.sqrt(np.mean((bc - target)[v] ** 2)), 3.0)
        add(f"highest peak over target 30-250 Hz, {tag} (dB)", max(0.0, float(np.max((bc - target)[v]))), 4.0)
        if k == 0:
            pred = AN.pmean_db([np.array(res["predicted_after_db"][n]) for n in bass_names])
            add("prediction vs measured, 30-250 Hz rms (dB)", np.sqrt(np.mean((bc - pred)[v] ** 2)), 2.5)
            add("FL repeat (dB)", p.repeat_fl_rms_db, 1.0)
        add(f"clipping, {tag} (clipped samples)", p.clipped, 0)
        add(f"mic peak, {tag} (dBFS, limit -6)", p.peak_dbfs + 6.0, 0.0)
    rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    overall = max((c["status"] for c in checks), key=lambda s: rank[s])
    ver = {"checks": checks, "overall": overall, "positions": [p.label for p in pos]}
    res["verify"] = ver
    RS.write_json(os.path.join(run_dir, "results.json"), res)
    RS.write_json(os.path.join(run_dir, "verify.json"), ver)
    from . import report
    report.write_report(run_dir, res)
    out(f"verify {overall}")
    for c in checks:
        out(f"  {c['status']:<4} {c['name']}: {c['value']} (limit {c['limit']})")
    return ver
