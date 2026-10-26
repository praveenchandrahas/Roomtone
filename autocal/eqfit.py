"""Consensus-cut bass EQ fitter (plan section 3).

Input: bass response curves in dB re the bed's mids (one per microphone position, already averaged over the Z906 channels, measured THROUGH
the current profile) on a log grid, and a target. Output: residual peaking filters to ADD to the current profile.

Rules (defaults; every one is a parameter of `FitOptions`):
* cut-only (max boost 0; optional +3 dB with Q <= 2, off by default); max cut -15 dB per filter; Q <= 5 below 100 Hz, <= 6 above; at most 8 filters;
  nothing outside 30-250 Hz (the mic/adapter roll-off makes anything below ~30 Hz untrustworthy; above ~250 Hz is beyond the Schroeder estimate);
* error below 100 Hz is the position-weighted power average; above 100 Hz it is the CONSENSUS CUT: the 25th percentile over positions of
  (response - target) (so a peak only one position shows is not cut), with a log-frequency cross-fade between 85 and 115 Hz;
* nulls are never filled and never chased: a bin is masked if, at ANY position, it lies > 6 dB below its smoothed neighbourhood (this also masks
  nulls that move between positions); taste cuts of the base set show up as dips, are masked the same way, and are therefore preserved;
* greedy: cut at the largest unmasked peak, Q from its -3 dB width, gain chosen to minimise the rms error to target; stop at peaks < 2 dB or when
  a filter improves the rms error by < 0.3 dB; filters under 1 dB are dropped (hysteresis);
* `fit_guarded` adds the leave-one-position-out check: if the held-out gain is below half the training gain, refit with fewer, lower-Q filters.
"""
from dataclasses import dataclass, field, replace

import numpy as np

from . import profile as PF

LO_HZ, HI_HZ = 30.0, 250.0


def hump_target(freq, hump_db=20.0, f_lo=45.0, f_hi=90.0, taper_hi_oct=1.0, taper_lo_oct=1.0):
    """The owner's voicing: hump_db over the mids between f_lo and f_hi, a raised-cosine (in log frequency) taper to 0 dB one octave above f_hi
    and one octave below f_lo (the room's deepest bass is not chased), flat (0 dB re mids) beyond the tapers."""
    f = np.asarray(freq, float); x = np.log2(f)
    t = np.zeros(len(f))
    inside = (f >= f_lo) & (f <= f_hi); t[inside] = 1.0
    up = (f > f_hi) & (x < np.log2(f_hi) + taper_hi_oct)
    t[up] = 0.5 + 0.5 * np.cos(np.pi * (x[up] - np.log2(f_hi)) / taper_hi_oct)
    dn = (f < f_lo) & (x > np.log2(f_lo) - taper_lo_oct)
    t[dn] = 0.5 + 0.5 * np.cos(np.pi * (np.log2(f_lo) - x[dn]) / taper_lo_oct)
    return hump_db * t


def target_curve(freq, name="owner_hump", hump_db=20.0):
    """Selectable targets: owner_hump (default, verified choice), house (gentle +5 dB rise below 100 Hz), flat."""
    f = np.asarray(freq, float)
    if name == "owner_hump":
        return hump_target(f, hump_db)
    if name == "flat":
        return np.zeros(len(f))
    if name == "house":
        t = np.clip(np.log2(400.0 / f) / np.log2(4.0), 0, 1)      # 0 dB at 400 Hz rising to full by 100 Hz
        return 5.0 * t * t * (3 - 2 * t)
    raise ValueError(f"unknown target {name}")


@dataclass
class FitOptions:
    max_filters: int = 8
    max_cut_db: float = 15.0
    max_boost_db: float = 0.0            # 0 = cut only; 3 allows boosts (Q <= 2)
    q_max_low: float = 5.0               # below 100 Hz
    q_max_high: float = 6.0              # 100 Hz and above
    q_boost_max: float = 2.0
    q_min: float = 0.7
    min_peak_db: float = 2.0
    min_gain_rms_db: float = 0.3
    min_filter_db: float = 1.0           # hysteresis
    null_depth_db: float = 6.0
    lo_hz: float = LO_HZ
    hi_hz: float = HI_HZ
    percentile: float = 25.0
    name_prefix: str = "ac_"


def _local_level(db, freq, half_oct=0.5):
    """Smoothed neighbourhood level: median over +-half_oct octave."""
    out = np.empty(len(db)); x = np.log2(freq)
    for i in range(len(db)):
        m = np.abs(x - x[i]) <= half_oct
        out[i] = np.median(db[m])
    return out


def null_mask(freq, curves, depth_db=6.0, dilate=2):
    """True where ANY position's response is more than depth_db below its own smoothed neighbourhood (a null), dilated by `dilate` bins."""
    m = np.zeros(len(freq), bool)
    for c in curves:
        m |= (_local_level(c, freq) - c) > depth_db
    if dilate:
        k = m.copy()
        for s in range(1, dilate + 1):
            k[s:] |= m[:-s]; k[:-s] |= m[s:]
        m = k
    return m


def combined_error(freq, curves, weights, target, opts):
    """Error to target per bin: power average below 100 Hz, consensus cut above, cross-faded 85-115 Hz."""
    curves = np.asarray(curves, float); w = np.asarray(weights, float)
    avg = 10 * np.log10(np.sum(w[:, None] * 10 ** (curves / 10), 0) / w.sum())
    e_avg = avg - target
    e_cons = np.percentile(curves - target, opts.percentile, axis=0)
    x = np.clip((np.log2(freq) - np.log2(85.0)) / (np.log2(115.0) - np.log2(85.0)), 0, 1)
    return (1 - x) * e_avg + x * e_cons, avg


def _q_from_width(freq, e, i, drop=3.0):
    """Q of a peak at bin i from the width where the error has fallen by `drop` dB on each side."""
    pk = e[i]; l = i
    while l > 0 and e[l] > pk - drop:
        l -= 1
    r = i
    while r < len(e) - 1 and e[r] > pk - drop:
        r += 1
    bw = max(freq[r] - freq[l], 1e-3)
    return freq[i] / bw


def fit_bass(freq, curves, weights, target, opts=None):
    """Greedy consensus-cut fit. Returns dict(filters, fit_db (sum of the filters on `freq`), error_before, error_after, avg, notes).

    error_before/after are rms dB to target over lo..hi Hz of the position-weighted power average (nothing masked: the honest figure)."""
    opts = opts or FitOptions()
    freq = np.asarray(freq, float); curves = np.asarray(curves, float)
    e, avg = combined_error(freq, curves, weights, target, opts)
    mask = null_mask(freq, curves, opts.null_depth_db)
    valid = (freq >= opts.lo_hz) & (freq <= opts.hi_hz)
    cost_valid = valid & ~mask
    work = e.copy(); filters = []; total = np.zeros(len(freq)); notes = []
    if mask[valid].any():
        notes.append(f"{int(np.sum(mask & valid))} bins masked as nulls (never filled or chased)")
    for _ in range(opts.max_filters):
        cand = np.where(cost_valid, work, -np.inf)
        if opts.max_boost_db > 0:
            cand = np.where(cost_valid, np.maximum(work, -work), -np.inf)
        i = int(np.argmax(cand))
        if not np.isfinite(cand[i]) or abs(work[i]) < opts.min_peak_db:
            break
        boost = work[i] < 0
        if boost and opts.max_boost_db <= 0:
            break
        f0 = freq[i]
        qmax = opts.q_boost_max if boost else (opts.q_max_low if f0 < 100 else opts.q_max_high)
        q0 = float(np.clip(_q_from_width(freq, work if not boost else -work, i), opts.q_min, qmax))
        best = None
        before = float(np.sqrt(np.mean(work[cost_valid] ** 2)))
        for di in (-2, -1, 0, 1, 2):
            j = i + di
            if not (0 <= j < len(freq)) or not (opts.lo_hz <= freq[j] <= opts.hi_hz):
                continue
            for qm in (0.7, 1.0, 1.4):
                q = float(np.clip(q0 * qm, opts.q_min, qmax))
                lim = opts.max_boost_db if boost else opts.max_cut_db
                for g in np.arange(0.5, lim + 1e-9, 0.5):
                    gg = g if boost else -g
                    cost = float(np.sqrt(np.mean((work + PF.peaking_db(freq, freq[j], gg, q))[cost_valid] ** 2)))
                    if best is None or cost < best[0] - 1e-9:
                        best = (cost, freq[j], gg, q)
        if best is None or before - best[0] < opts.min_gain_rms_db:
            break
        cost, fj, gg, q = best
        f_db = PF.peaking_db(freq, fj, gg, q)
        work = work + f_db; total = total + f_db
        filters.append({"name": f"{opts.name_prefix}{len(filters) + 1}", "freq": round(float(fj), 1), "gain": round(float(gg), 2), "q": round(q, 2)})
    kept = [f for f in filters if abs(f["gain"]) >= opts.min_filter_db]
    fit_db = PF.filters_db(freq, kept)
    err_avg = avg - target
    return {"filters": kept, "fit_db": fit_db, "avg": avg, "error": e, "mask": mask,
            "rms_before": float(np.sqrt(np.mean(err_avg[valid] ** 2))),
            "rms_after": float(np.sqrt(np.mean((err_avg + fit_db)[valid] ** 2))), "notes": notes}


def _rms(freq, curve, target, opts):
    v = (freq >= opts.lo_hz) & (freq <= opts.hi_hz)
    return float(np.sqrt(np.mean((curve - target)[v] ** 2)))


def loo_check(freq, curves, weights, target, opts):
    """Leave-one-position-out: fit on the others, measure the gain at the held-out position. Returns (mean train gain, mean held-out gain)."""
    curves = np.asarray(curves, float); P = len(curves)
    tr, ho = [], []
    for p in range(P):
        keep = [k for k in range(P) if k != p]
        fit = fit_bass(freq, curves[keep], np.asarray(weights)[keep], target, opts)
        avg_keep = fit["avg"]
        tr.append(_rms(freq, avg_keep, target, opts) - _rms(freq, avg_keep + fit["fit_db"], target, opts))
        ho.append(_rms(freq, curves[p], target, opts) - _rms(freq, curves[p] + fit["fit_db"], target, opts))
    return float(np.mean(tr)), float(np.mean(ho))


GUARD_STEPS = [(8, 5.0, 6.0), (6, 4.0, 5.0), (4, 3.0, 4.0), (2, 2.5, 3.0)]


def fit_guarded(freq, curves, weights, target, opts=None):
    """fit_bass with the overfitting guard: with >= 3 positions, accept the first (filters, Q) step whose held-out gain is >= half its training
    gain; otherwise the final step is used and flagged. One or two positions: a one-point fit, said so in `notes`."""
    opts = opts or FitOptions()
    curves = np.asarray(curves, float); attempts = []
    if len(curves) < 3:
        out = fit_bass(freq, curves, weights, target, opts)
        out["notes"].append("fewer than 3 positions: this is a one-point fit and cannot be checked for overfitting")
        out["loo"] = None
        return out
    for nf, ql, qh in GUARD_STEPS:
        o = replace(opts, max_filters=min(opts.max_filters, nf), q_max_low=min(opts.q_max_low, ql), q_max_high=min(opts.q_max_high, qh))
        tr, ho = loo_check(freq, curves, weights, target, o)
        attempts.append({"max_filters": o.max_filters, "q_max": o.q_max_low, "train_gain_db": round(tr, 2), "held_out_gain_db": round(ho, 2)})
        if tr <= 0.05 or ho >= 0.5 * tr:
            out = fit_bass(freq, curves, weights, target, o)
            out["loo"] = {"passed": True, "attempts": attempts}
            if len(attempts) > 1:
                out["notes"].append(f"leave-one-position-out check reduced the fit to <= {o.max_filters} filters, Q <= {o.q_max_low:g}")
            return out
    out = fit_bass(freq, curves, weights, target, o)
    out["loo"] = {"passed": False, "attempts": attempts}
    out["notes"].append("leave-one-position-out check failed at every step: the positions disagree; treat this fit as unreliable")
    return out
