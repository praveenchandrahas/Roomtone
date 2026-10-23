#!/usr/bin/env python3
"""Writes sweeptest-hw.flac: one log sweep per speaker (and some pairs) for measuring with the mic at the seat.

  python3 make-sweeptest.py [--level=-20]

Play it THROUGH CamillaDSP:   ./play-through-camilla.sh sweeptest-hw.flac
Record on the mic:            ./rec.sh NAME 160      (start the recording about 8 s BEFORE the playback)

File channel order = the USB interface's order: FL FR RL(height) RR(height) FC LFE SL SR.
Timeline: 10 s silence, 3 sync chirps (FL), then 13 sweep slots of 9 s (7 s sweep 20 Hz-20 kHz + 2 s tail), 3 sync
chirps, 5 s silence. Slots: each single speaker in order, then pairs (RL+LFE, RR+LFE, RL+RR), then RL and LFE again
(repeatability check). Deterministic: analyze-sweeptest.py rebuilds the stimulus from sweeptest-cues.json.
"""
import json, subprocess, sys, wave
import numpy as np

FS = 48000
NAMES = ["FL", "FR", "RL", "RR", "FC", "LFE", "SL", "SR"]
SWEEP_S, SLOT_S, F0, F1 = 7.0, 9.0, 20.0, 20000.0
PAD, SYNC_T, SYNC_GAP = 10.0, (10.0, 10.5, 11.0), 3.0
SLOT0 = 13.0
SLOTS = [[0], [1], [2], [3], [4], [5], [6], [7], [2, 5], [3, 5], [2, 3], [2], [5]]
END_SYNC = SLOT0 + SLOT_S * len(SLOTS) + 1.0
SYNC_END_T = (END_SYNC, END_SYNC + 0.5, END_SYNC + 1.0)
TOTAL = END_SYNC + 1.0 + 5.0 + 0.5


def sweep():
    n = int(SWEEP_S * FS); t = np.arange(n) / FS; k = np.log(F1 / F0)
    x = np.sin(2 * np.pi * F0 * SWEEP_S / k * (np.exp(t / SWEEP_S * k) - 1))
    nf, no = int(0.05 * FS), int(0.02 * FS)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x


def chirp(ms=30, f0=400.0, f1=8000.0, amp=0.2):
    n = int(FS * ms / 1000); k = np.log(f1 / f0); T = n / FS; t = np.arange(n) / FS
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))
    return amp * w * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def cues():
    return {"fs": FS, "names": NAMES, "sweep_s": SWEEP_S, "slot_s": SLOT_S, "f0": F0, "f1": F1, "sync": list(SYNC_T) + list(SYNC_END_T),
            "total": TOTAL, "slots": [{"t": SLOT0 + i * SLOT_S, "ch": c} for i, c in enumerate(SLOTS)]}


def render(level_db):
    x = np.zeros((int(TOTAL * FS), 8)); a = 10 ** (level_db / 20); s = sweep() * a; c = chirp()
    for tt in SYNC_T + SYNC_END_T:
        i = int(round(tt * FS)); x[i:i + len(c), 0] += c
    for sl in cues()["slots"]:
        i = int(round(sl["t"] * FS))
        for ch in sl["ch"]:
            x[i:i + len(s), ch] += s
    return x


def main():
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    level = float(opt.get("level", -20))
    x = render(level)
    rng = np.random.default_rng(1)
    pcm = np.clip(np.round(x * 32767 + rng.random(x.shape) - rng.random(x.shape)), -32768, 32767).astype("<i2")
    w = wave.open("sweeptest-hw.wav", "wb"); w.setnchannels(8); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes()); w.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "sweeptest-hw.wav", "-c:a", "flac", "sweeptest-hw.flac"], check=True)
    json.dump(cues(), open("sweeptest-cues.json", "w"), indent=1)
    print(f"sweeptest-hw.flac: {TOTAL:.0f} s, sweeps at {level:.0f} dBFS per speaker. Record at least {TOTAL + 12:.0f} s.")


if __name__ == "__main__":
    main()
