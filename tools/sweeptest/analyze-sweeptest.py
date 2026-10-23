#!/usr/bin/env python3
"""Analyses a recording of sweeptest-hw.flac played through CamillaDSP.

  python3 -I analyze-sweeptest.py recording.wav [--cues=sweeptest-cues.json] [--out=report.txt] [--heights-on]

The recording may start any time before the playback (and may even miss the first sync chirps). Reports, per speaker:
arrival time at the seat, level, 1/3-octave tone; then the crossover study: how the two heights and the LFE add
up near the crossover frequency, and the best crossover frequency / sub-path delay / polarity / height-bass gain.
--heights-on says the recording was made WITH the --heights processing active (the study then only checks it).
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


def sweep_sig(C):
    T, f0, f1 = C["sweep_s"], C["f0"], C["f1"]
    n = int(T * FS); t = np.arange(n) / FS; k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))
    nf, no = int(0.05 * FS), int(0.02 * FS)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x


def block_corr(x, times):
    c = chirp(); span = int((times[-1] - times[0]) * FS) + len(c)
    tpl = np.zeros(span)
    for tt in times:
        i = int(round((tt - times[0]) * FS)); tpl[i:i + len(c)] += c
    return np.abs(fftconvolve(x, tpl[::-1], mode="valid"))


def refine(a, k):
    if 0 < k < len(a) - 1:
        y0, y1, y2 = a[k - 1], a[k], a[k + 1]; return k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-30)
    return float(k)


def placement(x, C):
    """Recording time of file t=0 (a) and clock ratio (b), from the 2 x 3 sync chirps (FL)."""
    s, e = C["sync"][:3], C["sync"][3:]
    r = block_corr(x, s)
    k1 = int(np.argmax(r)); med = np.median(r)
    if r[k1] / (med + 1e-12) < 12:
        raise SystemExit("no sync chirps found: is this the right recording? (check ./rec.sh check)")
    D = int((e[0] - s[0]) * FS); tol = int(0.05 * FS)
    def partner(centre):
        lo, hi = centre - tol, centre + tol
        if lo < 0 or hi >= len(r):
            return None
        k = lo + int(np.argmax(r[lo:hi]))
        return k if r[k] > 0.5 * r[k1] else None
    kb = partner(k1 + D); ka = partner(k1 - D)
    if kb is not None:
        A, B = refine(r, k1) / FS, refine(r, kb) / FS
    elif ka is not None:
        A, B = refine(r, ka) / FS, refine(r, k1) / FS
    else:
        # only one block was recorded: decide whether it is the start or the end block (a sweep follows the start block)
        t = k1 / FS
        def rms(t0, t1):
            seg = x[int(max(0, t0) * FS):int(max(0, t1) * FS)]
            return np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
        # a block at the START is followed by sweeps and preceded by silence; a block at the END is the reverse
        after = rms(t + (C["slots"][0]["t"] - s[0]) + 1.0, t + (C["slots"][0]["t"] - s[0]) + 6.0)
        before = rms(t - 8.0, t - 4.0)
        if before > 3 * max(after, 1e-6):
            return refine(r, k1) / FS - e[0], 1.0, "end sync block only (the recording started late, after the first chirps)"
        return refine(r, k1) / FS - s[0], 1.0, "start sync block only (the recording stopped before the end chirps)"
    b = (B - A) / (e[0] - s[0])
    if abs(b - 1) > 3e-4:
        raise SystemExit(f"start and end sync blocks disagree by {1e6 * (b - 1):.0f} ppm: wrong recording, or a stutter during playback")
    return A - b * s[0], b, f"start and end sync blocks (clock drift {1e6 * (b - 1):+.0f} ppm)"


def deconv(x, C, a, b, slot):
    """Complex impulse response of one slot, time 0 = the instant the sweep would arrive with zero extra delay."""
    N = 1 << 19; pre = 0.2
    t0 = a + b * slot["t"] - pre
    i0 = int(round(t0 * FS))
    if i0 < 0 or i0 + int((C["slot_s"] + 0.2) * FS) > len(x):
        return None
    y = np.zeros(N); seg = x[i0:i0 + int((C["slot_s"] + pre) * FS)]; y[:len(seg)] = seg
    s = sweep_sig(C); xs = np.zeros(N); j = int(pre * FS); xs[j:j + len(s)] = s
    Y, X = rfft(y), rfft(xs)
    f = np.fft.rfftfreq(N, 1 / FS); P = np.abs(X) ** 2
    band = (f >= 12) & (f <= 21000)
    H = np.where(band, Y * np.conj(X) / (P + 1e-5 * P[band].max()), 0)
    h = np.roll(irfft(H, N), int(pre * FS))          # index pre*FS = zero extra delay
    return h, pre


def bp_env(h, lo, hi):
    sos = butter(4, [lo, hi], btype="band", fs=FS, output="sos")
    return np.abs(hilbert(sosfiltfilt(sos, h)))


def arrival(h, pre, lo, hi, frac=0.5):
    """First arrival (ms relative to the nominal sweep start) from the band-limited envelope."""
    c = int(pre * FS); lo_i, hi_i = c - int(0.02 * FS), c + int(0.08 * FS)
    env = bp_env(h, lo, hi)[lo_i:hi_i]
    m = env.max(); k = int(np.argmax(env > frac * m))
    k0 = k
    if k > 0:                                    # interpolate the crossing
        e0, e1 = env[k - 1], env[k]; k0 = k - 1 + (frac * m - e0) / (e1 - e0 + 1e-30)
    return (lo_i + k0 - c) / FS * 1000.0, int(np.argmax(env)) + lo_i


def spectrum(h, pre, t_arr_ms, n=1 << 16):
    """Complex response from the IR gated at arrival-2 ms .. +300 ms (half-Hann on the tail), time reference kept."""
    c = int(pre * FS); k0 = c + int((t_arr_ms - 2.0) / 1000 * FS); k1 = k0 + int(0.3 * FS)
    g = h[k0:k1].copy()
    nt = int(0.1 * FS); g[-nt:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    nh = int(0.001 * FS); g[:nh] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nh) / nh)
    z = np.zeros(n); z[k0:k0 + len(g)] = g          # same time origin for every slot (a common pure delay of `pre` is harmless)
    return np.fft.rfftfreq(n, 1 / FS), rfft(z)


def group_delay_ms(f, H, lo, hi, pre):
    m = (f >= lo) & (f <= hi)
    ph = np.unwrap(np.angle(H[m])); w = 2 * np.pi * f[m]
    return -np.polyfit(w, ph, 1)[0] * 1000.0 - pre * 1000.0


def thirds(f, H, centres):
    p = np.abs(H) ** 2; out = []
    for fc in centres:
        m = (f >= fc * 2 ** (-1 / 6)) & (f < fc * 2 ** (1 / 6)); out.append(10 * np.log10(p[m].mean() + 1e-30) if m.any() else np.nan)
    return np.array(out)


def lr4(f, fc, hp):
    s = 1j * f / fc; b = s * s / (s * s + np.sqrt(2) * s + 1)           # 2nd-order Butterworth high-pass (squared = LR4)
    h = b * b
    return h if hp else (1 - 0) * (1 / (s * s + np.sqrt(2) * s + 1)) ** 2



# The planned height tone filters (same as HEIGHT_TONE in set-eq.py) and the trim that goes with them.
PLAN_TONE = [("pk", 310, -7.5, 1.0), ("pk", 1700, -7.0, 2.5), ("hs", 6000, 2.5, 0.8)]
PLAN_TRIM_DB = 1.0


def _pk(f, f0, g, q):
    A = 10 ** (g / 40); w0 = 2 * np.pi * f0 / FS; al = np.sin(w0) / (2 * q); c = np.cos(w0)
    b = (1 + al * A, -2 * c, 1 - al * A); a = (1 + al / A, -2 * c, 1 - al / A)
    z = np.exp(-1j * 2 * np.pi * f / FS); return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def _hs(f, f0, g, q):
    A = 10 ** (g / 40); w0 = 2 * np.pi * f0 / FS; al = np.sin(w0) / (2 * q); c = np.cos(w0); sq = 2 * np.sqrt(A) * al
    b = (A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq))
    a = ((A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq)
    z = np.exp(-1j * 2 * np.pi * f / FS); return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def plan_tone(f):
    t = np.ones(len(f), complex) * 10 ** (PLAN_TRIM_DB / 20)
    for k, fr, g, q in PLAN_TONE:
        t = t * (_pk(f, fr, g, q) if k == "pk" else _hs(f, fr, g, q))
    return t


def design(S, heights_on):
    f = S[0]["f"]; fg = 2 ** np.arange(np.log2(30), np.log2(14000), 1 / 12)

    def sm(Hc):
        p = np.abs(Hc) ** 2; out = []
        for fc in fg:
            mk = (f >= fc * 2 ** (-1 / 12)) & (f < fc * 2 ** (1 / 12)); out.append(10 * np.log10(p[mk].mean() + 1e-30))
        return np.array(out)
    bed = sm(np.sqrt(np.mean([np.abs(S[c]["H"]) ** 2 for c in (0, 1, 4, 6, 7)], 0)))
    hi = (fg >= 170) & (fg <= 12500); lf = (fg >= 40) & (fg <= 400)
    line("\nHEIGHTS vs THE BED (bed = power mean of FL FR FC SL SR, 1/6 octave)")
    H2, H3, H5 = S[2]["H"], S[3]["H"], S[5]["H"]
    if heights_on:
        cl, cr = sm(H2), sm(H3)
        line("  Recording WITH the bass management and tone filters. Deviation of each height from the bed:")
        for n, c in (("RL", cl), ("RR", cr)):
            line(f"  {n}: rms 170 Hz-12.5 kHz {np.sqrt(np.mean((c - bed)[hi] ** 2)):.1f} dB (before the filters it was 4.5 / 4.1),  40-400 Hz {np.sqrt(np.mean((c - bed)[lf] ** 2)):.1f} dB")
        line("  Hz      bed     RL-bed  RR-bed")
        for x in (40, 63, 100, 125, 160, 200, 250, 315, 500, 800, 1000, 1700, 3150, 5000, 8000, 10000):
            i = int(np.argmin(abs(fg - x))); line(f"  {x:<6} {bed[i]:7.1f} {cl[i] - bed[i]:+7.1f} {cr[i] - bed[i]:+7.1f}")
        mid = (fg >= 300) & (fg <= 4000)
        line(f"  mean level 300 Hz-4 kHz: RL {np.mean((cl - bed)[mid]):+.1f} dB, RR {np.mean((cr - bed)[mid]):+.1f} dB re the bed (0 = matched)")
        return
    hl, hr = sm(H2), sm(H3)
    line(f"  Now (no filters): rms deviation 170 Hz-12.5 kHz  RL {np.std((hl - bed)[hi]):.1f} dB, RR {np.std((hr - bed)[hi]):.1f} dB")
    T = plan_tone(f); Tg = sm(T)
    line(f"  With the planned filters (310 Hz -7.5, 1700 Hz -7.0, 6 kHz shelf +2.5, trim +{PLAN_TRIM_DB:g}):  RL {np.sqrt(np.mean((hl + Tg - bed)[hi] ** 2)):.1f}, RR {np.sqrt(np.mean((hr + Tg - bed)[hi] ** 2)):.1f} dB;"
         f" mean level 300-4k re bed: RL {np.mean((hl + Tg - bed)[(fg >= 300) & (fg <= 4000)]):+.1f}, RR {np.mean((hr + Tg - bed)[(fg >= 300) & (fg <= 4000)]):+.1f} dB")
    w = 2 * np.pi * f
    def lr4(fc, hp):
        s = 1j * np.maximum(f, 1e-3) / fc; b = 1 / (s * s + np.sqrt(2) * s + 1)
        return (s * s * b) ** 2 if hp else b ** 2
    res = []
    for fc in (100, 120, 140, 160, 180, 200):
        HP, LP = lr4(fc, True), lr4(fc, False)
        for pol in (1, -1):
            for d in np.arange(0, 12.01, 0.5):
                for g in (-6, -4, -2, 0, 2):
                    ga = 10 ** (g / 20) * pol * np.exp(-1j * w * d / 1000)
                    cl = sm(HP * T * H2 + LP * ga * H5); cr = sm(HP * T * H3 + LP * ga * H5)
                    e = np.sqrt(np.mean(np.concatenate([(cl - bed)[lf], (cr - bed)[lf]]) ** 2))
                    res.append((e, fc, pol, d, g))
    res.sort(); plain = [r for r in res if r[1] == 120 and r[2] == 1 and r[3] == 0 and r[4] == 0][0]
    line("  Bass: the heights' own response falls steeply below ~140 Hz, so what matters is how heights + sub (fed their bass) compare with the bed (rms dB, 40-400 Hz):")
    line(f"    heights alone                      {np.sqrt(np.mean((sm(lr4(120, True) * T * H2) - bed)[lf] ** 2)):.1f}")
    line(f"    plain (120 Hz, 0 dB, no delay)     {plain[0]:.1f}")
    for r in res[:3]:
        line(f"    best: {r[1]} Hz, gain {r[4]:+d} dB, delay {r[3]:.1f} ms, {'in phase' if r[2] == 1 else 'INVERTED'}      {r[0]:.1f}")
    e, fc, pol, d, g = res[0]
    flat = [r for r in res if r[0] <= e + 0.05]
    line(f"    ({len(flat)} settings are within 0.05 dB of the best: delay and polarity hardly matter when the two barely overlap; I prefer the simplest of them)")
    pick = sorted(flat, key=lambda r: (r[2] == -1, abs(r[3]), abs(r[4]), r[1]))[0]
    e, fc, pol, d, g = pick
    line(f"\n  Command:  ./set-eq.py --set=drone2 --align --heights --heights-tone --heights-hp={fc} --heights-lfe={g:+d}" + (f" --heights-delay={d:.1f}" if d > 0.05 else "") + (" --heights-invert" if pol == -1 else ""))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if not args:
        print(__doc__); sys.exit(1)
    cues_path = opt.get("cues", os.path.join(HERE, "sweeptest-cues.json"))
    C = json.load(open(cues_path)); names = C["names"]
    x = load_mono(args[0])
    heights_on = "--heights-on" in sys.argv
    pk = np.abs(x).max()
    line(f"recording {os.path.basename(args[0])}: {len(x) / FS:.0f} s, peak {20 * np.log10(pk + 1e-12):.1f} dBFS" + ("   TOO HOT (clipping risk): lower the mic gain" if pk > 0.89 else ""))
    a, b, how = placement(x, C)
    line(f"placed by {how}; the recording starts {-a:.2f} s into the file" if a < 0 else f"placed by {how}; the file starts {a:.2f} s into the recording")

    res = {}
    for i, sl in enumerate(C["slots"]):
        d = deconv(x, C, a, b, sl)
        if d is None:
            continue
        h, pre = d
        ch = sl["ch"]
        lo, hi = (40, 120) if ch == [5] else (300, 4000)
        t_arr, _ = arrival(h, pre, lo, hi)
        f, H = spectrum(h, pre, t_arr)
        res[i] = dict(ch=ch, h=h, pre=pre, t=t_arr, f=f, H=H)
    miss = [i for i in range(len(C["slots"])) if i not in res]
    single = {r["ch"][0]: r for r in res.values() if len(r["ch"]) == 1}
    if len(single) < 8 or any(i not in res for i in range(len(C["slots"]))):
        line("WARNING: the recording does not hold every sweep slot; sections that need the missing ones are skipped. Record the whole file (about 150 s, started 8 s before the playback).")
    if 0 not in single:
        raise SystemExit("FL sweep (the timing reference) is not in this recording")

    cent = np.array([25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000])
    L = {n: thirds(single[c]["f"], single[c]["H"], cent) for c, n in enumerate(names) if c in single}

    # noise floor: the silence after the end block
    tn = a + b * (C["sync"][-1] + 1.5)
    if tn * FS + 2 * FS < len(x):
        nz = x[int(tn * FS):int(tn * FS) + 2 * FS]; line(f"noise floor after the test: {20 * np.log10(np.sqrt(np.mean(nz ** 2)) + 1e-12):.1f} dBFS rms")

    line("\nPER SPEAKER: arrival and level")
    line("  arrival = when the sound reaches the mic, relative to the moment it was sent into CamillaDSP, minus the same for FL")
    ref = single[0]["t"] if 0 in single else 0.0
    line("  speaker   arrival (ms)   level 500-2k (dB, re FL)")
    mid = (cent >= 500) & (cent <= 2000)
    for c, n in enumerate(names):
        if c in single:
            lv = np.mean(L[n][mid]) - (np.mean(L["FL"][mid]) if "FL" in L else 0)
            line(f"  {n:<8} {single[c]['t'] - ref:+9.2f}       {lv:+6.1f}")
    if 5 in single:
        r5 = single[5]
        line(f"  LFE low-frequency delay (group delay 50-100 Hz, the sub's own filters included): {group_delay_ms(r5['f'], r5['H'], 50, 100, r5['pre']) - ref:+.1f} ms re FL")
        for n, c in (("RL", 2), ("RR", 3)):
            if c in single:
                line(f"  {n} delay at 150-400 Hz: {group_delay_ms(single[c]['f'], single[c]['H'], 150, 400, single[c]['pre']) - ref:+.1f} ms re FL")
    line("  (arrival = first rise of the 300-4000 Hz envelope; for LFE the 40-120 Hz envelope, which is only a rough guide. A well aligned")
    line("   set shows the five bed speakers near 0.)")

    line("\nTONE: each speaker alone, 1/3-octave level re the mean of the five bed speakers (FL FR FC SL SR), dB")
    bedn = [n for n in ("FL", "FR", "FC", "SL", "SR") if n in L]
    bed = np.mean([L[n] for n in bedn], 0)
    show = [n for n in names if n in L]
    line("      Hz " + "".join(f"{n:>7}" for n in show))
    for i, fc in enumerate(cent):
        line(f"{fc:8.4g} " + "".join(f"{L[n][i] - bed[i]:7.1f}" for n in show))
    for n in ("RL", "RR"):
        if n in L:
            m = (cent >= 400) & (cent <= 1000); ref_l = np.mean(L[n][m])
            ok = [fc for fc, v in zip(cent, L[n]) if fc < 400 and v >= ref_l - 10]
            line(f"  {n}: lowest 1/3-octave band within 10 dB of its own 400-1000 Hz level: {min(ok) if ok else 'none'} Hz")

    # checks
    line("\nCHECKS")
    for (i, j, what) in ((2, 11, "RL"), (5, 12, "LFE")):
        if i in res and j in res:
            m = (cent >= 40) & (cent <= 8000)
            A = thirds(res[i]["f"], res[i]["H"], cent); B = thirds(res[j]["f"], res[j]["H"], cent)
            d = (A - B)[m]; d = d[np.isfinite(d)]
            line(f"  repeatability {what}: rms difference {np.sqrt(np.mean(d ** 2)):.2f} dB, arrival {res[i]['t'] - res[j]['t']:+.3f} ms  (small = trustworthy)")
    for i, (p, q) in zip((8, 9, 10), ((2, 5), (3, 5), (2, 3))):
        if i in res and p in single and q in single:
            f = res[i]["f"]; Hs = single[p]["H"] + single[q]["H"]
            m = (f >= 40) & (f <= 300)
            e = 20 * np.log10(np.abs(res[i]["H"][m]) / (np.abs(Hs[m]) + 1e-30) + 1e-30)
            line(f"  pair {names[p]}+{names[q]} measured vs sum of singles, 40-300 Hz: median {np.median(e):+.2f} dB, 90% of bins within {np.percentile(np.abs(e), 90):.1f} dB")

    # design / verification of the heights processing against the bed
    if all(k in single for k in (0, 1, 2, 3, 4, 5, 6, 7)):
        design(single, heights_on)
    open(opt.get("out", "sweeptest-report.txt"), "w").write("\n".join(OUT) + "\n")
    print("\n(report saved to", opt.get("out", "sweeptest-report.txt") + ")")


if __name__ == "__main__":
    main()
