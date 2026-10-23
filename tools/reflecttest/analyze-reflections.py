#!/usr/bin/env python3
"""Early-reflection analysis from a recording of the sweep test (tools/sweeptest/sweeptest-hw.flac).

  python3 -I analyze-reflections.py now.wav [--base=before.wav] [--geometry=room.json] [--plot=etc.png]
                                    [--cues=../sweeptest/sweeptest-cues.json] [--speakers=FL,FR,FC,RL,RR]

Uses the one-speaker-at-a-time sweeps already in the sweep test, so no new test file is needed. For each speaker it builds the
energy-time curve (ETC) of the impulse response at the mic in the 1-8 kHz band (the band ITU-R BS.1116 uses), normalised to the
direct sound, and reports:
  * every early reflection in the first 30 ms: delay, level re the direct sound, extra path length
  * the ITU-R BS.1116 check: all reflections in the first 15 ms at least 10 dB below the direct sound (1-8 kHz)
  * the summed early energy (1-15 ms) re the direct sound, in 1/3-octave bands from 250 Hz up (shows the frequency range the
    treatment actually works in)
With --base (a recording made with the panels removed or covered, mic untouched, same gain) it also reports, per speaker, the
delay and size of the change the treatment made: that is the direct answer to "is it working".
With --geometry (see room-example.json) it predicts the first-order reflection delay of every surface and names the surface
behind each measured peak, so you can see which reflection a panel is supposed to kill and whether it did.
"""
import importlib.util, json, os, sys
import numpy as np
from scipy.signal import butter, sosfiltfilt, hilbert, find_peaks

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "sweeptest")
spec = importlib.util.spec_from_file_location("sweepan", os.path.join(SW, "analyze-sweeptest.py"))
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
FS, C_AIR = S.FS, 343.0
OUT = []
NOISE_DB = 3.0   # measured from a same-state repeat (sweep1 vs sweep2 bed speakers: slices moved up to 3.3 dB), see README


def line(s=""):
    print(s); OUT.append(s)


def band(h, lo, hi, order=4):
    sos = butter(order, [lo, hi], btype="band", fs=FS, output="sos")
    return sosfiltfilt(sos, h)


def single_irs(path, C, want):
    """{name: (h, pre, t_direct_index)} for the wanted speakers (one-speaker slots only)."""
    x = S.load_mono(path)
    pk = np.abs(x).max()
    line(f"recording {os.path.basename(path)}: {len(x) / FS:.0f} s, peak {20 * np.log10(pk + 1e-12):.1f} dBFS"
         + ("   TOO HOT (clipping risk)" if pk > 0.89 else ""))
    a, b, how = S.placement(x, C)
    line(f"  placed by {how}")
    out = {}
    for sl in C["slots"]:
        if len(sl["ch"]) != 1 or C["names"][sl["ch"][0]] not in want:
            continue
        if sl["ch"][0] in (5,):
            continue
        d = S.deconv(x, C, a, b, sl)
        if d is None:
            continue
        h, pre = d
        n = C["names"][sl["ch"][0]]
        if n in out:
            continue
        out[n] = (h, pre)
    return out


def etc(h, pre, lo=1000.0, hi=8000.0, pre_ms=3.0, post_ms=40.0, smooth_ms=0.25):
    """Energy-time curve around the direct sound. Returns t (ms re the direct peak), E (dB re the direct peak), and the band IR window."""
    hb = band(h, lo, hi)
    c = int(pre * FS)
    nsm = int(smooth_ms / 1000 * FS) | 1                                  # energy smoothed over ~0.25 ms (about 2 cycles at 8 kHz)
    env_all = np.sqrt(np.convolve(hb ** 2, np.hanning(nsm) / np.hanning(nsm).sum(), mode="same"))
    w0, w1 = c - int(0.002 * FS), c + int(0.060 * FS)
    kd = w0 + int(np.argmax(env_all[w0:w1]))                      # direct sound = strongest early peak
    # first arrival could be slightly before the strongest peak (the driver rings): use the first >50% crossing as the time origin
    m = env_all[kd]; k = kd
    while k > w0 and env_all[k - 1] > 0.5 * m:
        k -= 1
    i0, i1 = k - int(pre_ms / 1000 * FS), k + int(post_ms / 1000 * FS)
    seg = hb[i0:i1]; env = env_all[i0:i1]
    t = (np.arange(len(seg)) - (k - i0)) / FS * 1000.0
    floor = 20 * np.log10(np.median(env_all[k + int(0.15 * FS):k + int(0.30 * FS)]) / m + 1e-12)
    return t, 20 * np.log10(env / m + 1e-9), seg, m, k, floor


def reflections(t, E, floor=-30.0, tmin=0.35, tmax=30.0, top=12):
    """Peaks of the smoothed ETC after the direct sound's own skirt: 4 dB prominence, 0.4 ms apart; the strongest `top`, in time order."""
    sel = (t >= tmin) & (t <= tmax)
    idx, pr = find_peaks(E, prominence=4.0, distance=int(0.0004 * FS))
    out = [(t[i], E[i], pr["prominences"][j]) for j, i in enumerate(idx) if sel[i] and E[i] >= floor]
    out = sorted(out, key=lambda r: -r[1])[:top]
    return sorted(out)


def early_ratio(seg, t, lo_ms, hi_ms, w_dir_ms=0.5):
    """Energy from lo_ms..hi_ms after the direct sound, relative to the direct sound's own energy (-0.5..+0.5 ms), dB."""
    d = (t >= -w_dir_ms) & (t <= w_dir_ms); e = (t >= lo_ms) & (t <= hi_ms)
    return 10 * np.log10(np.sum(seg[e] ** 2) / np.sum(seg[d] ** 2) + 1e-30)


def third_octave_early(h, pre, k, lo_ms=1.0, hi_ms=15.0, f_lo=250, f_hi=8000):
    """Early-energy / direct-energy per 1/3-octave band, from the broadband IR gated around the direct sound."""
    n = 1 << 16
    d0, d1 = k - int(0.0005 * FS), k + int(0.0005 * FS)
    e0, e1 = k + int(lo_ms / 1000 * FS), k + int(hi_ms / 1000 * FS)
    D = np.zeros(n); D[:d1 - d0] = h[d0:d1] * np.hanning(d1 - d0)
    R = np.zeros(n); R[:e1 - e0] = h[e0:e1]
    f = np.fft.rfftfreq(n, 1 / FS); pd = np.abs(np.fft.rfft(D)) ** 2; pr = np.abs(np.fft.rfft(R)) ** 2
    cs = 1000 * 2 ** (np.arange(-8, 10) / 3.0)
    cs = cs[(cs >= f_lo) & (cs <= f_hi)]
    res = []
    for fc in cs:
        m = (f >= fc * 2 ** (-1 / 6)) & (f < fc * 2 ** (1 / 6))
        res.append(10 * np.log10(pr[m].sum() / (pd[m].sum() + 1e-30) + 1e-30))
    return cs, np.array(res)


def predicted(geom, name):
    """First-order image-source delays (ms re the direct sound) for each surface; geometry in metres, origin at a floor corner."""
    L, W, H = geom["room"]["length"], geom["room"]["width"], geom["room"]["height"]
    sp = np.array(geom["speakers"][name], float); mic = np.array(geom["mic"], float)
    d0 = np.linalg.norm(sp - mic)
    out = []
    for nm, axis, val in (("left wall", 0, 0.0), ("right wall", 0, L), ("front wall", 1, 0.0), ("back wall", 1, W), ("floor", 2, 0.0), ("ceiling", 2, H)):
        im = sp.copy(); im[axis] = 2 * val - sp[axis]
        d = np.linalg.norm(im - mic)
        out.append((nm, (d - d0) / C_AIR * 1000.0, 20 * np.log10(d0 / d)))
    return d0, out


def verdict(worst):
    return "PASS (<= -15 dB)" if worst <= -15 else ("OK (-10 to -15 dB)" if worst <= -10 else "FAIL (> -10 dB)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if not args:
        print(__doc__); sys.exit(1)
    C = json.load(open(opt.get("cues", os.path.join(SW, "sweeptest-cues.json"))))
    want = opt.get("speakers", "FL,FR,FC,RL,RR").split(",")
    geom = json.load(open(opt["geometry"])) if "geometry" in opt else None
    now = single_irs(args[0], C, want)
    base = single_irs(opt["base"], C, want) if "base" in opt else None
    line("")
    line("Method: IR of each speaker alone at the mic, band 1-8 kHz, Hilbert envelope in dB re the direct sound (ETC).")
    line("Criterion: ITU-R BS.1116 asks every reflection in the first 15 ms to be 10 dB or more below the direct sound (1-8 kHz).")
    line("Reflections under ~0.35 ms cannot be separated from the speaker's own response and are not listed.")
    curves = {}
    for n in want:
        if n not in now:
            line(f"\n{n}: no sweep in the recording"); continue
        h, pre = now[n]
        t, E, seg, m, k, nfl = etc(h, pre)
        refl = reflections(t, E)
        curves[n] = (t, E)
        line(f"\n== {n} ==")
        d0 = None
        if geom:
            d0, pred = predicted(geom, n)
            line(f"  direct path {d0:.2f} m (from the geometry file)")
        in15 = [r for r in refl if 1.0 <= r[0] <= 15.0]
        worst = max([r[1] for r in in15], default=-99.0)
        line(f"  strongest reflections in the first 30 ms (peaks above -30 dB; the ETC noise floor here is {nfl:.0f} dB):")
        if not refl:
            line("    none above -30 dB")
        for (tt, ee, pr) in refl:
            tag = ""
            if geom:
                best = min(pred, key=lambda p: abs(p[1] - tt))
                if abs(best[1] - tt) <= 0.4:
                    tag = f"  <- {best[0]} (predicted {best[1]:.2f} ms)"
            own = "  (under 1 ms: mostly the speaker's own response, not counted)" if tt < 1.0 else ""
            line(f"    {tt:6.2f} ms  {ee:6.1f} dB   extra path {tt / 1000 * C_AIR:5.2f} m{tag}{own}")
        line(f"  BS.1116 check (worst reflection 1-15 ms): {worst:.1f} dB -> {verdict(worst)}")
        line(f"  summed early energy 1-15 ms re direct (1-8 kHz): {early_ratio(seg, t, 1.0, 15.0):+.1f} dB")
        if geom:
            line("  predicted first-order surface delays: " + ", ".join(f"{p[0]} {p[1]:.2f} ms" for p in sorted(pred, key=lambda p: p[1])))
        cs, r = third_octave_early(h, pre, k, f_lo=1000)
        line("  early energy (1-15 ms) re direct per 1/3-octave band, dB (1 kHz up; a 1 ms gate cannot judge lower bands):")
        line("    " + "  ".join(f"{c:.0f}:{v:+.0f}" for c, v in zip(cs[::2], r[::2])))
        if base and n in base:
            hb_, preb = base[n]
            tb, Eb, segb, mb, kb, _ = etc(hb_, preb)
            edges = [1.0, 3.0, 5.0, 8.0, 11.0, 15.0]
            def binned(seg_, t_):
                d = (t_ >= -0.5) & (t_ <= 0.5); ed = np.sum(seg_[d] ** 2)
                return np.array([10 * np.log10(np.sum(seg_[(t_ >= e0) & (t_ < e1)] ** 2) / ed + 1e-30) for e0, e1 in zip(edges[:-1], edges[1:])])
            bn, bb = binned(seg, t), binned(segb, tb)
            ch = bn - bb
            line("    change in energy per time slice after the direct sound, dB (negative = the treatment lowered it):")
            line("      " + "  ".join(f"{e0:g}-{e1:g}ms:{c:+.1f}" for e0, e1, c in zip(edges[:-1], edges[1:], ch)))
            j = int(np.argmin(ch))
            line(f"    biggest drop: {ch[j]:+.1f} dB in {edges[j]:g}-{edges[j + 1]:g} ms.  Repeat-run noise of this comparison was measured at about +-{NOISE_DB:g} dB: smaller changes are not real.")
            sm_b = early_ratio(segb, tb, 1.0, 15.0)
            line(f"    summed early energy 1-15 ms: base {sm_b:+.1f} dB -> now {early_ratio(seg, t, 1.0, 15.0):+.1f} dB   (change {early_ratio(seg, t, 1.0, 15.0) - sm_b:+.1f} dB)")
            csn, rn = third_octave_early(h, pre, k, f_lo=250)
            csb, rb = third_octave_early(hb_, preb, kb, f_lo=250)
            line("    change in early energy per 1/3-octave band, dB (same gate both times, so valid from 250 Hz):")
            line("      " + "  ".join(f"{c:.0f}:{v - w:+.0f}" for c, v, w in zip(csn[::2], rn[::2], rb[::2])))
    if "plot" in opt:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, axs = plt.subplots(len(curves), 1, figsize=(9, 2.2 * len(curves)), sharex=True)
        axs = np.atleast_1d(axs)
        for ax, (n, (t, E)) in zip(axs, curves.items()):
            ax.plot(t, E, lw=1, label="now")
            if base and n in base:
                tb, Eb = etc(*base[n])[:2]; ax.plot(tb, Eb, lw=1, alpha=.7, label="base")
            ax.axhline(-10, color="r", ls="--", lw=.8); ax.axvline(15, color="gray", ls=":", lw=.8)
            ax.set_xlim(-2, 30); ax.set_ylim(-60, 3); ax.set_ylabel(f"{n} dB"); ax.legend(loc="upper right", fontsize=7)
        axs[-1].set_xlabel("ms after the direct sound (red = BS.1116 limit, dotted = 15 ms)")
        fig.tight_layout(); fig.savefig(opt["plot"], dpi=110); line(f"\nplot: {opt['plot']}")
    if "out" in opt:
        open(opt["out"], "w").write("\n".join(OUT) + "\n")


if __name__ == "__main__":
    main()
