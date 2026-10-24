"""Tests for tools/drifttest/drift.py against simulated recordings with known drift. Run: python3 tests/test_drift.py   (needs numpy, scipy)"""
import os
import sys

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "drifttest"))
import drift as D

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), name, detail)


FS = 12000
META = dict(fs=FS, period=6.0, count=40, gap=1.5, f0=30.0, f1=120.0, dur=1.0, lead=2.0)


def room_ir(seed, fs=FS):
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.6 * fs)) / fs
    ir = np.zeros(len(t))
    ir[0] = 1.0
    for f in (38, 56, 67, 84, 103):
        ir += 0.35 * rng.uniform(0.5, 1) * np.exp(-t / 0.25) * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    return ir


def simulate(drift_ppm=0.0, mic_ppm=0.0, step_ms=0.0, step_at=None, snr_db=20.0, a_delay=0.011, b_delay=0.040, seed=1):
    """Microphone recording of path A and path B. B's delay grows by drift_ppm, jumps by step_ms at period step_at.
    The microphone clock runs mic_ppm fast, so its samples are spaced 1/(fs*(1+mic_ppm)) of true time apart."""
    m = META
    r = 1 + mic_ppm * 1e-6
    n = int((m["lead"] + m["count"] * m["period"] + 6) * FS)
    t_true = np.arange(n) / FS / r
    a = np.zeros(n)
    b = np.zeros(n)
    for k in range(m["count"]):
        t0 = m["lead"] + k * m["period"]
        da = a_delay
        db = b_delay + drift_ppm * 1e-6 * t0 + (step_ms * 1e-3 if step_at is not None and k >= step_at else 0.0)
        lo, hi = int((t0 - 0.2) * FS), int((t0 + m["gap"] + m["dur"] + 0.6) * FS)
        a[lo:hi] += D.chirp_at(t_true[lo:hi], t0 + da, m["f0"], m["f1"], m["dur"])
        b[lo:hi] += D.chirp_at(t_true[lo:hi], t0 + m["gap"] + db, m["f0"], m["f1"], m["dur"])
    x = signal.fftconvolve(a, room_ir(11))[:n] + signal.fftconvolve(b, room_ir(12))[:n]
    rng = np.random.default_rng(seed)
    return x + rng.standard_normal(n) * np.std(x) * 10 ** (-snr_db / 20)


r0 = D.analyze(simulate(), FS, META)
check("locked paths: drift reads near zero", r0["ok"] and abs(r0["drift_ppm"]) < 5, f"({r0['drift_ppm']:+.2f} ppm, jitter {r0['jitter_ms']:.2f} ms)")
check("locked paths: no steps, verdict says LOCKED", not r0["steps"] and D.verdict(r0).splitlines()[-1].startswith("LOCKED"))

r1 = D.analyze(simulate(drift_ppm=60.0), FS, META)
check("60 ppm drift recovered", r1["ok"] and abs(r1["drift_ppm"] - 60) < 6, f"({r1['drift_ppm']:+.1f} ppm)")
check("60 ppm drift is called NOT LOCKED", D.verdict(r1).splitlines()[-1].startswith("NOT LOCKED"))
check("projection to a 3 h film is about +650 ms", abs(r1["per_3h_ms"] - 648) < 80, f"({r1['per_3h_ms']:+.0f} ms)")

r2 = D.analyze(simulate(drift_ppm=-25.0, mic_ppm=45.0), FS, META)
check("negative drift with a fast microphone clock still recovered", r2["ok"] and abs(r2["drift_ppm"] + 25) < 6 and abs(r2["mic_ppm"] - 45) < 8,
      f"({r2['drift_ppm']:+.1f} ppm, mic {r2['mic_ppm']:+.0f} ppm)")

r3 = D.analyze(simulate(step_ms=12.0, step_at=20), FS, META)
check("a 12 ms re-sync step is detected near the right period", r3["ok"] and len(r3["steps"]) >= 1 and abs(r3["steps"][0][0] - 20) <= 3
      and abs(r3["steps"][0][1] - 12) < 2, f"({r3['steps']})")

r4 = D.analyze(simulate(snr_db=0.0, drift_ppm=40.0), FS, META)
check("noisy room (0 dB SNR) still finds the drift", r4["ok"] and abs(r4["drift_ppm"] - 40) < 10, f"({r4['drift_ppm']:+.1f} ppm)" if r4["ok"] else r4["why"])

noise = np.random.default_rng(3).standard_normal(int(60 * FS))
r5 = D.analyze(noise, FS, META)
check("no signal in the recording is reported as a failure, not a number", not r5["ok"])

import tempfile
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "t.wav")
    meta = D.make(p, period=4, count=3, gap=1.0, fs=8000)
    from scipy.io import wavfile
    fs, w = wavfile.read(p)
    check("make writes a stereo file with chirps on both channels, B after A",
          w.shape[1] == 2 and np.abs(w[:, 0]).max() > 0.2 and np.abs(w[:, 1]).max() > 0.2
          and np.argmax(np.abs(w[:, 1]) > 0.05) - np.argmax(np.abs(w[:, 0]) > 0.05) > 0.9 * 8000,
          f"({w.shape}, fs {fs})")

sys.exit(0 if ok else 1)
