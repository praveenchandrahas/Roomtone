#!/usr/bin/env python3
"""Self-test: pretends to be the room. Convolves dialoguetest-hw.wav with made-up impulse responses (direct sound, a few early
reflections, a diffuse tail of known RT60), adds background noise, writes sim-dialogue.wav (mono, 24 bit, started 8 s early),
then prints what the analyser SHOULD find so it can be compared with analyze-dialoguetest.py's report.

  python3 make-dialoguetest.py && python3 simulate-dialogue.py [--rt=0.35] [--noise=-62] [--comb]
"""
import sys, wave, json
import numpy as np
from scipy.signal import fftconvolve
from dialoguetest_common import *

opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
RT, NOISE = float(opt.get("rt", 0.35)), float(opt.get("noise", -62))
rng = np.random.default_rng(11)
w = wave.open("dialoguetest-hw.wav"); x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 8) / 32768.0


def room_ir(delay_ms, refl, tilt=1.0, n=int(1.2 * FS)):
    h = np.zeros(n); d = int(delay_ms / 1000 * FS); h[d] = 1.0
    for ms, db in refl:
        h[d + int(ms / 1000 * FS)] += 10 ** (db / 20) * (1 if rng.random() > 0.3 else -1)
    t = np.arange(n) / FS
    tail = rng.standard_normal(n) * np.exp(-6.91 * t / RT) * 10 ** (-13 / 20) * 0.35
    tail[:d + int(0.012 * FS)] = 0
    tail = np.concatenate([np.zeros(d), tail[:n - d]])
    return h + tail


irs = {0: room_ir(6.0, [(2.5, -14), (5.1, -9)]), 1: room_ir(6.2, [(3.0, -15)]), 4: room_ir(5.8, [(1.7, -17), (3.4, -14)])}
if "comb" in opt:
    pass
mic = np.zeros(len(x) + len(irs[0]))
direct_only = {}
for ch, h in irs.items():
    mic[:len(x) + len(h) - 1][:len(mic)] += fftconvolve(x[:, ch], h)[:len(mic)]
mic = mic[:len(x)]
mic = mic * 0.7                                    # mic gain
noise = rng.standard_normal(len(mic)); X = np.fft.rfft(noise); f = np.fft.rfftfreq(len(mic), 1 / FS); f[0] = f[1]
noise = np.fft.irfft(X / f ** 0.5, len(mic)); noise *= 10 ** (NOISE / 20) / np.sqrt(np.mean(noise ** 2))
mic += noise
mic = np.concatenate([np.zeros(int(8 * FS)), mic, np.zeros(int(4 * FS))])
pcm = np.clip(np.round(mic * (2 ** 23 - 1)), -2 ** 23, 2 ** 23 - 1).astype(np.int32)
b = np.stack([pcm & 255, (pcm >> 8) & 255, (pcm >> 16) & 255], -1).astype(np.uint8)
st = np.repeat(b[:, None, :], 2, 1).reshape(-1)
o = wave.open("sim-dialogue.wav", "wb"); o.setnchannels(2); o.setsampwidth(3); o.setframerate(FS); o.writeframes(st.tobytes()); o.close()

# ground truth from the known FC impulse response
def sti_truth(h, snr_free=True):
    k = int(np.argmax(np.abs(h))); m = []
    for fc in OCT:
        hb = octave_filter(h, fc); e = (hb[k - 100:k + int(0.6 * FS)] ** 2); tt = np.arange(len(e)) / FS
        m.append([abs(np.sum(e * np.exp(-2j * np.pi * F * tt))) / np.sum(e) for F in (0.63, 0.8, 1.0, 1.25, 1.6, 2.0, 2.5, 3.15, 4.0, 5.0, 6.3, 8.0, 10.0, 12.5)])
    ti = []
    for mb in m:
        mb = np.clip(mb, 1e-4, 0.9999); ti.append(np.mean((np.clip(10 * np.log10(np.array(mb) / (1 - np.array(mb))), -15, 15) + 15) / 30))
    ti = np.array(ti)
    return np.sum(np.array(ALPHA) * ti) - np.sum(np.array(BETA) * np.sqrt(ti[:-1] * ti[1:]))

print(f"simulated room: RT60 about {RT} s, noise {NOISE} dBFS (pink-ish). Truth: FC STI noise-free (from the true IR) = {sti_truth(irs[4]):.2f}")
print("expect from the analyser: FC STI (IR, noise-free) about that, STI with noise a little lower, direct about the same as with noise.")
