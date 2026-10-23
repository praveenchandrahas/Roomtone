#!/usr/bin/env python3
"""Reads a recording of bar-pantest-5.1 (5 single-speaker references, then pink noise panned round the ring in 30 degree steps).

  python3 -I analyze-bar-pan.py recording.wav [--cues=bar-pantest-cues.json] [--out=pan-report.txt]

For each position: the level at the mic, in octave bands and broadband (200 Hz-6 kHz), against
  (a) the mean of the five single-speaker references, and
  (b) what the five references predict for that pan (incoherent power sum of the two speakers' gains).
A smooth, even pan shows small (a) and (b). A hole between two speakers is cancellation or a bar that does not do pairwise panning;
a bump above (b) is the two speakers adding coherently. The same file played on the rig gives a like-for-like curve.
Works on the rig's recordings too (play the .flac there through CamillaDSP with the rig's 5.1 speakers wired as usual).
"""
import importlib.util, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(TOOLS, "dialoguetest"))
from dialoguetest_common import octave_filter                    # noqa: E402
sp = importlib.util.spec_from_file_location("sweepan", os.path.join(TOOLS, "sweeptest", "analyze-sweeptest.py"))
S = importlib.util.module_from_spec(sp); sp.loader.exec_module(S)
FS = 48000; BANDS = [250, 500, 1000, 2000, 4000]
OUT = []


def line(s=""):
    print(s); OUT.append(s)


def bp(x, lo, hi):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, [lo, hi], "band", fs=FS, output="sos"), x)


def level(seg):
    d = 10 * np.log10(np.mean(bp(seg, 200, 6000) ** 2) + 1e-30)
    b = [10 * np.log10(np.mean(octave_filter(seg, fc) ** 2) + 1e-30) for fc in BANDS]
    return d, np.array(b)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    if not args:
        print(__doc__); sys.exit(1)
    C = json.load(open(opt.get("cues", os.path.join(HERE, "bar-pantest-cues.json"))))
    x = S.load_mono(args[0]); pk = np.abs(x).max()
    line(f"recording {os.path.basename(args[0])}: {len(x) / FS:.0f} s, peak {20 * np.log10(pk + 1e-12):.1f} dBFS" + ("   TOO HOT: lower the mic gain" if pk > 0.89 else ""))
    a, b, how = S.placement(x, C); line(f"placed by {how}")
    names = {int(k): v for k, v in C["names"].items()}

    def meas(e):
        t0 = a + b * e["t"] + 0.4; seg = x[int(t0 * FS):int((t0 + 1.2) * FS)]
        return level(seg)
    refs = {e["ch"]: meas(e) for e in C["refs"]}
    rmean = np.mean([v[0] for v in refs.values()]); rb = np.mean([v[1] for v in refs.values()], 0)
    line("\n1. SINGLE-SPEAKER REFERENCES (level re the mean of the five)")
    line("   speaker   azimuth   broadband   " + "".join(f"{fc:>7}" for fc in BANDS))
    for e in C["refs"]:
        d, bb = refs[e["ch"]]
        line(f"   {e['name']:<8} {e['az']:>6}   {d - rmean:+8.1f}    " + "".join(f"{v - r:7.1f}" for v, r in zip(bb, rb)))
    line("   (a bar that applies its own speaker levels, wide channels or room correction will not show the file's levels here: that is the point)")

    line("\n2. PAN RING (broadband, dB re the mean reference; predicted = incoherent sum of the reference levels weighted by the pan gains)")
    line("   azimuth   measured   predicted   measured-predicted   [measured-predicted per octave band: " + " ".join(f"{fc}" for fc in BANDS) + "]")
    dev, dev_p, rows = [], [], []
    for e in C["ring"]:
        d, bb = meas(e); g = {int(k): v for k, v in e["gains"].items()}
        pw = sum((v ** 2) * 10 ** (refs[c][0] / 10) for c, v in g.items()); pwb = sum((v ** 2) * 10 ** (refs[c][1] / 10) for c, v in g.items())
        pred = 10 * np.log10(pw); predb = 10 * np.log10(pwb)
        dev.append(d - rmean); dev_p.append(d - pred); rows.append((e["az"], d - rmean, pred - rmean, d - pred, bb - predb))
        line(f"   {e['az']:>6}   {d - rmean:+8.1f}   {pred - rmean:+9.1f}   {d - pred:+12.1f}         " + " ".join(f"{v:+5.1f}" for v in (bb - predb)))
    line("\n   level vs azimuth (each # = 1 dB re the lowest position)")
    lo = min(dev)
    for az, m, _, _, _ in rows:
        line(f"   {az:>4}  " + "#" * int(round(m - lo + 1)) + f" {m:+.1f}")
    line(f"\n3. SUMMARY  evenness of the ring: rms {np.std(dev):.1f} dB, peak-to-peak {max(dev) - min(dev):.1f} dB;"
         f" after the references' own level differences are removed (measured-predicted): rms {np.std(dev_p):.1f} dB, worst {max(dev_p, key=abs):+.1f} dB")
    worst = sorted(rows, key=lambda r: -abs(r[3]))[:3]
    for az, m, p, d, bb in worst:
        line(f"   largest departure from the prediction: {az} deg, {d:+.1f} dB (broadband); band detail {' '.join(f'{v:+.1f}' for v in bb)}")
    line("   Under about 2 dB rms is smooth; holes of 4 dB or more between speakers are the sort of thing you hear as a source dipping in loudness as it moves.")
    open(opt.get("out", "pan-report.txt"), "w").write("\n".join(OUT) + "\n")
    print("\n(report saved to", opt.get("out", "pan-report.txt") + ")")


if __name__ == "__main__":
    main()
