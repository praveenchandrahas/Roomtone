#!/usr/bin/env python3
"""Analyse a recording of delaytest.wav (made by scripts/make-delaytest.py) and report, per speaker:
arrival delay relative to FL, extra path length, polarity of the first lobe and level.

How it works: each speaker plays a 30 ms chirp in its own 0.25 s slot, 12 cycles. A matched filter finds every
chirp. Because the playback interface and the microphone interface have separate clocks, arrival times drift
linearly; a least-squares model  t = a + s*slot + d_channel  separates the drift (s) from the per-speaker
delay (d). Residual scatter (+- ms) tells you how far to trust each number.

Caveats: polarity from one lobe is unreliable when delays are off by about 1 ms (see docs/alignment.md);
use the pair test in make-bedtest.py (T6) for polarity. Level here is the matched-filter peak, a rough guide only.

Usage: python3 analyze-delaytest.py recording.wav        (mono or stereo wav, 16 or 24 bit; uses channel 1)
Needs numpy and scipy.
"""
import importlib.util
import os
import sys
import wave

import numpy as np
from scipy.signal import fftconvolve, hilbert

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mk", os.path.join(HERE, "..", "scripts", "make-delaytest.py"))
MK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MK)
FS, NCH, SLOT, CYC = MK.FS, MK.NCH, MK.SLOT, MK.CYCLES
NAMES = ["FL", "FR", "RL(height)", "RR(height)", "FC", "LFE", "SL", "SR"]


def load_mono(path):
    w = wave.open(path)
    raw, sw, nc = w.readframes(w.getnframes()), w.getsampwidth(), w.getnchannels()
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
        x = np.where(x >= 1 << 23, x - (1 << 24), x) / 2**23
    elif sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) / 32768.0
    else:
        raise SystemExit("unsupported sample width (use 16 or 24 bit)")
    return x.reshape(-1, nc)[:, 0].astype(np.float64)


def analyse(x):
    tpl = np.array(MK.chirp())
    m = fftconvolve(x, tpl[::-1], mode="full")[len(tpl) - 1:]
    env = np.abs(hilbert(m))
    thr, hop = 0.08 * env.max(), int(0.1 * FS)
    ev, idx = [], np.where(env > thr)[0]
    while len(idx):
        j = idx[0]
        k = j + int(np.argmax(env[j:j + hop]))
        ev.append(k)
        idx = idx[idx >= k + hop]
    T, A, P = [], [], []
    for k in ev:                                    # sub-sample refinement (parabola on the envelope)
        y0, y1, y2 = env[k - 1], env[k], env[k + 1]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        T.append((k + off) / FS)
        A.append(y1)
        kk = k + int(round(off))
        P.append(np.sign(m[kk - 1:kk + 2][np.argmax(np.abs(m[kk - 1:kk + 2]))]))
    T, A, P = np.array(T), np.array(A), np.array(P)
    pattern = [0, 1, 2, 3, 4, 6, 7]                 # slot 5 is the silent LFE slot

    def block_ok(i):
        return all(np.any(np.abs(T - (T[i] + s * SLOT)) < 0.015) for s in pattern)

    start = next((i for i in range(len(T)) if block_ok(i)), None)
    if start is None:
        raise SystemExit("could not find the chirp block: is the recording complete and the level sensible?")
    t0 = T[start]
    rows = []
    for e in range(len(T)):
        slot = round((T[e] - t0) / SLOT)
        if slot < 0 or slot >= CYC * NCH or abs(T[e] - (t0 + slot * SLOT)) > 0.02 + 1e-4 * slot:
            continue
        rows.append((slot, slot % NCH, slot // NCH, T[e], A[e], P[e]))
    rows = np.array(rows)
    chs = [c for c in range(NCH) if c != MK.LFE]
    cols = [np.ones(len(rows)), rows[:, 0]] + [(rows[:, 1] == c).astype(float) for c in chs if c != 0]
    M = np.stack(cols, 1)
    sol = np.linalg.lstsq(M, rows[:, 3], rcond=None)[0]
    d = {0: 0.0}
    for i, c in enumerate([c for c in chs if c != 0]):
        d[c] = sol[2 + i]
    resid = rows[:, 3] - M @ sol
    out = {}
    for c in chs:
        sel = rows[:, 1] == c
        out[c] = dict(delay_ms=d[c] * 1000, sd_ms=np.std(resid[sel]) * 1000, n=int(sel.sum()),
                      pol=int(np.sign(np.sum(rows[sel, 5]))), level_db=20 * np.log10(np.median(rows[sel, 4])))
    return out, (sol[1] / SLOT - 1) * 1e6


def report(out, drift):
    ref = out[0]
    print(f"clock drift between playback and capture: {drift:+.0f} ppm (removed)")
    print(f"{'channel':<12}{'delay vs FL (ms)':>18}{'+- ms':>8}{'extra path (cm)':>17}{'polarity':>10}{'level vs FL (dB)':>18}")
    for c, v in out.items():
        print(f"{NAMES[c]:<12}{v['delay_ms']:18.3f}{v['sd_ms']:8.3f}{v['delay_ms'] * 34.3:17.1f}"
              f"{('+' if v['pol'] > 0 else '-'):>10}{v['level_db'] - ref['level_db']:18.2f}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    report(*analyse(load_mono(sys.argv[1])))
