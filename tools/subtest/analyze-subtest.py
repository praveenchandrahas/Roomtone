#!/usr/bin/env python3
"""Analyses a recording of subtest-hw.flac played through CamillaDSP (LFE only).

  python3 -I analyze-subtest.py recording.wav [--cues=subtest-cues.json] [--out=report.txt]

Part 1  how low the sub goes: response of three sweeps (8-200 Hz) at rising level, the -3/-6/-10 dB points,
        compression between levels, and harmonic distortion against frequency.
Part 2  stepped tones: for every tone, level, harmonics and the "other" energy (rattle, buzz, noise) - and which
        tones get worse out of proportion when the level goes up.
Mic caveat: the measurement mic and its USB input roll off below about 20 Hz, so the very bottom reads low for the
mic's reasons as well as the sub's. Compare the three levels with each other; the mic's own shape is the same in all of them.
"""
import json, os, sys, wave
import numpy as np
from scipy.fft import rfft, irfft
from scipy.signal import fftconvolve, butter, sosfiltfilt, hilbert

FS = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def line(s=""):
    print(s); OUT.append(s)


def load_mono(path):
    w = wave.open(path); raw, sw, nc = w.readframes(w.getnframes()), w.getsampwidth(), w.getnchannels()
    if w.getframerate() != FS:
        raise SystemExit(f"{path}: sample rate {w.getframerate()} (need {FS})")
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
        x = np.where(x >= 1 << 23, x - (1 << 24), x) / 2**23
    elif sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) / 32768.0
    else:
        raise SystemExit("unsupported sample width (use 16 or 24 bit)")
    return x.reshape(-1, nc)[:, 0].astype(np.float64)


def chirp(ms=30, f0=400.0, f1=8000.0):
    n = int(FS * ms / 1000); k = np.log(f1 / f0); T = n / FS; t = np.arange(n) / FS
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))) * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def block_corr(x, times):
    c = chirp(); span = int((times[-1] - times[0]) * FS) + len(c); tpl = np.zeros(span)
    for tt in times:
        i = int(round((tt - times[0]) * FS)); tpl[i:i + len(c)] += c
    r = np.abs(fftconvolve(x, tpl[::-1], mode="valid"))
    # normalise by the signal energy under the template, so loud broadband buzz (a rattling cabinet, close-mic
    # recordings) cannot out-score the real, faint chirps
    en = fftconvolve(x ** 2, np.ones(span), mode="valid")
    return r / np.sqrt(np.maximum(en, 1e-12))


def refine(a, k):
    if 0 < k < len(a) - 1:
        y0, y1, y2 = a[k - 1], a[k], a[k + 1]; return k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-30)
    return float(k)


def placement(x, C):
    s, e = C["sync"][:3], C["sync"][3:]
    r = block_corr(x, s); k1 = int(np.argmax(r))
    if r[k1] / (np.median(r) + 1e-12) < 12:
        raise SystemExit("no sync chirps found: wrong recording? (check ./rec.sh check)")
    D = int((e[0] - s[0]) * FS); tol = int(0.05 * FS)

    def partner(centre):
        lo, hi = centre - tol, centre + tol
        if lo < 0 or hi >= len(r):
            return None
        k = lo + int(np.argmax(r[lo:hi])); return k if r[k] > 0.5 * r[k1] else None
    kb, ka = partner(k1 + D), partner(k1 - D)
    if kb is not None:
        A, B = refine(r, k1) / FS, refine(r, kb) / FS
    elif ka is not None:
        A, B = refine(r, ka) / FS, refine(r, k1) / FS
    else:
        t = k1 / FS
        def rms(t0, t1):
            seg = x[int(max(0, t0) * FS):int(max(0, t1) * FS)]; return np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
        after = rms(t + 3.0 + 1.0, t + 3.0 + 6.0); before = rms(t - 12.0, t - 4.0)
        if before > 3 * max(after, 1e-6):
            return refine(r, k1) / FS - e[0], 1.0, "end sync block only (the recording started late)"
        return refine(r, k1) / FS - s[0], 1.0, "start sync block only (the recording stopped before the end chirps)"
    b = (B - A) / (e[0] - s[0])
    if abs(b - 1) > 3e-4:
        raise SystemExit(f"start and end sync blocks disagree by {1e6 * (b - 1):.0f} ppm: wrong recording or a stutter")
    return A - b * s[0], b, f"start and end sync blocks (drift {1e6 * (b - 1):+.0f} ppm)"


def sweep_sig(sw):
    T, f0, f1 = sw["T"], sw["f0"], sw["f1"]
    n = int(T * FS); t = np.arange(n) / FS; k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1)); nf, no = int(0.3 * FS), int(0.05 * FS)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x


def deconv(x, a, b, sw, pre=1.0, tail=4.0):
    i0 = int(round((a + b * sw["t"] - pre) * FS)); L = int((sw["T"] + pre + tail) * FS)
    if i0 < 0 or i0 + L > len(x):
        return None
    N = 1 << int(np.ceil(np.log2(L + FS)))
    y = np.zeros(N); y[:L] = x[i0:i0 + L]
    s = sweep_sig(sw); xs = np.zeros(N); j = int(pre * FS); xs[j:j + len(s)] = s
    Y, X = rfft(y), rfft(xs); f = np.fft.rfftfreq(N, 1 / FS); P = np.abs(X) ** 2
    band = (f >= 4) & (f <= 2000)
    H = np.where(band, Y * np.conj(X) / (P + 1e-5 * P[band].max()), 0)
    return np.roll(irfft(H, N), int(pre * FS)), pre


def spec_of(h, c0, t0, t1, taper, n=1 << 17):
    """Spectrum of the IR between t0 and t1 (s, relative to the main peak), half-Hann taper on the end."""
    N = len(h); idx = (np.arange(int(t0 * FS), int(t1 * FS)) + c0) % N
    g = h[idx].copy(); nt = int(taper * FS); g[-nt:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    nh = int(0.002 * FS); g[:nh] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nh) / nh)
    return np.fft.rfftfreq(n, 1 / FS), np.abs(rfft(g, n)) ** 2


def band_db(f, P, fc, frac=6):
    m = (f >= fc * 2 ** (-1 / (2 * frac))) & (f < fc * 2 ** (1 / (2 * frac)))
    return 10 * np.log10(P[m].mean() + 1e-30)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if not args:
        print(__doc__); sys.exit(1)
    C = json.load(open(opt.get("cues", os.path.join(HERE, "subtest-cues.json"))))
    x = load_mono(args[0]); pk = np.abs(x).max()
    line(f"recording {os.path.basename(args[0])}: {len(x) / FS:.0f} s, peak {20 * np.log10(pk + 1e-12):.1f} dBFS" + ("   TOO HOT: lower the mic gain" if pk > 0.89 else ""))
    a, b, how = placement(x, C)
    line(f"placed by {how}")

    # ------------------------------------------------------------------ part 1: sweeps
    line("\nPART 1  HOW LOW DOES THE SUB GO (LFE, sweeps 8-200 Hz)")
    cent = 2 ** np.arange(np.log2(10), np.log2(200) + 0.01, 1 / 6)
    R = []
    for sw in C["sweeps"]:
        d = deconv(x, a, b, sw)
        if d is None:
            line(f"  sweep at {sw['level']:g} dBFS is not in this recording"); continue
        h, pre = d; c = int(pre * FS)
        env = np.abs(hilbert(sosfiltfilt(butter(4, [20, 90], "band", fs=FS, output="sos"), h)))
        k = c - int(0.05 * FS) + int(np.argmax(env[c - int(0.05 * FS):c + int(0.2 * FS)]))
        f, P1 = spec_of(h, k, -0.02, 0.45, 0.2)
        _, Pn = spec_of(h, k, -1.5, -0.3, 0.1)                            # nothing arrives here except noise
        Pn = Pn * ((0.45 + 0.02) / (1.5 - 0.3))
        lv = np.array([band_db(f, P1, fc) for fc in cent]); nz = np.array([band_db(f, Pn, fc) for fc in cent])
        hd = {}
        for kk in (2, 3, 4, 5):
            dt = -sw["T"] * np.log(kk) / np.log(sw["f1"] / sw["f0"])
            fk, Pk = spec_of(h, k, dt - 0.15, dt + 0.35, 0.15)
            hd[kk] = np.array([band_db(fk, Pk, fc * kk) - band_db(f, P1, fc) if fc * kk <= 2000 else np.nan for fc in cent])
        R.append(dict(level=sw["level"], lv=lv, nz=nz, hd=hd))
    if R:
        ref = max(r["lv"][(cent >= 25) & (cent <= 120)].max() for r in R)
        line("  Response, 1/6-octave, dB relative to the highest point of any sweep (the mic and USB input roll off below ~20 Hz).")
        line("  A value marked * is within 10 dB of the noise: do not trust it.")
        line("  Hz     " + "".join(f"{r['level']:>9g}dBFS" for r in R) + "     compression 1st->2nd, 2nd->3rd (dB, 0 = linear)")
        for i, fc in enumerate(cent):
            cells = ""
            for r in R:
                bad = r["lv"][i] - r["nz"][i] < 10
                cells += f"{r['lv'][i] - ref:>9.1f}{'*' if bad else ' '}    "
            comp = ""
            for p in range(len(R) - 1):
                step = R[p + 1]["level"] - R[p]["level"]
                comp += f"{(R[p + 1]['lv'][i] - R[p]['lv'][i]) - step:+7.1f}"
            line(f"  {fc:6.1f} {cells}{comp}")
        line("\n  How low (dB below the sub's own maximum in 25-120 Hz, each sweep):")
        for r in R:
            m = (cent >= 25) & (cent <= 120); top = r["lv"][m].max()
            rep = []
            for dbv in (3, 6, 10):
                ok = [fc for i, fc in enumerate(cent) if fc < 60 and r["lv"][i] >= top - dbv and r["lv"][i] - r["nz"][i] >= 10]
                # lowest frequency from which everything up to 60 Hz stays inside the limit
                lowest = None
                for i in range(len(cent) - 1, -1, -1):
                    if cent[i] > 60:
                        continue
                    if r["lv"][i] >= top - dbv and r["lv"][i] - r["nz"][i] >= 10:
                        lowest = cent[i]
                    else:
                        break
                rep.append(f"-{dbv} dB at {lowest:.1f} Hz" if lowest else f"-{dbv} dB: n/a")
            line(f"    sweep at {r['level']:g} dBFS:  " + ",   ".join(rep))
        line("\n  Distortion at the mic (harmonics 2-5 added, dB below the fundamental; -20 dB = 10 %, -30 dB = 3 %, -40 dB = 1 %):")
        line("  Hz     " + "".join(f"{r['level']:>9g}dBFS" for r in R))
        for i, fc in enumerate(cent):
            if fc < 12 or fc > 100:
                continue
            cells = ""
            for r in R:
                v = [r["hd"][kk][i] for kk in (2, 3, 4, 5) if np.isfinite(r["hd"][kk][i])]
                tot = 10 * np.log10(np.sum(10 ** (np.array(v) / 10))) if v else np.nan
                cells += f"{tot:>9.0f}   "
            line(f"  {fc:6.1f} {cells}")
        line("  (distortion estimates below about -45 dB are limited by room noise, not by the sub)")
    open(opt.get("out", "subtest-report.txt"), "w").write("\n".join(OUT) + "\n")
    part2(x, a, b, C)
    open(opt.get("out", "subtest-report.txt"), "w").write("\n".join(OUT) + "\n")
    print("\n(report saved to", opt.get("out", "subtest-report.txt") + ")")


def part2(x, a, b, C):
    line("\nPART 2  STEPPED TONES (rattle hunt)")
    rows = {}
    for cu in C["tones"]:
        t0 = a + b * cu["t"] + 1.0; t1 = a + b * cu["t"] + 3.6
        if t0 < 0 or t1 * FS > len(x):
            continue
        seg = x[int(t0 * FS):int(t1 * FS)]; n = len(seg); w = np.hanning(n)
        N = 1 << 19; S = np.abs(rfft(seg * w, N)) ** 2 * (N / n); f = np.fft.rfftfreq(N, 1 / FS)       # power per bin
        f0 = cu["f"]
        def peak(fc):
            hw = max(0.03 * fc, 1.5); m = (f >= fc - hw) & (f <= fc + hw); return S[m].sum()
        fund = peak(f0)
        pk_seg = float(np.max(np.abs(seg)))
        harm = [peak(k * f0) for k in range(2, 9) if k * f0 < 2000]
        mask = np.zeros(len(f), bool)
        for k in range(1, 11):
            hw = max(0.03 * k * f0, 1.5); mask |= (f >= k * f0 - hw) & (f <= k * f0 + hw)
        res_band = (f >= 20) & (f <= 2000) & ~mask
        resid = S[res_band].sum()
        rows[(cu["pass"], f0)] = dict(fund=10 * np.log10(fund + 1e-30), thd=10 * np.log10(sum(harm) / fund + 1e-30),
                                      other=10 * np.log10(resid / fund + 1e-30), resid_abs=10 * np.log10(resid + 1e-30), level=cu["level"], pk=pk_seg)
    if not rows:
        line("  no tones in this recording"); return
    # the noise: silence between the end sync block and the end of the recording, or before the first block
    tn = a + b * (C["sync"][-1] + 2.0)
    noise = None
    if (tn + 3.0) * FS <= len(x):
        seg = x[int(tn * FS):int((tn + 3.0) * FS)]; N = 1 << 19; S = np.abs(rfft(seg * np.hanning(len(seg)), N)) ** 2 * (N / len(seg)); f = np.fft.rfftfreq(N, 1 / FS)
        noise = 10 * np.log10(S[(f >= 20) & (f <= 2000)].sum() + 1e-30)
    freqs = sorted({k[1] for k in rows})
    # clip guard: when several tones share one identical peak (within 0.5 %) the mic chain is hitting a rail and the distortion
    # of those tones belongs to the recording, not to the sub. (Seen on 23 Oct 2026: mic a few cm from the cabinet, flat tops at -6.1 dBFS.)
    gp = float(np.max(np.abs(x)))
    railed = {k for k, v in rows.items() if v["pk"] >= 0.995 * gp}
    clipped = railed if len(railed) >= 3 else set()
    if clipped:
        line(f"\n  !! RECORDING CLIPPED: {len(clipped)} tones peak at the same level ({20 * np.log10(gp):.1f} dBFS): the mic chain is overloaded (mic too close or its gain too high).")
        line("     Their distortion and 'buzz' numbers are the recording's, not the sub's, and are marked CLIPPED below. Move the mic back (3x the distance = -10 dB)")
        line("     or lower the recording level until the loudest tone peaks below about -12 dBFS, then repeat.")
    q = [k for k in rows if k[0] == 0]; lo, hi = (C["tones"][0]["level"], C["tones"][-1]["level"])
    line(f"  quiet pass {lo:g} dBFS, loud pass {hi:g} dBFS. 'other' = everything that is not the tone or its first 10 harmonics, 20 Hz-2 kHz,")
    line("  in dB below the tone: noise, buzz, rattle. 'thd' = harmonics 2-8 in dB below the tone.")
    line("  Hz     level(q)  level(l)  gain  | thd q   thd l | other q  other l  rise | flag   (levels in dB, uncalibrated; 'compressing' = gain more than 3 dB under nominal)")
    sus = []
    for fq in freqs:
        A, B = rows.get((0, fq)), rows.get((1, fq))
        if not A or not B:
            continue
        gain = B["fund"] - A["fund"]; rise = B["other"] - A["other"]
        flags = []
        above = (B["resid_abs"] - noise) if noise is not None else 99.0
        if (0, fq) in clipped or (1, fq) in clipped:
            flags.append("CLIPPED: " + ("quiet+loud" if (0, fq) in clipped and (1, fq) in clipped else ("quiet" if (0, fq) in clipped else "loud")) + " pass, recording overloaded, ignore")
        elif above < 6 and B["other"] < -40:
            flags.append("(near the noise: unclear)")
        else:
            if (rise > 8 and B["other"] > -40) or (B["other"] > -33 and above >= 10):
                flags.append("<-- RATTLE/BUZZ likely"); sus.append(fq)
            elif (rise > 5 and B["other"] > -45) or (B["other"] > -38 and above >= 10):
                flags.append("<-- suspicious"); sus.append(fq)
        if gain < (hi - lo) - 3 and not ((0, fq) in clipped or (1, fq) in clipped):
            flags.append("compressing")
        flag = " ".join(flags)
        line(f"  {fq:6g}  {A['fund']:7.1f}  {B['fund']:7.1f}  {gain:5.1f} | {A['thd']:6.0f}  {B['thd']:6.0f} | {A['other']:7.0f}  {B['other']:7.0f}  {rise:+5.0f} | {flag}")
    line(f"  (a perfectly linear system shows 'gain' = {hi - lo:g} dB and no 'rise')")
    if sus:
        line(f"\n  Suspect tones: {', '.join(f'{f:g} Hz' for f in sus)}. Listen to those steps (tone number = position in the list 25, 28, 31.5, ...); a rattle that starts on one")
        line("  step and fades two steps later is a single resonance (loose object, panel, a port): find it by hand at that frequency.")
    else:
        if clipped:
            line("\n  No verdict for the CLIPPED tones. Among the others nothing rattles or buzzes out of proportion to the level.")
        else:
            line("\n  No tone stands out: nothing rattles or buzzes out of proportion to the level in this test.")


if __name__ == "__main__":
    main()
