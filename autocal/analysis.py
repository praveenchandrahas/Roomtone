"""Turns measurements (one or five positions, taken THROUGH the current CamillaDSP profile) into a proposal of residual changes.

new = now + residual, with hysteresis: a delay change under 0.15 ms or a trim change under 1 dB keeps the current value.

* Delay: arrival (re FL) per speaker; the latest speaker among FL FR FC SL SR RL RR is the reference; each other speaker gets the difference added.
  Then everything is shifted so the smallest of the 8 delays is not above its old value (no growth run after run). The LFE is measured and
  reported (it carries the Z906's own ~6-7 ms lag, which cannot be corrected) but not re-timed on its own: it follows FL's change, as ALIGN does.
* Trim: levels 500-2000 Hz; trims <= 0 against the quietest bed speaker at trim 0 (so nothing is boosted above 0 dB). LFE (sub knob) untouched.
  Heights: L/R difference by per-channel trim, the common offset to the bed by the height trim (heights.py).
* Polarity: for each speaker, predict the summed power with each other speaker in phase and inverted (150-800 Hz, delays removed,
  the heights' known high-pass/tone divided out). Flip proposed only if the inverted sum is >= 3 dB better at >= 60 % of the positions.
* Bass EQ: eqfit.fit_guarded on the power average over the Z906 channels. Heights tone: heights.fit_heights.
"""
from dataclasses import dataclass
import copy

import numpy as np

from . import eqfit, heights as HT, ir as IR, profile as PF
from .stimulus import RESULT_ORDER, BED, HEIGHTS

GRID = np.geomspace(20, 20000, 200)
DELAY_HYST_MS = 0.15
TRIM_HYST_DB = 1.0
POL_BAND = (150.0, 800.0)
POL_FLIP_DB = 3.0
MAX_SHIFT_WARN_MS = 1.5
MAX_TRIM_WARN_DB = 6.0
ROUND_MS = 2


@dataclass
class Options:
    target: str = "owner_hump"
    hump_db: float = 20.0
    allow_boost: bool = False
    bass: eqfit.FitOptions = None
    heights: HT.HeightOptions = None


def pmean_db(arrs, weights=None):
    a = np.asarray(arrs, float)
    w = np.ones(len(a)) if weights is None else np.asarray(weights, float)
    shape = (-1,) + (1,) * (a.ndim - 1)
    return 10 * np.log10(np.sum(w.reshape(shape) * 10 ** (a / 10), 0) / w.sum())


def wmean(vals, weights):
    v = np.asarray(vals, float); w = np.asarray(weights, float)
    return float(np.sum(v * w) / np.sum(w))


class Measurement:
    """A set of PositionData with curve helpers."""

    def __init__(self, positions):
        self.pos = positions
        self.w = np.array([p.weight for p in positions], float)
        self._cache = {}

    @property
    def names(self):
        return RESULT_ORDER

    def dead(self, n):
        return all(p.ch[n].dead for p in self.pos if n in p.ch) if any(n in p.ch for p in self.pos) else True

    def alive(self):
        return [n for n in RESULT_ORDER if not self.dead(n)]

    def per_pos(self, n, attr):
        return [getattr(p.ch[n], attr) for p in self.pos if n in p.ch and not p.ch[n].dead]

    def wvals(self, n, attr):
        vals, ws = [], []
        for p in self.pos:
            if n in p.ch and not p.ch[n].dead:
                vals.append(getattr(p.ch[n], attr)); ws.append(p.weight)
        return wmean(vals, ws) if vals else None

    def ref_db(self, pi):
        """Bed mids reference of position pi: power mean of the live bed speakers' 500-2000 Hz levels."""
        p = self.pos[pi]
        lv = [p.ch[n].level_db for n in BED if n in p.ch and not p.ch[n].dead]
        return float(pmean_db(lv))

    def curve(self, n, pi, width):
        """dB curve re the position's bed mids, on GRID, smoothed by `width` octaves."""
        key = (n, pi, round(width, 4))
        if key not in self._cache:
            c = self.pos[pi].ch[n]
            self._cache[key] = IR.smooth_db(c.f, c.H, GRID, width) - self.ref_db(pi)
        return self._cache[key]

    def curve_at(self, n, pi, centres, width):
        c = self.pos[pi].ch[n]
        return IR.smooth_db(c.f, c.H, centres, width) - self.ref_db(pi)


def _hyst(now, new, thr):
    return now if abs(new - now) < thr else new


def _pol_margin(meas, pi, name, room, others, arr_mean, T):
    """In-phase advantage in dB (median over `others`) of speaker `name` at position pi; None if it cannot be judged."""
    p = meas.pos[pi]
    f = p.ch[name].f
    m = (f >= POL_BAND[0]) & (f <= POL_BAND[1]); fm = f[m]

    def prep(n):
        c = p.ch[n]
        h = c.H[m] * np.exp(2j * np.pi * fm * (arr_mean[n] - T) / 1000.0)          # remove this speaker's excess delay: predict the aligned sum
        prof = PF.response(room, n, fm, parts=("bass", "tone", "hp"))
        return h / prof
    a = prep(name)
    vals = []
    for o in others:
        b = prep(o)
        pp = np.sum(np.abs(a + b) ** 2); pm = np.sum(np.abs(a - b) ** 2)
        vals.append(10 * np.log10((pp + 1e-30) / (pm + 1e-30)))
    return float(np.median(vals)) if vals else None


def propose(meas, room, opts=None, previous=None):
    """The whole proposal. Returns a dict with: table, new_room, curves, bass, heights, warnings, verdict, summary, details."""
    opts = opts or Options()
    P = len(meas.pos); w = meas.w
    alive = meas.alive(); warnings = []
    dead = [n for n in RESULT_ORDER if n not in alive]
    A = room["align"]
    timed = [n for n in RESULT_ORDER if n in alive and n != "LFE"]
    arr_pp = {n: [p.ch[n].arrival_ms for p in meas.pos if n in p.ch and not p.ch[n].dead] for n in RESULT_ORDER}
    arr_mean = {n: meas.wvals(n, "arrival_ms") for n in alive}

    # ---- delays -------------------------------------------------------------------------------------------------------------------
    T = max(arr_mean[n] for n in timed)
    resid = {n: (T - arr_mean[n]) for n in timed}
    # the LFE is not measured reliably in time (Z906 lag): it tracks FL, as ALIGN does, so the bed-to-sub relation is kept
    track = "FL" if "FL" in resid else (timed[0] if timed else None)
    for n in ["LFE"] + dead:                      # LFE and dead channels cannot be timed: they follow FL's change so their relation to the group is kept
        resid[n] = resid.get(track, 0.0) if track else 0.0
    raw_new = {n: A[n]["delay_ms"] + resid.get(n, 0.0) for n in RESULT_ORDER}
    shift = min(raw_new.values())
    delay_new = {n: round(_hyst(A[n]["delay_ms"], raw_new[n] - shift, DELAY_HYST_MS), ROUND_MS) for n in RESULT_ORDER}
    if all(abs(delay_new[n] - A[n]["delay_ms"]) < 1e-9 for n in RESULT_ORDER):
        delay_new = {n: A[n]["delay_ms"] for n in RESULT_ORDER}

    # ---- trims --------------------------------------------------------------------------------------------------------------------
    bed = [n for n in BED if n in alive]
    lvl = {n: meas.wvals(n, "level_db") for n in alive}
    trim_new = {n: A[n]["trim_db"] for n in RESULT_ORDER}
    if len(bed) >= 2:
        raw_lvl = {n: lvl[n] - A[n]["trim_db"] for n in bed}
        t_l = min(raw_lvl.values())
        for n in bed:
            trim_new[n] = round(min(0.0, _hyst(A[n]["trim_db"], t_l - raw_lvl[n], TRIM_HYST_DB)), 2)
    hs = [n for n in HEIGHTS if n in alive]
    if len(hs) == 2:
        lm = {n: meas.wvals(n, "level_mid_db") for n in hs}
        d = lm["RL"] - lm["RR"]
        dl = {"RL": min(0.0, -d), "RR": min(0.0, d)}
        for n in hs:
            trim_new[n] = round(_hyst(A[n]["trim_db"], A[n]["trim_db"] + dl[n], TRIM_HYST_DB), 2)
    dtrim = {n: trim_new[n] - A[n]["trim_db"] for n in RESULT_ORDER}

    # ---- polarity -----------------------------------------------------------------------------------------------------------------
    pol = {}
    judged = [n for n in timed]
    need = max(1, int(np.ceil(0.6 * P)))
    for n in RESULT_ORDER:
        if n == "LFE" or n not in alive or len(judged) < 3:
            pol[n] = {"ok": True, "flip_proposed": False, "margin_db": None}
            continue
        others = [o for o in judged if o != n]
        ms = [_pol_margin(meas, pi, n, room, others, arr_mean, T) for pi in range(P)]
        ms = [m for m in ms if m is not None]
        med = float(np.median(ms)) if ms else None
        flips = sum(m <= -POL_FLIP_DB for m in ms)
        pol[n] = {"ok": bool(med is None or med >= 0), "flip_proposed": bool(flips >= need), "margin_db": None if med is None else round(med, 1)}

    # ---- bass EQ ------------------------------------------------------------------------------------------------------------------
    target = eqfit.target_curve(GRID, opts.target, opts.hump_db)
    bass_names = [n for n in PF.BASS_CH if n in alive]
    bass_curves = [pmean_db([meas.curve(n, pi, 1 / 6) for n in bass_names]) for pi in range(P)]
    bopts = opts.bass or eqfit.FitOptions(max_boost_db=3.0 if opts.allow_boost else 0.0)
    fit = eqfit.fit_guarded(GRID, bass_curves, w, target, bopts)
    extra_old = room["bass"].get("extra_filters", [])
    extra_new = PF.merge_filters(extra_old, fit["filters"])
    if len(extra_new) > 8:
        warnings.append(f"{len(extra_new)} extra bass filters in total (limit 8): consider re-fitting from the base set")

    # ---- heights ------------------------------------------------------------------------------------------------------------------
    hres = None
    if len(hs) == 2 and len(bed) >= 2:
        bed_c = np.array([pmean_db([meas.curve_at(n, pi, HT.CENTRES, 1 / 3) for n in bed]) for pi in range(P)])
        rl_c = np.array([meas.curve_at("RL", pi, HT.CENTRES, 1 / 3) for pi in range(P)])
        rr_c = np.array([meas.curve_at("RR", pi, HT.CENTRES, 1 / 3) for pi in range(P)])
        hopts = opts.heights or HT.HeightOptions(allow_boost=opts.allow_boost)
        hres = HT.fit_heights(HT.CENTRES, bed_c, rl_c, rr_c, w, room, hopts, extra_shift_db=(dtrim["RL"] + dtrim["RR"]) / 2)
        warnings += hres["notes"]
    else:
        warnings.append("heights tone not fitted (a height channel or too few bed speakers are silent)")

    # ---- new room -----------------------------------------------------------------------------------------------------------------
    new_room = copy.deepcopy(room)
    for n in RESULT_ORDER:
        new_room["align"][n] = {"delay_ms": delay_new[n], "trim_db": trim_new[n]}
    new_room["bass"]["extra_filters"] = extra_new
    if hres:
        new_room["height_tone"]["filters"] = hres["new_tone_filters"]
        new_room["height_tone"]["trim_db"] = hres["trim_new"]
    # inversion is not part of room.json (schema section 1): a proposed flip is reported, never applied automatically.

    # ---- curves for the contract --------------------------------------------------------------------------------------------------
    resp = {}
    pred = {}
    ref_shift = 0.0
    if bed:
        before = pmean_db([lvl[n] for n in bed]); after = pmean_db([lvl[n] + dtrim[n] for n in bed]); ref_shift = after - before
    extra_db = PF.filters_db(GRID, fit["filters"])
    for n in RESULT_ORDER:
        rec = {}
        cs = []
        for pi in range(P):
            if n in meas.pos[pi].ch:
                c = meas.curve(n, pi, 1 / 6); cs.append(c); rec[f"pos{pi + 1}"] = c
        if not cs:
            continue
        avg = pmean_db(cs, [w[pi] for pi in range(P) if n in meas.pos[pi].ch])
        resp[n] = {"avg": avg, **rec}
        p_ = avg + dtrim[n] - ref_shift
        if n in PF.BASS_CH:
            p_ = p_ + extra_db
        if n in HEIGHTS and hres:
            p_ = p_ + _height_delta_db(room, new_room) + (hres["trim_new"] - room["height_tone"]["trim_db"])
        pred[n] = p_

    ok_bass = [n for n in bass_names if n in resp]
    cur_avg = pmean_db([resp[n]["avg"] for n in ok_bass])
    pred_avg = pmean_db([pred[n] for n in ok_bass])
    v = (GRID >= eqfit.LO_HZ) & (GRID <= eqfit.HI_HZ)
    rms = lambda c: float(np.sqrt(np.mean((c - target)[v] ** 2)))
    bsel = (GRID >= 25) & (GRID <= 300)
    bass = {"freq": GRID[bsel], "current_db": cur_avg[bsel], "proposed_extra_db": extra_db[bsel],
            "filters_current": PF.bass_filters(room), "filters_extra_proposed": fit["filters"],
            "rms_to_target_before_db": rms(cur_avg), "rms_to_target_after_db": rms(pred_avg)}
    heights = {"tone_filters_proposed": hres["filters_residual"] if hres else [], "trim_new_db": hres["trim_new"] if hres else room["height_tone"]["trim_db"],
               "rms_to_bed_before_db": hres["rms_before"] if hres else None, "rms_to_bed_after_db": hres["rms_after"] if hres else None,
               "above_5k_matched": bool(hres["above_5k_matched"]) if hres else False}

    # ---- table --------------------------------------------------------------------------------------------------------------------
    table = []
    for n in RESULT_ORDER:
        notes = []
        for pi in range(P):
            if n in meas.pos[pi].ch:
                for s in meas.pos[pi].ch[n].notes:
                    if s not in notes and (pi == 0 or "no usable" not in s):
                        notes.append(s)
        if n == "LFE":
            notes.append("LFE: delay and trim not changed (Z906 sub lag is internal; sub knob untouched); arrival is the 40-120 Hz envelope, a rough guide; level is 40-100 Hz")
        if n in dead:
            notes.append("DEAD: excluded from every fit")
        snrs = meas.per_pos(n, "snr_db")
        sh = abs(delay_new[n] - A[n]["delay_ms"])
        if n in alive and sh > MAX_SHIFT_WARN_MS:
            warnings.append(f"{n}: delay changes by {sh:.2f} ms (> {MAX_SHIFT_WARN_MS} ms): a speaker or the mic has moved, or a cable changed")
        if n in alive and abs(dtrim[n]) > MAX_TRIM_WARN_DB:
            warnings.append(f"{n}: trim changes by {dtrim[n]:+.1f} dB (> {MAX_TRIM_WARN_DB:g} dB): check gains and cables")
        table.append({"channel": n, "arrival_ms": None if n in dead else round(arr_mean[n], 3), "delay_now_ms": A[n]["delay_ms"],
                      "delay_new_ms": delay_new[n], "level_db": None if n in dead else round(lvl[n], 1), "trim_now_db": A[n]["trim_db"],
                      "trim_new_db": trim_new[n], "polarity": pol[n], "snr_db": None if not snrs else round(float(np.mean(snrs)), 1),
                      "notes": notes})

    for n, pr in pol.items():
        if pr["flip_proposed"]:
            warnings.append(f"{n}: polarity looks INVERTED (inverted sum better by {-pr['margin_db']:.1f} dB). Not applied automatically: check the wiring, then use set-eq --invert={n}")
    if dead:
        warnings.append("dead channel(s): " + ", ".join(dead))
    if fit.get("loo") and not fit["loo"]["passed"]:
        warnings.append("bass fit failed the leave-one-position-out check: positions disagree")
    p1 = meas.pos[0]
    if p1.repeat_fl_rms_db is not None and p1.repeat_fl_rms_db > 1.0:
        warnings.append(f"FL repeated sweep differs by {p1.repeat_fl_rms_db:.1f} dB rms (> 1 dB): noise or a disturbance during the run")
    for p in meas.pos:
        for n, c in p.ch.items():
            if c.pilot_dev_ms is not None and abs(c.pilot_dev_ms) > 1.0:
                warnings.append(f"position {p.index} {n}: pilot chirp timing off by {c.pilot_dev_ms:+.2f} ms (possible xrun)")
    lowsnr = [n for n in alive if n not in dead and any((s is not None and s < 30) for s in meas.per_pos(n, "snr40_db"))]
    if lowsnr:
        warnings.append("SNR at 40 Hz under 30 dB for " + ", ".join(lowsnr) + ": measure again when quieter")
    if previous:
        warnings += compare_previous(table, previous)

    # ---- verdict, summary ---------------------------------------------------------------------------------------------------------
    changes = []
    dch = [n for n in RESULT_ORDER if abs(delay_new[n] - A[n]["delay_ms"]) > 1e-9]
    tch = [n for n in RESULT_ORDER if abs(trim_new[n] - A[n]["trim_db"]) > 1e-9]
    if dch: changes.append("delay " + ", ".join(f"{n} {A[n]['delay_ms']:.2f}->{delay_new[n]:.2f} ms" for n in dch))
    if tch: changes.append("trim " + ", ".join(f"{n} {A[n]['trim_db']:+.1f}->{trim_new[n]:+.1f} dB" for n in tch))
    if fit["filters"]: changes.append(f"{len(fit['filters'])} extra bass cut(s)")
    if hres and (hres["filters_residual"] or abs(hres["trim_res"]) > 0): changes.append("heights tone/trim")
    flips = [n for n, p in pol.items() if p["flip_proposed"]]
    if dead or any("failed the leave-one" in x for x in warnings):
        verdict = "FAIL"
    elif warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"
    one = "" if P > 1 else " One microphone position: a one-point tuning."
    summary = ("No change needed: the current profile is within the thresholds." if not changes else "Proposed: " + "; ".join(changes) + ".") + one
    if dead:
        summary = f"Dead channel(s) {', '.join(dead)}: fix the wiring first. " + summary
    if flips:
        summary += f" Polarity of {', '.join(flips)} looks inverted (check wiring)."

    # ---- impulse responses, scatter -----------------------------------------------------------------------------------------------
    irs = {}
    for n in RESULT_ORDER:
        c = meas.pos[0].ch.get(n)
        if c is not None and c.ir_h is not None:
            pk = float(np.max(np.abs(c.ir_h))) or 1.0
            irs[n] = {"t_ms": [round(float(t), 3) for t in c.ir_t_ms], "h": [round(float(x) / pk, 4) for x in c.ir_h]}
    scatter = {n: [round(float(p.ch[n].arrival_ms), 3) for p in meas.pos if n in p.ch and not p.ch[n].dead] for n in RESULT_ORDER}

    return {"table": table, "new_room": new_room, "response": resp, "predicted": pred, "target": target, "bass": bass, "heights": heights,
            "warnings": warnings, "verdict": verdict, "summary": summary, "ir": irs, "arrival_scatter": scatter, "fit": fit, "hres": hres,
            "polarity": pol, "dead": dead, "delay_shift_ms": shift, "notes": list(fit["notes"])}


def _height_delta_db(old_room, new_room):
    """dB change of the heights tone-filter chain between two rooms, on GRID (filters matched by name)."""
    def chain(r):
        return PF.filters_db(GRID, [dict(f) for f in r["height_tone"]["filters"]])
    return chain(new_room) - chain(old_room)


def compare_previous(table, prev):
    """Warn if a speaker shifted > 1.5 ms or 6 dB since the previous run (mic moved, or a cable)."""
    out = []
    try:
        old = {r["channel"]: r for r in prev["table"]}
        for r in table:
            o = old.get(r["channel"])
            if not o or r["arrival_ms"] is None or o.get("arrival_ms") is None:
                continue
            if abs(r["arrival_ms"] - o["arrival_ms"]) > MAX_SHIFT_WARN_MS:
                out.append(f"{r['channel']}: arrival moved {r['arrival_ms'] - o['arrival_ms']:+.2f} ms since the previous run (mic moved, or a speaker/cable changed)")
            if r["level_db"] is not None and o.get("level_db") is not None and abs(r["level_db"] - o["level_db"]) > MAX_TRIM_WARN_DB:
                out.append(f"{r['channel']}: level moved {r['level_db'] - o['level_db']:+.1f} dB since the previous run")
    except (KeyError, TypeError):
        pass
    return out
