#!/usr/bin/env python3
"""Writes subtest-hw.flac: (A) three slow sweeps 8-200 Hz on the LFE channel at rising level, for "how low does the sub go"
and how it behaves under load, then (B) stepped bass tones, 19 steps from 25 to 200 Hz, played twice (quiet, then loud),
for finding the frequency that makes something rattle. Everything plays on LFE only (FL carries the sync chirps, FC a
tiny tick before each tone so you can count steps).

  python3 make-subtest.py [--loud=-14] [--quiet=-24] [--boost=0]

--boost=X adds X dB to everything (the three sweeps and both tone passes), refused if anything would peak above -1 dBFS.

Play THROUGH CamillaDSP:  ./play-through-camilla.sh subtest-hw.flac      Record: ./rec.sh sub1 300  (start it ~8 s before)
Channel order: FL FR RL RR FC LFE SL SR (USB interface order).
"""
import json, subprocess, sys, wave
import numpy as np

FS = 48000
SYNC_T = (10.0, 10.5, 11.0)
SWEEPS = [(-30.0, 13.0), (-22.0, 38.0), (-14.0, 63.0)]          # (level dBFS, start s); each 20 s + 5 s tail
SWEEP_T, SW_F0, SW_F1 = 20.0, 8.0, 200.0
TONES = [25, 28, 31.5, 35.5, 40, 45, 50, 56, 63, 71, 80, 90, 100, 112, 125, 140, 160, 180, 200]
TONE_S, STEP_S = 4.0, 5.0
PASS_T = (92.0, 92.0 + len(TONES) * STEP_S + 5.0)               # start of the quiet pass, of the loud pass
END_SYNC = PASS_T[1] + len(TONES) * STEP_S + 3.0
SYNC_END = (END_SYNC, END_SYNC + 0.5, END_SYNC + 1.0)
TOTAL = END_SYNC + 1.0 + 6.0


def levels(argv):
    opt = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    return float(opt.get("quiet", -24)), float(opt.get("loud", -14)), float(opt.get("boost", 0))


def chirp(ms=30, f0=400.0, f1=8000.0, amp=0.2):
    n = int(FS * ms / 1000); k = np.log(f1 / f0); T = n / FS; t = np.arange(n) / FS
    return amp * (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))) * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def sweep(T=SWEEP_T, f0=SW_F0, f1=SW_F1):
    n = int(T * FS); t = np.arange(n) / FS; k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))
    nf, no = int(0.3 * FS), int(0.05 * FS)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x


def tone(f, dur=TONE_S):
    n = int(dur * FS); t = np.arange(n) / FS; x = np.sin(2 * np.pi * f * t); nf = int(0.3 * FS)
    w = np.ones(n); w[:nf] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); w[-nf:] = w[:nf][::-1]
    return x * w


def cues(quiet, loud, boost=0.0):
    return {"fs": FS, "sync": list(SYNC_T) + list(SYNC_END), "total": TOTAL,
            "sweeps": [{"t": t, "level": lv + boost, "T": SWEEP_T, "f0": SW_F0, "f1": SW_F1} for lv, t in SWEEPS],
            "tones": [{"t": PASS_T[p] + i * STEP_S, "f": f, "dur": TONE_S, "level": (quiet, loud)[p] + boost, "pass": p}
                      for p in (0, 1) for i, f in enumerate(TONES)]}


def main():
    quiet, loud, boost = levels(sys.argv[1:])
    top = max(max(lv for lv, _ in SWEEPS), quiet, loud) + boost
    if top > -1.0:
        raise SystemExit(f"--boost={boost:g} puts the loudest step at {top:.1f} dBFS; keep it at or below -1 dBFS")
    x = np.zeros((int(TOTAL * FS), 8)); c = chirp()
    for tt in SYNC_T + SYNC_END:
        i = int(round(tt * FS)); x[i:i + len(c), 0] += c
    s = sweep()
    for lv, t in SWEEPS:
        i = int(round(t * FS)); x[i:i + len(s), 5] += s * 10 ** ((lv + boost) / 20)
    tick = 0.03 * (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(int(0.06 * FS)) / int(0.06 * FS))) * np.sin(2 * np.pi * 1200 * np.arange(int(0.06 * FS)) / FS)
    for cu in cues(quiet, loud, boost)["tones"]:
        i = int(round(cu["t"] * FS)); x[i:i + int(cu["dur"] * FS), 5] += tone(cu["f"]) * 10 ** (cu["level"] / 20)
        j = i - int(0.45 * FS); x[j:j + len(tick), 4] += tick
    rng = np.random.default_rng(2)
    pcm = np.clip(np.round(x * 32767 + rng.random(x.shape) - rng.random(x.shape)), -32768, 32767).astype("<i2")
    w = wave.open("subtest-hw.wav", "wb"); w.setnchannels(8); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes()); w.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "subtest-hw.wav", "-c:a", "flac", "subtest-hw.flac"], check=True)
    json.dump(cues(quiet, loud, boost), open("subtest-cues.json", "w"), indent=1)
    print(f"subtest-hw.flac: {TOTAL:.0f} s. Sweeps at {[lv + boost for lv, _ in SWEEPS]} dBFS; tones at {quiet + boost:g} then {loud + boost:g} dBFS. Record at least {TOTAL + 12:.0f} s.")
    print("Tone order (both passes):", ", ".join(f"{f:g}" for f in TONES), "Hz, 5 s apart, a tiny tick on the centre speaker before each.")


if __name__ == "__main__":
    main()
