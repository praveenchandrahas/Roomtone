"""Tests for autocal/eqfit.py (consensus-cut bass fitter) including a regression on the owner's raw REW sweep (data/rew).
Run: python3 tests/test_autocal_eqfit.py   (needs numpy, scipy)"""
import os

import numpy as np

from autocal_common import check, finish, ROOT

from autocal import eqfit as E, profile as PF, analysis as AN

G = AN.GRID
T = E.target_curve(G)

# ---- target -------------------------------------------------------------------------------------------------------------------------------------------
at = lambda f: float(np.interp(f, G, T))
check("hump target: 20 dB at 45-90 Hz, ~0 dB from 400 Hz up, tapers in between", abs(at(60) - 20) < 0.3 and abs(at(80) - 20) < 0.3 and abs(at(500)) < 0.01 and abs(at(5000)) < 0.01 and 2 < at(130) < 18,
      f"(60 Hz {at(60):.1f}, 130 Hz {at(130):.1f}, 500 Hz {at(500):.1f})")
check("flat and house targets", np.all(E.target_curve(G, "flat") == 0) and E.target_curve(G, "house")[G < 80].max() > 4.5 and abs(E.target_curve(G, "house")[G > 500]).max() < 0.01)
check("hump_db is a parameter", abs(np.interp(60, G, E.target_curve(G, "owner_hump", 8.0)) - 8) < 0.1)

# ---- regression on the owner's raw REW sweep ------------------------------------------------------------------------------------------------------
d = np.loadtxt(os.path.join(ROOT, "data", "rew", "L-sweep-2026-10-19.txt"), comments="*")
f, s = d[:, 0], d[:, 1]
mids = np.mean(s[(f >= 500) & (f <= 2000)])
raw = np.interp(np.log(G), np.log(f), s) - mids
fit = E.fit_bass(G, [raw], [1.0], T)
fl = fit["filters"]
check("REW raw sweep: fit improves the rms distance to the hump target by > 3 dB", fit["rms_before"] - fit["rms_after"] > 3.0, f"({fit['rms_before']:.1f} -> {fit['rms_after']:.1f} dB)")
check("REW fit obeys the caps: <= 8 filters, cut-only, >= -15 dB, Q <= 5/6, 30-250 Hz",
      0 < len(fl) <= 8 and all(-15.0 <= x["gain"] < 0 and 30 <= x["freq"] <= 250 and x["q"] <= (5.0 if x["freq"] < 100 else 6.0) + 1e-9 for x in fl), f"({fl})")
mask = E.null_mask(G, [raw])
check("REW fit places no filter in a null of the measurement", all(not mask[int(np.argmin(abs(G - x['freq'])))] for x in fl))
hump20 = [dict(name=n, freq=fq, gain=g, q=q) for n, fq, g, q in PF.BASE_SETS["punch20x"]]
after_hand = raw + PF.filters_db(G, hump20)
v = (G >= 30) & (G <= 250)
hand = float(np.sqrt(np.mean((after_hand - T)[v] ** 2)))
check("fit is competitive with the hand-fitted punch20x set on the same data (within 1.5 dB rms)", fit["rms_after"] <= hand + 1.5, f"(autocal {fit['rms_after']:.1f} dB, hand set {hand:.1f} dB, fitted from scratch with <= 8 filters)")

# ---- synthetic cases ----------------------------------------------------------------------------------------------------------------------------------
def mk(peaks=(), dips=(), base=None):
    c = (T if base is None else base).copy()
    for f0, g, q in peaks:
        c = c + PF.peaking_db(G, f0, g, q)
    for f0, g, q in dips:
        c = c + PF.peaking_db(G, f0, g, q)
    return c

c = mk(peaks=[(63, 10, 4)], dips=[(88, -22, 8)])
r = E.fit_bass(G, [c], [1.0], T)
near63 = [x for x in r["filters"] if abs(np.log2(x["freq"] / 63)) < 0.12]
check("a 10 dB peak at 63 Hz is cut by about 10 dB", len(near63) >= 1 and -12.5 < sum(x["gain"] for x in near63) < -7.5, f"({r['filters']})")
check("no filter is placed in the 88 Hz null", all(abs(np.log2(x["freq"] / 88)) > 0.05 for x in r["filters"]))
i88 = int(np.argmin(abs(G - 88)))
check("the null is NOT boosted: filters never raise any bin (cut-only)", np.all(r["fit_db"] <= 1e-9) and (r["fit_db"][i88] <= 0.0))
i63 = int(np.argmin(abs(G - 63)))
check("the error at 63 Hz falls from ~10 dB to under 2 dB and the rms improves (the null stays in the rms: it cannot be fixed by cutting)", abs((c + r["fit_db"] - T)[i63]) < 2.0 and r["rms_after"] < r["rms_before"] - 0.3, f"({r['rms_before']:.2f} -> {r['rms_after']:.2f} dB)")

r0 = E.fit_bass(G, [mk(peaks=[(63, 1.2, 4), (110, -1.5, 3)])], [1.0], T)
check("hysteresis: deviations under the 2 dB peak limit produce no filters", r0["filters"] == [])
r1 = E.fit_bass(G, [mk(peaks=[(400, 9, 3), (20, 9, 3)])], [1.0], T)
check("nothing is fitted outside 30-250 Hz", r1["filters"] == [])
rb = E.fit_bass(G, [mk(dips=[(55, -8, 3)])], [1.0], T, E.FitOptions(max_boost_db=3.0))
check("boosts are off by default, and with max_boost 3 they respect +3 dB and Q <= 2",
      E.fit_bass(G, [mk(dips=[(55, -8, 3)])], [1.0], T)["filters"] == [] and all(0 < x["gain"] <= 3.0 and x["q"] <= 2.0 + 1e-9 for x in rb["filters"]) and len(rb["filters"]) >= 1, f"({rb['filters']})")

# ---- consensus cut and positions ------------------------------------------------------------------------------------------------------------------------
five = [mk(peaks=[(140, 9, 5)]) if k == 0 else mk() for k in range(5)]
rc = E.fit_bass(G, five, [2, 1, 1, 1, 1], T)
check("consensus cut: a peak at 140 Hz that only 1 of 5 positions shows is NOT cut", all(abs(np.log2(x["freq"] / 140)) > 0.15 for x in rc["filters"]), f"({rc['filters']})")
five3 = [mk(peaks=[(140, 9, 5)]) if k < 4 else mk() for k in range(5)]
rc3 = E.fit_bass(G, five3, [2, 1, 1, 1, 1], T)
cut140 = sum(x["gain"] for x in rc3["filters"] if abs(np.log2(x["freq"] / 140)) < 0.15)
check("consensus cut: a peak that 4 of 5 positions show is cut", cut140 < -4, f"(cut {cut140:.1f} dB)")
low = [mk(peaks=[(63, 10, 4)]) if k == 0 else mk() for k in range(5)]
rl = E.fit_bass(G, low, [2, 1, 1, 1, 1], T)
cut63 = sum(x["gain"] for x in rl["filters"] if abs(np.log2(x["freq"] / 63)) < 0.15)
check("below 100 Hz the power average is used: a 10 dB peak at the centre only (weight 2/6) is cut partly", -8 < cut63 < -1.5, f"(cut {cut63:.1f} dB)")

# leave-one-position-out guard
consistent = [mk(peaks=[(63, 9, 4), (155, 6, 5)]) + np.random.default_rng(k).normal(0, 0.3, len(G)) for k in range(5)]
rg = E.fit_guarded(G, consistent, [2, 1, 1, 1, 1], T)
check("consistent positions pass the leave-one-out check at the first step", rg["loo"]["passed"] and len(rg["loo"]["attempts"]) == 1, f"({rg['loo']['attempts']})")
rng = np.random.default_rng(7)
scattered = [mk(peaks=[(float(rng.uniform(40, 220)), float(rng.uniform(6, 12)), 5.0) for _ in range(4)]) for k in range(5)]
rs = E.fit_guarded(G, scattered, [2, 1, 1, 1, 1], T)
check("positions that disagree: the guard reduces the fit (fewer filters than the unguarded fit) or flags it",
      len(rs["filters"]) <= len(E.fit_bass(G, scattered, [2, 1, 1, 1, 1], T)["filters"]) and (len(rs["loo"]["attempts"]) > 1 or not rs["loo"]["passed"]),
      f"({len(rs['filters'])} filters, attempts {len(rs['loo']['attempts'])}, passed {rs['loo']['passed']})")
r1p = E.fit_guarded(G, [mk(peaks=[(63, 9, 4)])], [1.0], T)
check("one position: a one-point fit, stated", r1p["loo"] is None and any("one-point" in n for n in r1p["notes"]))
finish("test_autocal_eqfit")
