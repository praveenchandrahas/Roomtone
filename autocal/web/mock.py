"""Mock run directories for developing and testing the autocal web GUI without the core.

Everything follows the contract in plans/autocal-schema.md (sections 2 and 3). Pure stdlib.

    python3 -m autocal.web.mock make RUNS_DIR [--mode five|single] [--id ID] [--scenario pass|warn|fail]
    python3 -m autocal.web.mock live RUNS_DIR [--mode five] [--id ID] [--speed 1.0]
    python3 -m autocal.web.mock cli apply ID | keep | rollback     (stand-in for `python3 -m autocal ...`)

The data are invented: believable shapes, no measurement of anything.
"""
import argparse
import cmath
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone

CHANNELS = ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"]
BED = ["FL", "FR", "FC", "SL", "SR"]
HEIGHTS = ["RL", "RR"]
C_MM = 343.0  # m/s

# listener at (0,0); x right, y front, z up (metres)
SPK_POS = {"FL": (-1.9, 3.0, 0.0), "FR": (1.9, 3.0, 0.0), "FC": (0.0, 3.2, 0.0), "LFE": (-2.1, 3.1, -0.7),
           "SL": (-2.3, 0.2, 0.2), "SR": (2.3, 0.2, 0.2), "RL": (-1.3, -0.6, 1.6), "RR": (1.3, -0.6, 1.6)}
ROOM_MODES = [(38.0, 6.0, 9.0), (56.0, 4.5, 11.0), (112.0, 4.0, 14.0), (141.0, 3.0, 16.0), (196.0, 2.0, 12.0)]  # Hz, dB peak, Q
NULL_AT = (96.0, -6.0, 6.0)


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_run_id(when=None):
    return (when or datetime.now()).strftime("%Y%m%d-%H%M%S")


def write_json_atomic(path, obj):
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def peaking_db(f, f0, gain, q):
    """RBJ peaking biquad magnitude in dB at f (fs 48 kHz)."""
    fs = 48000.0
    a = 10 ** (gain / 40.0)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    b = [1 + alpha * a, -2 * math.cos(w0), 1 - alpha * a]
    d = [1 + alpha / a, -2 * math.cos(w0), 1 - alpha / a]
    z = cmath.exp(-1j * 2 * math.pi * f / fs)
    num = b[0] + b[1] * z + b[2] * z * z
    den = d[0] + d[1] * z + d[2] * z * z
    return 20 * math.log10(abs(num / den))


def freq_grid(n=121):
    return [round(20.0 * (1000.0 ** (i / (n - 1))), 3) for i in range(n)]


def _target(f, hump_db=20.0):
    """Owner voicing: hump over the mids around 45-90 Hz, flat above, gentle roll-off below 28 Hz."""
    lf = math.log2(f)
    hump = hump_db * math.exp(-((lf - math.log2(62.0)) ** 2) / (2 * 0.33 ** 2))
    low = -min(0.0, (lf - math.log2(28.0)) * 0.0) if f > 28 else -12 * (math.log2(28.0 / f)) ** 1.2
    return hump + low


def _smooth_noise(rng, n, scale, width=6):
    raw = [rng.gauss(0, 1) for _ in range(n + 2 * width)]
    out = []
    for i in range(n):
        seg = raw[i:i + 2 * width + 1]
        out.append(scale * sum(seg) / math.sqrt(len(seg)))
    return out


def _speaker_character(ch, f):
    """Frequency-dependent deviations of the rig itself, dB."""
    v = 0.0
    if ch in HEIGHTS:
        v -= 24 * max(0.0, math.log2(110.0 / f)) ** 1.1 * 0.5 if f < 110 else 0
        v += 3.0 * math.exp(-((math.log2(f) - math.log2(310)) ** 2) / (2 * 0.3 ** 2))   # honk to be cut
        v += 2.5 * math.exp(-((math.log2(f) - math.log2(2400)) ** 2) / (2 * 0.25 ** 2))
        v += 1.5 * max(0.0, math.log2(f / 6000)) if f > 6000 else 0
        v -= 2.0 * max(0.0, math.log2(f / 12000)) if f > 12000 else 0
    elif ch == "LFE":
        v -= 18 * max(0.0, math.log2(f / 160.0)) if f > 160 else 0
        v -= 14 * max(0.0, math.log2(24.0 / f)) if f < 24 else 0
    else:
        v -= 3 * max(0.0, math.log2(45.0 / f)) if f < 45 else 0
        v -= 3.0 * max(0.0, math.log2(f / 14000)) if f > 14000 else 0
    return v


def _mode_error(f, ch_gain, pos_gain):
    e = 0.0
    for f0, amp, q in ROOM_MODES:
        e += amp * ch_gain * pos_gain * math.exp(-((math.log2(f / f0)) ** 2) * q * 1.2)
    n0, ndb, nq = NULL_AT
    e += ndb * math.exp(-((math.log2(f / n0)) ** 2) * nq * 1.5) * pos_gain
    return e


def bass_filters_current():
    return [{"name": "drone2_72", "freq": 72.0, "gain": -3.0, "q": 4.0},
            {"name": "drone2_84", "freq": 84.0, "gain": -2.0, "q": 5.0}]


def bass_filters_extra():
    return [{"name": "ac_1", "freq": 40.0, "gain": -2.5, "q": 2.5},
            {"name": "ac_2", "freq": 56.0, "gain": -2.5, "q": 3.0},
            {"name": "ac_3", "freq": 126.0, "gain": -3.5, "q": 2.5},
            {"name": "ac_4", "freq": 150.0, "gain": -3.0, "q": 2.5},
            {"name": "ac_5", "freq": 190.0, "gain": -2.0, "q": 2.0}]


def heights_filters():
    return [{"name": "ht_310", "type": "Peaking", "freq": 310.0, "gain": -3.0, "q": 1.0},
            {"name": "ht_2400", "type": "Peaking", "freq": 2400.0, "gain": -2.5, "q": 2.0}]


def build_results(run_id, mode="five", seed=7, scenario="warn", simulated=True):
    rng = random.Random(seed)
    f = freq_grid()
    n = len(f)
    if mode == "five":
        positions = [{"index": 1, "label": "centre", "offset_cm": [0, 0]}, {"index": 2, "label": "20 cm left", "offset_cm": [-20, 0]},
                     {"index": 3, "label": "20 cm right", "offset_cm": [20, 0]}, {"index": 4, "label": "20 cm fore", "offset_cm": [0, 20]},
                     {"index": 5, "label": "20 cm aft", "offset_cm": [0, -20]}]
    else:
        positions = [{"index": 1, "label": "centre", "offset_cm": [0, 0]}]
    target = [round(_target(x), 2) for x in f]

    # geometry, delays and arrivals
    dist = {ch: math.dist(SPK_POS[ch], (0, 0, 0)) for ch in CHANNELS}
    far = max(dist[c] for c in CHANNELS if c != "LFE")
    delay_now = {ch: round((far - dist[ch]) / C_MM * 1000, 1) for ch in CHANNELS}
    delay_now["LFE"] = 0.0
    delay_now["RL"] = round(delay_now["RL"] - 0.6, 1) if delay_now["RL"] > 0.6 else 0.0   # a drifted value the run should find
    delay_now["SR"] = round(delay_now["SR"] + 0.3, 1)
    trim_now = {ch: 0.0 for ch in CHANNELS}
    trim_now.update({"FC": -1.0, "SL": -2.0, "SR": -2.0, "RL": -4.0, "RR": -4.0})
    arrival_scatter = {}
    for ch in CHANNELS:
        vals = []
        for p in positions:
            ox, oy = p["offset_cm"][0] / 100.0, p["offset_cm"][1] / 100.0
            d = math.dist(SPK_POS[ch], (ox, oy, 0.0))
            lag = 6.5 if ch == "LFE" else 0.0
            vals.append(round(2.9 + d / C_MM * 1000 + delay_now[ch] + lag + rng.gauss(0, 0.012), 3))
        arrival_scatter[ch] = vals
    arrival = {ch: round(sum(v) / len(v), 3) for ch, v in arrival_scatter.items()}

    # frequency responses measured through the CURRENT profile
    level_offset = {ch: -31.0 + {"FL": 0, "FR": 0.4, "FC": 1.8, "LFE": -1.5, "SL": 1.2, "SR": 2.2, "RL": 2.6, "RR": 2.4}[ch] + rng.gauss(0, 0.2) for ch in CHANNELS}
    response, per_pos_all = {}, {}
    for ch in CHANNELS:
        chg = 0.6 + 0.8 * rng.random()
        base_noise = _smooth_noise(rng, n, 0.9, 5)
        ripple_hf = [(0.8 if x > 1500 else 0.3) * math.sin(x / 90.0 + rng.random() * 0.05) for x in f]
        per_pos = []
        for pi, p in enumerate(positions):
            pg = 1.0 + (0.0 if pi == 0 else rng.uniform(-0.5, 0.6))
            pn = _smooth_noise(rng, n, 0.7, 3)
            hf_scatter = [(1.0 + 2.2 * max(0.0, math.log2(x / 800.0)) / 4.5) for x in f]
            pos_curve = []
            for i, x in enumerate(f):
                v = target[i] + _speaker_character(ch, x) + _mode_error(x, chg, pg) + base_noise[i] + ripple_hf[i] + pn[i] * hf_scatter[i]
                # the Z906 feeds every channel into the one sub, so bass sits similar for all; non-LFE gets a little less
                pos_curve.append(v)
            per_pos.append(pos_curve)
        avg = [sum(pc[i] for pc in per_pos) / len(per_pos) for i in range(n)]
        d = {"avg": [round(v, 2) for v in avg]}
        if mode == "five":
            for pi, pc in enumerate(per_pos):
                d["pos%d" % (pi + 1)] = [round(v, 2) for v in pc]
        else:
            d["pos1"] = [round(v, 2) for v in per_pos[0]]
        response[ch] = d
        per_pos_all[ch] = avg

    # bass: spatial average below 250 Hz over all channels (the Z906 filters are common)
    bi = [i for i, x in enumerate(f) if x <= 250.0]
    bass_f = [f[i] for i in bi]
    cur = [sum(per_pos_all[ch][i] for ch in CHANNELS) / 8.0 for i in bi]
    extra_f = bass_filters_extra()
    extra = [round(sum(peaking_db(x, e["freq"], e["gain"], e["q"]) for e in extra_f), 3) for x in bass_f]
    tb = [target[i] for i in bi]
    sel = [k for k, x in enumerate(bass_f) if 30 <= x <= 250]
    rms = lambda arr: math.sqrt(sum(v * v for v in arr) / len(arr))
    # rms of the error after removing the mean offset (relative curves)
    def rms_err(c):
        e = [c[k] - tb[k] for k in sel]
        m = sum(e) / len(e)
        return rms([v - m for v in e])
    net = [cur[k] + extra[k] for k in range(len(bi))]
    bass = {"freq": bass_f, "current_db": [round(v, 2) for v in cur], "proposed_extra_db": extra,
            "filters_current": bass_filters_current(), "filters_extra_proposed": extra_f,
            "rms_to_target_before_db": round(rms_err(cur), 2), "rms_to_target_after_db": round(rms_err(net), 2)}

    # heights tone
    hfs = heights_filters()
    ht_trim_now = -4.0
    ht_trim_new = -3.0
    hcorr = [sum(peaking_db(x, h["freq"], h["gain"], h["q"]) for h in hfs) for x in f]
    predicted = {}
    for ch in CHANNELS:
        av = per_pos_all[ch]
        corr = [0.0] * n
        if ch in HEIGHTS:
            corr = [hcorr[i] + (ht_trim_new - ht_trim_now) for i in range(n)]
        if ch != "LFE":
            pass
        for i, x in enumerate(f):
            if x <= 250.0:
                corr[i] += extra[bi.index(i)]
        predicted[ch] = [round(av[i] + corr[i], 2) for i in range(n)]

    def power_mean(chs, src):
        return [10 * math.log10(sum(10 ** (src[c][i] / 10) for c in chs) / len(chs)) for i in range(n)]
    h_before = power_mean(HEIGHTS, per_pos_all)
    bed_m = power_mean(BED, per_pos_all)
    h_after = power_mean(HEIGHTS, predicted)
    ks = [i for i, x in enumerate(f) if 170 <= x <= 5000]
    def rms_diff(a, b):
        e = [a[i] - b[i] for i in ks]
        m = sum(e) / len(e)
        return math.sqrt(sum((v - m) ** 2 for v in e) / len(e))
    heights = {"tone_filters_proposed": hfs, "trim_new_db": ht_trim_new, "rms_to_bed_before_db": round(rms_diff(h_before, bed_m), 2),
               "rms_to_bed_after_db": round(rms_diff(h_after, bed_m), 2), "above_5k_matched": False}

    # table
    ref_far = max(arrival[c] for c in BED + HEIGHTS)
    raw = {ch: delay_now[ch] + (ref_far - arrival[ch]) for ch in BED + HEIGHTS}
    mn = min(raw.values())
    rows = []
    min_level = min(level_offset[c] for c in BED + HEIGHTS)
    trim_raw = {ch: trim_now[ch] + (min_level - level_offset[ch]) for ch in BED + HEIGHTS}
    mx = max(trim_raw.values())
    for ch in CHANNELS:
        if ch == "LFE":
            dn, tn = delay_now[ch], trim_now[ch]
            notes = ["Z906 sub lag about 6.5 ms; level and delay reported only, sub knob untouched"]
        else:
            dn = round(raw[ch] - mn, 2)
            if abs(dn - delay_now[ch]) < 0.15:
                dn = delay_now[ch]
            tn = round(trim_raw[ch] - mx, 1)
            if abs(tn - trim_now[ch]) < 1.0:
                tn = trim_now[ch]
            notes = []
        margin = round(rng.uniform(5.0, 9.0), 1)
        pol = {"ok": True, "flip_proposed": False, "margin_db": margin}
        snr = round(rng.uniform(38, 46), 1)
        if ch == "SL":
            pol["margin_db"] = 1.4
            notes.append("polarity inconclusive: margin under 3 dB, nothing proposed")
        if ch == "RL" and scenario != "pass":
            snr = 27.5
            notes.append("SNR under 30 dB: sweep repeated once")
        if ch == "RR" and scenario == "fail":
            pol = {"ok": False, "flip_proposed": True, "margin_db": 6.8}
            notes.append("inverted sum wins by 6.8 dB at 5 of 5 positions")
        rows.append({"channel": ch, "arrival_ms": arrival[ch], "delay_now_ms": delay_now[ch], "delay_new_ms": dn,
                     "level_db": round(level_offset[ch], 1), "trim_now_db": trim_now[ch], "trim_new_db": tn,
                     "polarity": pol, "snr_db": snr, "notes": notes})

    # impulse responses
    ir = {}
    t_ms = [round(i * 0.04, 3) for i in range(1500)]
    modes_ir = [(m[0], 0.5 + 0.1 * k) for k, m in enumerate(ROOM_MODES[:4])]
    for ch in CHANNELS:
        a = arrival_scatter[ch][0]
        sign = -1.0 if (ch == "RR" and scenario == "fail") else 1.0
        fc = 90.0 if ch == "LFE" else (1800.0 if ch not in HEIGHTS else 1100.0)
        refl = [(a + 2.1 + rng.random(), -0.35 * rng.random()), (a + 5.5 + rng.random() * 2, 0.25 * rng.random()), (a + 11 + rng.random() * 3, -0.18 * rng.random())]
        h = []
        for t in t_ms:
            v = 0.0
            for (t0, g) in [(a, 1.0)] + refl:
                x = (t - t0) / 1000.0
                u = (math.pi * fc * x) ** 2
                v += g * (1 - 2 * u) * math.exp(-u)
            if t > a:
                for fm, amp in modes_ir:
                    v += 0.05 * amp * math.exp(-(t - a) / 45.0) * math.sin(2 * math.pi * fm * (t - a) / 1000.0) * (0.6 if ch != "LFE" else 1.4)
            v += rng.gauss(0, 0.004)
            h.append(v * sign)
        pk = max(abs(v) for v in h) or 1.0
        ir[ch] = {"t_ms": t_ms, "h": [round(v / pk, 4) for v in h]}

    warnings = []
    if scenario in ("warn", "fail"):
        warnings.append("RL: SNR 27.5 dB is under the 30 dB gate; the sweep was repeated once and kept.")
        if mode == "five":
            warnings.append("Position 5: the ceiling pair (RL, RR) arrived 0.2 ms off the pattern of the bed speakers; was the mic height the same?")
        warnings.append("SL polarity is inconclusive (margin 1.4 dB). Nothing proposed.")
    if scenario == "fail":
        warnings.append("RR looks inverted at 5 of 5 positions. Check the speaker wiring before applying.")
    verdict = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[scenario]
    summary = {"pass": "Delays and trims match the current profile within the scatter. Bass error falls from %.1f to %.1f dB rms." % (bass["rms_to_target_before_db"], bass["rms_to_target_after_db"]),
               "warn": "Mostly sensible: RL and SR delays and the heights tone changed, five bass cuts proposed. Review the warnings before applying.",
               "fail": "A speaker looks inverted and a channel is noisy. Do not apply; fix the cause and measure again."}[scenario]
    return {"version": 1, "id": run_id, "created": _now_iso(), "mode": mode, "simulated": simulated,
            "target": {"name": "owner_hump", "hump_db": 20}, "channels": CHANNELS, "freq": f, "positions": positions,
            "response_db": response, "target_db": target, "predicted_after_db": predicted, "table": rows, "bass": bass,
            "heights": heights, "ir": ir, "arrival_scatter": arrival_scatter, "warnings": warnings, "verdict": verdict,
            "summary": summary,
            "commands": {"apply": "autocal apply %s" % run_id, "keep": "autocal keep", "rollback": "autocal rollback"},
            "verify": None}


def room_json():
    return {"version": 1,
            "align": {ch: {"delay_ms": 0.0, "trim_db": 0.0} for ch in CHANNELS},
            "height_tone": {"filters": [{"name": "ht_310", "type": "Peaking", "freq": 310, "gain": -7.5, "q": 1.0}], "trim_db": 1.0},
            "bass": {"base_set": "drone2", "extra_filters": []}}


def make_run(runs_dir, run_id=None, mode="five", seed=7, scenario="warn", simulated=True):
    """Write a finished run directory (run.json, status.json state done, results.json, proposed files)."""
    run_id = run_id or new_run_id()
    d = os.path.join(runs_dir, run_id)
    os.makedirs(os.path.join(d, "control"), exist_ok=True)
    write_json_atomic(os.path.join(d, "run.json"), {"id": run_id, "created": _now_iso(), "mode": mode, "simulated": simulated,
                                                    "target": {"name": "owner_hump", "hump_db": 20}, "room_before": room_json(), "version": 1})
    write_json_atomic(os.path.join(d, "results.json"), build_results(run_id, mode, seed, scenario, simulated))
    npos = 5 if mode == "five" else 1
    write_json_atomic(os.path.join(d, "status.json"), status_dict(run_id, mode, "done", npos, npos, "done", 1.0, "Analysis finished", simulated=simulated))
    with open(os.path.join(d, "room.proposed.json"), "w") as f:
        json.dump(room_json(), f)
    with open(os.path.join(d, "config.proposed.yml"), "w") as f:
        f.write("# theater-profile: film (mock)\n")
    return d


def status_dict(run_id, mode, state, position, total, step, progress, message, mic=None, level=None, prompt=None, simulated=True, error=None):
    return {"id": run_id, "state": state, "mode": mode, "position": position, "positions_total": total, "step": step,
            "progress": round(progress, 3), "message": message,
            "mic": mic or {"peak_dbfs": -90.0, "rms_dbfs": -95.0, "noise_floor_dbfs": -71.0},
            "level_dbfs": level, "prompt": prompt, "simulated": simulated, "error": error, "updated": _now_iso()}


def run_live(runs_dir, run_id=None, mode="five", speed=1.0, seed=3, scenario="warn", quiet=False):
    """Play a believable status.json sequence, honouring control/continue-<id> and control/abort."""
    run_id = run_id or new_run_id()
    rng = random.Random(seed)
    d = os.path.join(runs_dir, run_id)
    os.makedirs(os.path.join(d, "control"), exist_ok=True)
    write_json_atomic(os.path.join(d, "run.json"), {"id": run_id, "created": _now_iso(), "mode": mode, "simulated": True,
                                                    "target": {"name": "owner_hump", "hump_db": 20}, "room_before": room_json(), "version": 1})
    total = 5 if mode == "five" else 1
    tick = 0.2 * speed if speed > 0 else 0.01

    def sleep(s):
        time.sleep(max(0.005, s * speed) if speed > 0 else 0.005)

    def aborted():
        return os.path.exists(os.path.join(d, "control", "abort"))

    def put(**kw):
        write_json_atomic(os.path.join(d, "status.json"), status_dict(run_id, mode, simulated=True, **kw))

    def phase(state, step, seconds, pos, mlevel, msg, mic_fn):
        steps = max(1, int(seconds / 0.2))
        for i in range(steps):
            if aborted():
                return False
            lv = mlevel(i / steps) if callable(mlevel) else mlevel
            put(state=state, position=pos, total=total, step=step, progress=(i + 1) / steps, message=msg, mic=mic_fn(lv), level=lv)
            sleep(0.2)
        return True

    def mic_for(lv):
        peak = (lv + 18.0 + rng.uniform(-2, 2)) if lv is not None else -71.0 + rng.uniform(-1, 3)
        return {"peak_dbfs": round(peak, 1), "rms_dbfs": round(peak - 12 + rng.uniform(-1, 1), 1), "noise_floor_dbfs": -71.0}

    def finish_abort():
        put(state="aborted", position=1, total=total, step="aborted", progress=0.0, message="Aborted by user", mic=mic_for(None))

    put(state="idle", position=1, total=total, step="starting", progress=0.0, message="Starting")
    sleep(0.5)
    if not phase("preflight", "preflight: mic, streams, noise floor", 3, 1, None, "Measuring noise floor (projector fan on)", mic_for):
        return finish_abort()
    if not phase("ramp", "level ramp", 5, 1, lambda x: -50.0 + 18.0 * x, "Raising level toward the -20 dBFS cap", mic_for):
        return finish_abort()
    if not phase("channel_id", "channel ID", 4, 1, -38.0, "Quiet chirp on each speaker", mic_for):
        return finish_abort()
    for pos in range(1, total + 1):
        if pos > 1:
            pid = "move-%d" % pos
            label = {2: "20 cm LEFT of the centre", 3: "20 cm RIGHT of the centre", 4: "20 cm FORWARD of the centre", 5: "20 cm BACK from the centre"}[pos]
            prompt = {"id": pid, "text": "Move the mic to %s, then continue" % label, "action": "continue"}
            cf = os.path.join(d, "control", "continue-" + pid)
            while not os.path.exists(cf):
                if aborted():
                    return finish_abort()
                put(state="waiting_user", position=pos, total=total, step="waiting for mic move", progress=0.0, message="Waiting for you", mic=mic_for(None), prompt=prompt)
                sleep(0.25)
        for ch in CHANNELS:
            if not phase("sweeping", "%s sweep" % ch, 1.2, pos, -32.0, "Sweeping %s at position %d of %d" % (ch, pos, total), mic_for):
                return finish_abort()
    if not phase("analysing", "analysing", 3, total, None, "Deconvolving and fitting", mic_for):
        return finish_abort()
    make_run(runs_dir, run_id, mode, seed=seed, scenario=scenario)
    put(state="done", position=total, total=total, step="done", progress=1.0, message="Done. Review the proposal; nothing was applied.", mic=mic_for(None))


def mock_cli(argv):
    """Stand-in for `python3 -m autocal apply|keep|rollback` used by the GUI in --mock mode."""
    if not argv or argv[0] not in ("apply", "keep", "rollback"):
        print("mock cli: unknown command", file=sys.stderr)
        return 2
    if argv[0] == "apply":
        print("[mock] backed up config to backups/config-%s.yml" % datetime.now().strftime("%H%M%S"))
        print("[mock] installed proposed config for run %s" % (argv[1] if len(argv) > 1 else "?"))
        print("[mock] 60 s trial started: it reverts unless you run `autocal keep`")
    elif argv[0] == "keep":
        print("[mock] trial accepted, proposed config kept")
    else:
        print("[mock] restored the last backup")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("make", "live"):
        p = sub.add_parser(name)
        p.add_argument("runs_dir")
        p.add_argument("--mode", choices=["single", "five"], default="five")
        p.add_argument("--id")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--scenario", choices=["pass", "warn", "fail"], default="warn")
        p.add_argument("--speed", type=float, default=1.0)
    c = sub.add_parser("cli")
    c.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "cli":
        return mock_cli(a.rest)
    os.makedirs(a.runs_dir, exist_ok=True)
    if a.cmd == "make":
        print(make_run(a.runs_dir, a.id, a.mode, a.seed, a.scenario))
    else:
        run_live(a.runs_dir, a.id, a.mode, a.speed, a.seed, a.scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
