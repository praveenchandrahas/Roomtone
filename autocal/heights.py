"""Heights tone fit (plan section 3): match the two ceiling heights' tone and level to the bed, as a RESIDUAL on the current height tone filters.

Works on 1/3-octave curves (dB re the bed's mids, measured through the current profile). Rules:
* error = power mean of RL and RR minus the power mean of the five bed speakers, 170 Hz - 5 kHz (above 5 kHz only on request: elevation
  cues make 'match the bed' questionable, so `above_5k_matched` stays False by default);
* only features BOTH heights share, and that >= 60 % of the (position x height) curves show (3 of 5 positions), are fitted: the consensus error is
  the 25th percentile of the same-signed magnitudes, and zero where the sign disagrees;
* at most 3 NEW cut filters (Q <= 3, total cut per filter <= -8 dB); a residual near an existing tone filter (within 1/3 octave) changes that filter's
  gain instead of adding another; changes under 1 dB are dropped (hysteresis);
* the trim comes from the 300 Hz - 4 kHz mean after the filters; it may not rise above max(current trim, 0) (no boosts above 0 dB unless allowed).
"""
from dataclasses import dataclass

import numpy as np

from . import profile as PF

CENTRES = 1000.0 * 2 ** (np.arange(-8, 14) / 3.0)          # 1/3-octave centres, 157 Hz .. 12.7 kHz
FIT_LO, FIT_HI = 170.0, 5000.0
TRIM_LO, TRIM_HI = 300.0, 4000.0


@dataclass
class HeightOptions:
    max_new_filters: int = 3
    q_max: float = 3.0
    q_min: float = 0.7
    max_cut_db: float = 8.0
    min_peak_db: float = 2.0
    min_change_db: float = 1.0
    min_gain_rms_db: float = 0.3
    agree: float = 0.6
    allow_boost: bool = False
    match_above_5k: bool = False
    shelf_max_db: float = 4.0


def consensus(errs, agree=0.6):
    """errs: (curves, bins). Sign-consistent magnitude: 25th percentile of |e| among the curves agreeing with the median's sign, if at least
    `agree` of the curves do; else 0."""
    errs = np.asarray(errs, float); out = np.zeros(errs.shape[1])
    for k in range(errs.shape[1]):
        col = errs[:, k]; s = np.sign(np.median(col))
        if s == 0:
            continue
        same = col[np.sign(col) == s]
        if len(same) / len(col) >= agree:
            out[k] = s * np.percentile(np.abs(same), 25)
    return out


def _pm(curves, weights=None):
    c = np.asarray(curves, float)
    w = np.ones(len(c)) if weights is None else np.asarray(weights, float)
    return 10 * np.log10(np.sum(w[:, None] * 10 ** (c / 10), 0) / w.sum())


def fit_heights(centres, bed, rl, rr, weights, room, opts=None, extra_shift_db=0.0):
    """bed, rl, rr: (positions, bins) dB re the bed mids at `centres`. extra_shift_db: mean level change already planned by per-channel trims.

    Returns dict(filters_residual [{name,type,freq,gain,q} to ADD/merge], new_tone_filters (merged full list), trim_res, trim_new,
    rms_before, rms_after, above_5k_matched, fit_db (residual filters' response on centres, incl. trim), notes)."""
    opts = opts or HeightOptions()
    centres = np.asarray(centres, float); bed = np.asarray(bed, float); rl = np.asarray(rl, float); rr = np.asarray(rr, float)
    notes = []
    bed_avg = _pm(bed, weights); h_avg = _pm(np.concatenate([rl, rr]), np.concatenate([weights, weights]))
    raw = h_avg - bed_avg
    band = (centres >= FIT_LO) & (centres <= (FIT_HI if not opts.match_above_5k else 12000.0))
    tb = (centres >= TRIM_LO) & (centres <= TRIM_HI)
    m = float(np.mean(raw[tb]))
    E = np.concatenate([rl - bed, rr - bed], 0) - m                   # level offset is handled by the trim, not by filters
    shared = consensus(E, opts.agree)
    shared[~band] = 0.0
    work = shared.copy(); res = []
    tone = [dict(f) for f in room["height_tone"]["filters"]]
    for _ in range(opts.max_new_filters):
        i = int(np.argmax(work))
        if work[i] < opts.min_peak_db:
            break
        f0 = centres[i]
        best = None
        before = float(np.sqrt(np.mean(work[band] ** 2)))
        for q in (0.7, 1.0, 1.5, 2.0, 3.0):
            q = float(np.clip(q, opts.q_min, opts.q_max))
            for g in np.arange(1.0, opts.max_cut_db + 1e-9, 0.5):
                c = float(np.sqrt(np.mean((work + PF.peaking_db(centres, f0, -g, q))[band] ** 2)))
                if best is None or c < best[0]:
                    best = (c, g, q)
        if before - best[0] < opts.min_gain_rms_db:
            break
        work = work + PF.peaking_db(centres, f0, -best[1], best[2])
        res.append({"type": "Peaking", "freq": float(round(f0)), "gain": -best[1], "q": best[2]})
    # merge with the existing tone filters
    merged = [dict(f) for f in tone]; changed = []
    for r in res:
        for t in merged:
            if abs(np.log2(r["freq"] / t["freq"])) < 1 / 3:
                new_g = max(-opts.max_cut_db, t["gain"] + r["gain"]) if t.get("type", "Peaking") == "Peaking" else t["gain"]
                d = new_g - t["gain"]
                if abs(d) >= opts.min_change_db:
                    t["gain"] = round(new_g, 2); changed.append(dict(t, delta=round(d, 2)))
                break
        else:
            if r["gain"] <= -opts.min_change_db:
                nf = {"name": f"ht_ac{len(merged) + 1}", "type": "Peaking", "freq": r["freq"], "gain": round(r["gain"], 2), "q": round(r["q"], 2)}
                merged.append(nf); changed.append(dict(nf, delta=nf["gain"]))
    if opts.match_above_5k:
        hf = (centres >= 6300)
        e_hf = float(np.mean((raw - m)[hf])) if hf.any() else 0.0
        sh = [t for t in merged if t.get("type") == "Highshelf"]
        if sh and abs(e_hf) > 1.0:
            t = sh[0]; new_g = float(np.clip(t["gain"] - e_hf, -opts.shelf_max_db, opts.shelf_max_db))
            if abs(new_g - t["gain"]) >= opts.min_change_db:
                changed.append(dict(t, delta=round(new_g - t["gain"], 2))); t["gain"] = round(new_g, 2)
    # response of the net residual on the centres (peaking cuts / shelf changes)
    delta_filters = []
    for t_new in merged:
        old = next((o for o in tone if o["name"] == t_new["name"]), None)
        dg = t_new["gain"] - (old["gain"] if old else 0.0)
        if abs(dg) > 1e-9:
            delta_filters.append({"type": t_new.get("type", "Peaking"), "freq": t_new["freq"], "gain": dg, "q": t_new["q"]})
    fit_db = PF.filters_db(centres, delta_filters)
    after_shape = raw + fit_db
    m_after = float(np.mean(after_shape[tb])) + extra_shift_db
    trim_now = float(room["height_tone"]["trim_db"])
    trim_res = -m_after
    cap = trim_now if opts.allow_boost else max(trim_now, 0.0)
    trim_new = min(trim_now + trim_res, max(cap, -99))
    if abs(trim_new - trim_now) < opts.min_change_db:
        trim_new = trim_now
    elif trim_now + trim_res > cap + 1e-9:
        notes.append(f"heights are {-trim_res:.1f} dB below the bed but the trim may not rise above {cap:+.1f} dB (cut-only); not boosted")
    shift = trim_new - trim_now + extra_shift_db
    rb = float(np.sqrt(np.mean(raw[band] ** 2)))
    ra = float(np.sqrt(np.mean((after_shape + shift)[band] ** 2)))
    return {"filters_residual": changed, "new_tone_filters": merged, "trim_res": trim_new - trim_now, "trim_new": round(trim_new, 2),
            "rms_before": rb, "rms_after": ra, "above_5k_matched": bool(opts.match_above_5k), "fit_db": fit_db, "shared": shared,
            "level_offset_db": m, "notes": notes}
