#!/usr/bin/env python3
"""Tracks how the relative delay between two playback paths changes over time (clock drift, re-sync steps, jitter).

Why it exists: a second subwoofer behind another clock (Vantec USB vs HDMI to a soundbar) only helps if its phase against the first sub stays put.
A 20 ppm clock difference adds 1 ms per 50 s, i.e. 25 degrees at 70 Hz after one minute and 150 degrees after five.

How it works: one stereo test file plays a low-frequency chirp on the left channel (path A) and the same chirp on the right channel (path B),
B exactly GAP seconds after A, repeated every PERIOD seconds. Both come from one sample clock, so if the two paths stay locked,
(arrival of B) - (arrival of A) - GAP is constant. One microphone records the room. Both chirps are in the same recording, so the microphone's own
clock cancels out (its ppm error only touches the short GAP, and is corrected from the spacing of the A chirps).
Each chirp is found with a matched filter. The result is the relative delay of B against A for every period: drift (ppm), jitter (ms), and steps (ms).

    drift.py make  OUT.wav [--period 20 --count 180 --gap 1.5 --f0 30 --f1 120 --fs 48000]    writes OUT.wav and OUT.json
    drift.py analyze REC.wav OUT.json [--csv delays.csv]                                       the recording must start before the first chirp
"""
import argparse
import json
import sys

import numpy as np
from scipy import signal
from scipy.io import wavfile


def chirp_at(t, t0, f0, f1, dur):
    """Linear chirp starting at time t0 (seconds, may be fractional), evaluated at times t (array). Exact fractional delay, no interpolation."""
    x = t - t0
    inside = (x >= 0) & (x < dur)
    k = (f1 - f0) / dur
    y = np.zeros(len(t))
    xi = x[inside]
    y[inside] = np.sin(2 * np.pi * (f0 * xi + 0.5 * k * xi * xi)) * signal.windows.tukey(len(xi), 0.25)
    return y


def template(fs, f0, f1, dur):
    return chirp_at(np.arange(int(dur * fs)) / fs, 0.0, f0, f1, dur)


def make(path, period=20.0, count=180, gap=1.5, f0=30.0, f1=120.0, dur=1.0, fs=48000, lead=2.0, level=0.5):
    n = int((lead + count * period + gap + dur + 3) * fs)
    t = np.arange(n) / fs
    a = np.zeros(n)
    b = np.zeros(n)
    for k in range(count):
        t0 = lead + k * period
        s0, s1 = int(t0 * fs), int((t0 + gap + dur + 0.01) * fs)
        a[s0:s1] += chirp_at(t[s0:s1], t0, f0, f1, dur)
        b[s0:s1] += chirp_at(t[s0:s1], t0 + gap, f0, f1, dur)
    wavfile.write(path, fs, (np.stack([a, b], 1) * level).astype("<f4"))
    meta = dict(fs=fs, period=period, count=count, gap=gap, f0=f0, f1=f1, dur=dur, lead=lead)
    with open(path.rsplit(".", 1)[0] + ".json", "w") as f:
        json.dump(meta, f)
    return meta


def _peak(win, tpl, fs):
    """Matched-filter envelope peak in a window: (time of peak in seconds from window start, peak / median envelope)."""
    c = signal.fftconvolve(win, tpl[::-1], mode="valid")
    env = np.abs(signal.hilbert(c))
    i = int(np.argmax(env))
    if 0 < i < len(env) - 1:                                                   # parabolic refinement
        y0, y1, y2 = env[i - 1], env[i], env[i + 1]
        den = y0 - 2 * y1 + y2
        d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    else:
        d = 0.0
    return (i + d) / fs, env[i] / (np.median(env) + 1e-18)


def analyze(rec, fs, meta, min_snr=6.0, search=0.4):
    """rec: mono array at sample rate fs (the microphone's clock). Returns a dict with per-period delays and the fit."""
    period, gap, dur, count = meta["period"], meta["gap"], meta["dur"], meta["count"]
    tpl = template(fs, meta["f0"], meta["f1"], dur)
    # acquisition: the first strong chirp in the first two periods is A of period 0
    head = rec[: int(min(len(rec), (2 * period + gap + dur + 2) * fs))]
    c = np.abs(signal.hilbert(signal.fftconvolve(head, tpl[::-1], mode="valid")))
    first = int(np.argmax(c > 0.5 * c.max()))
    a_prev = first / fs
    p_est = period
    tA, tB = [], []
    for k in range(count):
        exp_a = a_prev + (p_est if k else 0.0)
        res = []
        for exp, add in ((exp_a, 0.0), (None, gap)):
            if exp is None:
                exp = (res[0] if res[0] is not None else exp_a) + gap
            s0 = int(max(0, (exp - search) * fs))
            s1 = int(min(len(rec), (exp + search + dur) * fs))
            if s1 - s0 < len(tpl) + 10:
                res.append(None)
                continue
            t, snr = _peak(rec[s0:s1], tpl, fs)
            res.append(s0 / fs + t if snr >= min_snr else None)
        tA.append(res[0] if res[0] is not None else np.nan)
        tB.append(res[1] if res[1] is not None else np.nan)
        if res[0] is not None:
            if k and not np.isnan(tA[-2]):
                p_est = 0.7 * p_est + 0.3 * (res[0] - tA[-2])
            a_prev = res[0]
        else:
            a_prev = exp_a
    tA, tB = np.array(tA), np.array(tB)
    good = ~np.isnan(tA) & ~np.isnan(tB)
    if good.sum() < 6:
        return dict(ok=False, why=f"only {int(good.sum())} of {count} periods found a clean pair (check level, mic position, recording start)")
    k = np.arange(count)
    r = np.polyfit(k[good], tA[good], 1)[0] / period                         # mic seconds per true second
    d = (tB - tA - gap * r) * 1000.0                                          # ms, B relative to A (mic clock corrected)
    d0 = np.median(d[good][:5])
    d = d - d0
    t_true = tA / r
    slope, icpt = np.polyfit(t_true[good], d[good], 1)                       # ms per s
    resid = d[good] - (slope * t_true[good] + icpt)
    mad = 1.4826 * np.median(np.abs(resid - np.median(resid)))
    steps = []
    idx = np.where(good)[0]
    dd = np.diff(d[idx])                                                      # noise scale from successive differences: a step barely moves it
    noise = 1.4826 * np.median(np.abs(dd - np.median(dd))) / np.sqrt(2)
    for j in range(3, len(idx) - 3):
        before, after = np.median(d[idx[j - 3:j]]), np.median(d[idx[j:j + 3]])
        if abs(after - before) > max(1.0, 6 * noise) and (not steps or idx[j] - steps[-1][0] > 3):
            steps.append((int(idx[j]), float(after - before)))
    return dict(ok=True, periods=int(good.sum()), of=count, mic_ppm=float((r - 1) * 1e6), drift_ppm=float(slope * 1000),
                jitter_ms=float(resid.std()), mad_ms=float(mad), total_ms=float(d[good][-1] - d[good][0]),
                per_3h_ms=float(slope * 3 * 3600), steps=steps, delays_ms=d, t_s=t_true)


def verdict(res, tol_ms=1.0):
    if not res["ok"]:
        return res["why"]
    lines = [f"{res['periods']}/{res['of']} periods measured; microphone clock {res['mic_ppm']:+.0f} ppm (removed)",
             f"relative drift  {res['drift_ppm']:+.2f} ppm  ->  {res['per_3h_ms']:+.1f} ms over a 3 h film",
             f"jitter          {res['jitter_ms']:.2f} ms rms (robust {res['mad_ms']:.2f} ms)",
             "steps           " + (", ".join(f"period {k}: {s:+.1f} ms" for k, s in res["steps"]) or "none")]
    bad = abs(res["per_3h_ms"]) > tol_ms or res["steps"] or res["jitter_ms"] > tol_ms / 2
    lines.append(("NOT LOCKED: " if bad else "LOCKED: ") + f"tolerance {tol_ms} ms (about {tol_ms * 70 * 0.36:.0f} degrees at 70 Hz)")
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("out")
    m.add_argument("--period", type=float, default=20.0)
    m.add_argument("--count", type=int, default=180)
    m.add_argument("--gap", type=float, default=1.5)
    m.add_argument("--f0", type=float, default=30.0)
    m.add_argument("--f1", type=float, default=120.0)
    m.add_argument("--fs", type=int, default=48000)
    m.add_argument("--level", type=float, default=0.1, help="peak level of the chirps, 0-1 (0.1 = -20 dBFS, the default; 0.5 = -6 dBFS is LOUD sustained bass)")
    a = sub.add_parser("analyze")
    a.add_argument("rec")
    a.add_argument("meta")
    a.add_argument("--csv")
    a.add_argument("--tol", type=float, default=1.0)
    args = ap.parse_args(argv)
    if args.cmd == "make":
        if not 0 < args.level <= 0.5:
            print("--level must be above 0 and at most 0.5")
            return 2
        meta = make(args.out, args.period, args.count, args.gap, args.f0, args.f1, fs=args.fs, level=args.level)
        print(f"wrote {args.out} ({meta['count']} pairs, one every {meta['period']} s = {meta['count'] * meta['period'] / 60:.0f} min) and its .json")
        return 0
    fs, x = wavfile.read(args.rec)
    x = x.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    with open(args.meta) as f:
        meta = json.load(f)
    res = analyze(x, fs, meta)
    print(verdict(res, args.tol))
    if res["ok"] and args.csv:
        np.savetxt(args.csv, np.stack([res["t_s"], res["delays_ms"]], 1), delimiter=",", header="t_s,delay_ms", comments="")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
