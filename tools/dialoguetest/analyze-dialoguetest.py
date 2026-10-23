#!/usr/bin/env python3
"""Analyses a recording of dialoguetest-hw.flac played through CamillaDSP, mic at the seat.

  python3 -I analyze-dialoguetest.py recording.wav [--cues=dialoguetest-cues.json] [--cal-dba=62] [--out=report.txt]

--cal-dba is optional: the dB(A) reading of the pink-noise segment (FC, at -27 dBFS, 12 s) from a phone SPL app or meter held at
the seat. With it the report gives the noise floor and the dialogue level in dB(A); without it all levels are relative.

Reports (the centre speaker "FC" is the one that carries dialogue):
  1. FC speech-band response (200 Hz-5 kHz): flatness, boom (100-200 Hz) and presence (2-4 kHz) against the 500-1000 Hz level,
     and FC against the mean of FL and FR
  2. real centre against phantom centre (FL+FR): level, and comb-filter ripple (how much the 1/12-octave detail departs from the trend)
  3. clarity from the impulse response: C50, RT60, and an STI computed two ways (from the impulse response with the measured noise,
     and directly from the speech-modulated noise as recorded). The two should agree within about 0.03
  4. background noise and the dialogue-to-noise ratio per octave band
  5. a short scorecard with the rules of thumb used
The STI here is "STIPA-style": the modulation method of IEC 60268-16 with an approximate speech spectrum and no auditory-masking
correction, so use it to compare setups and to judge good / poor, not as a certified figure.
"""
import importlib.util, json, os, sys
import numpy as np
from scipy.signal import butter, sosfiltfilt, hilbert, find_peaks
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dialoguetest_common import *

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sweepan", os.path.join(HERE, "..", "sweeptest", "analyze-sweeptest.py"))
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
OUT = []
MODS14 = [0.63, 0.8, 1.0, 1.25, 1.6, 2.0, 2.5, 3.15, 4.0, 5.0, 6.3, 8.0, 10.0, 12.5]


def line(s=""):
    print(s); OUT.append(s)


def a_weight(f):
    f2 = np.maximum(f, 1e-3) ** 2
    ra = (12194.0 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194.0 ** 2))
    return 20 * np.log10(ra + 1e-20) + 2.0


def dba(x):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / FS)
    p = (np.abs(X) ** 2) * 10 ** (a_weight(f) / 10)
    return 10 * np.log10(p.sum() / (len(x) ** 2) * 2 + 1e-30)


def band_levels(x):
    return np.array([10 * np.log10(np.mean(octave_filter(x, fc) ** 2) + 1e-30) for fc in OCT])


def mod_depth(e, F):
    t = np.arange(len(e)) / FS
    return 2 * np.abs(np.sum(e * np.exp(-2j * np.pi * F * t))) / np.sum(e)


def sti_from_m(m):
    """m[band][F-index] modulation transfer values (0..1) -> STI by IEC 60268-16 (male weights)."""
    ti = []
    for mb in m:
        mb = np.clip(np.asarray(mb), 1e-4, 0.9999)
        snr = np.clip(10 * np.log10(mb / (1 - mb)), -15, 15)
        ti.append(np.mean((snr + 15) / 30))
    mti = np.array(ti)
    s = np.sum(np.array(ALPHA) * mti) - np.sum(np.array(BETA) * np.sqrt(mti[:-1] * mti[1:]))
    return float(np.clip(s, 0, 1)), mti


def seg(x, a, b, t0, dur, trim=0.0):
    i = int(round((a + b * (t0 + trim)) * FS)); n = int((dur - 2 * trim) * FS)
    if i < 0 or i + n > len(x):
        return None
    return x[i:i + n]


def stipa_direct(rec, ref):
    """STI straight from the recorded modulated noise: per octave band, the modulation depth of the recording over that of the source."""
    m = []
    for fc in OCT:
        er, es = octave_filter(rec, fc) ** 2, octave_filter(ref, fc) ** 2
        m.append([min(mod_depth(er, F) / mod_depth(es, F), 0.9999) for F in MODF[fc]])
    return sti_from_m(m)


def ir_octave(h, k, fc):
    """Squared octave-band IR from the direct sound on, noise-compensated using the IR tail."""
    hb = octave_filter(h, fc)
    tail = hb[k + int(1.2 * FS):k + int(1.9 * FS)]
    sigma = np.mean(tail ** 2) if len(tail) else 0.0
    e = hb[k - int(0.002 * FS):k + int(1.0 * FS)] ** 2
    e = np.maximum(e - sigma, 0)
    return e


def sti_from_ir(h, k, snr_db):
    """IR-based STI: m(F) = |FT(h^2)(F)| / sum(h^2) / (1 + 10^(-SNR/10)); snr_db per octave (None = noise-free)."""
    m = []
    for bi, fc in enumerate(OCT):
        e = ir_octave(h, k, fc)
        # cut the IR where its decay reaches the noise: keep up to 0.6 s
        e = e[:int(0.6 * FS)]
        t = np.arange(len(e)) / FS
        den = np.sum(e) + 1e-30
        nterm = 1.0 if snr_db is None else 1.0 / (1 + 10 ** (-snr_db[bi] / 10))
        m.append([min(np.abs(np.sum(e * np.exp(-2j * np.pi * F * t))) / den * nterm, 0.9999) for F in MODS14])
    return sti_from_m(m)


def schroeder_t(e, lo_db, hi_db):
    c = np.cumsum(e[::-1])[::-1]; c = 10 * np.log10(c / c[0] + 1e-30)
    i0 = int(np.argmax(c <= lo_db)); i1 = int(np.argmax(c <= hi_db))
    if i1 <= i0:
        return np.nan
    return (i1 - i0) / FS * 60.0 / abs(hi_db - lo_db)


def c50_rt(h, k):
    c50, rt = {}, {}
    for fc in (500, 1000, 2000, 4000):
        e = ir_octave(h, k, fc)
        i0 = int(0.002 * FS)                      # direct sound sits 2 ms into this array
        n50 = i0 + int(0.05 * FS)
        c50[fc] = 10 * np.log10(np.sum(e[:n50]) / (np.sum(e[n50:int(0.8 * FS)]) + 1e-30) + 1e-30)
        rt[fc] = schroeder_t(e[i0:int(0.8 * FS)], -5.0, -25.0)
    return c50, rt


def thirds_db(f, H, centres):
    return S.thirds(f, H, centres)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if not args:
        print(__doc__); sys.exit(1)
    C = json.load(open(opt.get("cues", os.path.join(HERE, "dialoguetest-cues.json"))))
    x = S.load_mono(args[0])
    pk = np.abs(x).max()
    line(f"recording {os.path.basename(args[0])}: {len(x) / FS:.0f} s, peak {20 * np.log10(pk + 1e-12):.1f} dBFS" + ("   TOO HOT: lower the mic gain" if pk > 0.89 else ""))
    a, b, how = S.placement(x, C)
    line(f"placed by {how}")

    # impulse responses of the sweeps
    irs = []
    for sl in C["slots"]:
        d = S.deconv(x, C, a, b, sl)
        if d is None:
            continue
        h, pre = d
        ch = sl["ch"]
        t_arr, kd = S.arrival(h, pre, 300, 4000)
        f, H = S.spectrum(h, pre, t_arr)
        irs.append(dict(ch=ch, h=h, pre=pre, t=t_arr, f=f, H=H, k=int(pre * FS + t_arr / 1000 * FS)))
    def pick(ch, n=0):
        r = [i for i in irs if i["ch"] == ch]
        return r[n] if len(r) > n else None
    fc1, fc2, fl, fr, ph = pick([4]), pick([4], 1), pick([0]), pick([1]), pick([0, 1])
    if fc1 is None:
        raise SystemExit("the centre sweep is not in this recording")

    cent = np.array([100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000])
    Lc = thirds_db(fc1["f"], fc1["H"], cent)
    ref = (cent >= 500) & (cent <= 1000)
    LcN = Lc - np.mean(Lc[ref])

    line("\n1. CENTRE (FC) SPEECH-BAND RESPONSE, 1/3 octave, relative to its own 500-1000 Hz mean (and to the mean of FL and FR)")
    line("      Hz    FC     FC-FLFR")
    LB = None
    if fl is not None and fr is not None:
        LB = 10 * np.log10((10 ** (thirds_db(fl["f"], fl["H"], cent) / 10) + 10 ** (thirds_db(fr["f"], fr["H"], cent) / 10)) / 2)
        LB = LB - np.mean(LB[ref])
    for i, fcx in enumerate(cent):
        line(f"  {fcx:6.0f} {LcN[i]:+6.1f} " + (f"{LcN[i] - LB[i]:+7.1f}" if LB is not None else ""))
    sp = (cent >= 200) & (cent <= 5000)
    lf = np.log2(cent[sp]); fit = np.polyval(np.polyfit(lf, LcN[sp], 1), lf)
    flat = np.sqrt(np.mean((LcN[sp] - fit) ** 2))
    boom = np.mean(LcN[(cent >= 100) & (cent <= 200)])
    pres = np.mean(LcN[(cent >= 2000) & (cent <= 4000)])
    line(f"  flatness 200 Hz-5 kHz (rms about its own trend): {flat:.1f} dB")
    line(f"  boom: 100-200 Hz is {boom:+.1f} dB re 500-1000 Hz     presence: 2-4 kHz is {pres:+.1f} dB re 500-1000 Hz")
    if fc2 is not None:
        m = (cent >= 200) & (cent <= 8000)
        L2 = thirds_db(fc2["f"], fc2["H"], cent)
        d = (Lc - L2)[m]; d = d[np.isfinite(d)]
        line(f"  repeatability (two FC sweeps): rms {np.sqrt(np.mean(d ** 2)):.2f} dB, arrival {fc1['t'] - fc2['t']:+.3f} ms")
    if LB is not None:
        mid = (cent >= 500) & (cent <= 2000)
        Lf = thirds_db(fl["f"], fl["H"], cent); Lr = thirds_db(fr["f"], fr["H"], cent)
        line(f"  FC level re the mean of FL and FR, 500 Hz-2 kHz: {np.mean(Lc[mid]) - np.mean(10 * np.log10((10 ** (Lf[mid] / 10) + 10 ** (Lr[mid] / 10)) / 2)):+.1f} dB")
        line(f"  arrival at the mic re FL: FC {fc1['t'] - fl['t']:+.2f} ms, FR {fr['t'] - fl['t']:+.2f} ms")

    line("\n2. REAL CENTRE vs PHANTOM CENTRE (FL+FR played together)")
    if ph is not None:
        Lp = thirds_db(ph["f"], ph["H"], cent)
        dpc = (Lp - Lc)
        line("  phantom minus real centre, 1/3 octave (an in-phase pair at the seat is ~+6 dB; the shape matters, not the offset):")
        line("  " + "  ".join(f"{c:.0f}:{v - np.nanmedian(dpc[(cent >= 500) & (cent <= 2000)]):+.0f}" for c, v in zip(cent[::2], dpc[::2])))
        def ripple(r):
            f = r["f"]; fg = 2 ** np.arange(np.log2(500), np.log2(5000), 1 / 12)
            p = np.abs(r["H"]) ** 2
            fine = np.array([10 * np.log10(p[(f >= g * 2 ** (-1 / 24)) & (f < g * 2 ** (1 / 24))].mean() + 1e-30) for g in fg])
            coarse = np.array([10 * np.log10(p[(f >= g * 2 ** (-1 / 6)) & (f < g * 2 ** (1 / 6))].mean() + 1e-30) for g in fg])
            return np.sqrt(np.mean((fine - coarse) ** 2)), np.min(fine - coarse)
        rr, nr = ripple(fc1); rp, npp = ripple(ph)
        line(f"  comb ripple 500 Hz-5 kHz (1/12-octave detail about the 1/3-octave trend): real centre {rr:.1f} dB rms, deepest notch {nr:.1f} dB;"
             f"  phantom {rp:.1f} dB rms, deepest notch {npp:.1f} dB")
        if rp > rr + 0.5:
            line("  -> the phantom centre has more comb-filter colouring than the real centre: this is what the real centre speaker buys.")
        else:
            line("  -> the phantom is not noticeably more combed than the real centre at this seat.")
    else:
        line("  (the FL+FR sweep is not in this recording)")

    line("\n3. CLARITY FROM THE IMPULSE RESPONSE OF FC")
    c50, rt = c50_rt(fc1["h"], fc1["k"])
    line("  octave band:   " + "".join(f"{fc:>8}" for fc in (500, 1000, 2000, 4000)))
    line("  C50 (dB):      " + "".join(f"{c50[fc]:8.1f}" for fc in (500, 1000, 2000, 4000)))
    line("  RT60 (s, T20): " + "".join(f"{rt[fc]:8.2f}" for fc in (500, 1000, 2000, 4000)))
    c50s = np.mean([c50[500], c50[1000], c50[2000]])
    line(f"  speech C50 (mean of 500 Hz, 1 kHz, 2 kHz): {c50s:+.1f} dB")

    # noise and levels
    nzs = seg(x, a, b, C["noise"]["t"], C["noise"]["dur"], 1.0)
    st_fc = seg(x, a, b, C["stipa"][0]["t"], C["stipa"][0]["dur"], 1.5)
    st_ph = seg(x, a, b, C["stipa"][1]["t"], C["stipa"][1]["dur"], 1.5)
    pink = seg(x, a, b, C["pink"]["t"], C["pink"]["dur"], 0.8)
    line("\n4. BACKGROUND NOISE AND DIALOGUE-TO-NOISE")
    if nzs is None or st_fc is None:
        line("  (the noise-floor or speech segment is missing from the recording)")
        snr = None
    else:
        Ln, Ls = band_levels(nzs), band_levels(st_fc)
        snr = Ls - Ln
        line("  octave band (Hz):       " + "".join(f"{fc:>7}" for fc in OCT))
        line("  noise floor (dBFS):     " + "".join(f"{v:7.1f}" for v in Ln))
        line("  dialogue at seat (dBFS):" + "".join(f"{v:7.1f}" for v in Ls))
        line("  dialogue - noise (dB):  " + "".join(f"{v:7.1f}" for v in snr))
        wsnr = float(np.sum(np.array(ALPHA) * np.clip(snr, -15, 30)) / np.sum(ALPHA))
        line(f"  STI-weighted mean dialogue-to-noise: {wsnr:.1f} dB   (above ~15 dB in every band the noise no longer limits intelligibility)")
        if "cal-dba" in opt and pink is not None:
            cal = float(opt["cal-dba"]); ref_a = dba(pink)
            line(f"  with the pink-noise reading of {cal:g} dB(A): dialogue about {cal + dba(st_fc) - ref_a:.0f} dB(A), background noise about {cal + dba(nzs) - ref_a:.0f} dB(A)")
        else:
            line("  (add --cal-dba=<reading> from the pink-noise segment to get these in dB(A))")

    line("\n   STI (STIPA-style, no auditory masking correction)")
    sti_n = sti_ir = sti_dir = sti_ph = None
    if snr is not None:
        sti_ir, mti = sti_from_ir(fc1["h"], fc1["k"], snr)
        sti_free, _ = sti_from_ir(fc1["h"], fc1["k"], None)
        line(f"  from the FC impulse response, noise-free (room + speaker only):  {sti_free:.2f}")
        line(f"  from the FC impulse response, with the measured noise:          {sti_ir:.2f}")
        sti_dir, mtid = stipa_direct(st_fc, stipa_signal(C["stipa"][0]["seed"], C["stipa"][0]["dur"])[int(1.5 * FS):-int(1.5 * FS)])
        line(f"  direct, from the recorded speech-modulated noise on FC:         {sti_dir:.2f}")
        sti_ph, _ = stipa_direct(st_ph, stipa_signal(C["stipa"][1]["seed"], C["stipa"][1]["dur"])[int(1.5 * FS):-int(1.5 * FS)])
        line(f"  direct, same signal on FL+FR (phantom centre):                  {sti_ph:.2f}")
        line("  per-octave modulation index, IR method (125..8k): " + " ".join(f"{v:.2f}" for v in mti))
        if abs(sti_ir - sti_dir) > 0.06:
            line("  NOTE: the two FC figures differ by more than 0.06: check for clipping, a moved mic, or a noise burst during the recording.")
        sti_n = sti_dir

    line("\n5. SCORECARD (rules of thumb, not standards, except where named)")
    def flag(v, good, ok, hi_good=True):
        if hi_good:
            return "GOOD" if v >= good else ("OK" if v >= ok else "CHECK")
        return "GOOD" if v <= good else ("OK" if v <= ok else "CHECK")
    rows = [("FC flatness 200 Hz-5 kHz (rms about trend)", f"{flat:.1f} dB", flag(flat, 2.0, 3.5, False)),
            ("boom, 100-200 Hz re 500-1000 Hz", f"{boom:+.1f} dB", flag(abs(boom), 3.0, 6.0, False)),
            ("presence, 2-4 kHz re 500-1000 Hz", f"{pres:+.1f} dB", flag(abs(pres), 3.0, 6.0, False)),
            ("speech C50", f"{c50s:+.1f} dB", flag(c50s, 2.0, 0.0))]
    if sti_dir is not None:
        rows += [("STI of FC, direct", f"{sti_dir:.2f}", flag(sti_dir, 0.75, 0.60)),
                 ("dialogue-to-noise", f"{wsnr:.0f} dB", flag(wsnr, 20, 12))]
    if ph is not None:
        rows.append(("phantom comb ripple minus real centre", f"{rp - rr:+.1f} dB", "info"))
    for n, v, f in rows:
        line(f"  {n:<44}{v:>10}   {f}")
    line("  STI bands (IEC 60268-16): 0.75+ excellent, 0.60-0.75 good, 0.45-0.60 fair, below poor.")
    line("  Single seat, single mic position: differences under ~2 dB per band or 0.03 STI are within repeatability.")
    if "out" in opt:
        open(opt["out"], "w").write("\n".join(OUT) + "\n")


if __name__ == "__main__":
    main()
