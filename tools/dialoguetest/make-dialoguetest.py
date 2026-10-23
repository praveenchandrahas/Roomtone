#!/usr/bin/env python3
"""Writes dialoguetest-hw.flac: everything needed to measure how well dialogue comes through, in one 3-minute playback.

  python3 make-dialoguetest.py [--level=-20]

Contents (file channel order FL FR RL RR FC LFE SL SR; FL also carries the sync chirps):
  1. log sweeps (20 Hz-20 kHz, -20 dBFS): FC alone twice, FL alone, FR alone, FL+FR together (the phantom centre)
  2. 20 s of silence: the room's background noise (projector, fan, AC, as they will be running when you watch)
  3. a 22 s speech-shaped, STIPA-style modulated noise on FC at -27 dBFS RMS (film dialogue level), then
  4. 12 s of pink noise on FC at the same level, for an optional SPL reading with a phone or meter at the seat
  5. the same speech-shaped signal on FL+FR (phantom centre) at the same level
Play THROUGH CamillaDSP at your normal listening volume:   ./play-through-camilla.sh dialoguetest-hw.flac
Record on the mic (start about 8 s before):                  ./rec.sh dlg1 200
"""
import json, os, subprocess, sys, wave, importlib.util
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dialoguetest_common import *

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mk", os.path.join(HERE, "..", "sweeptest", "make-sweeptest.py"))
MK = importlib.util.module_from_spec(spec); spec.loader.exec_module(MK)

NAMES = MK.NAMES
SLOT_S = 9.0
SWEEP_SLOTS = [[4], [4], [0], [1], [0, 1]]
SLOT0 = 13.0
T_NOISE = SLOT0 + SLOT_S * len(SWEEP_SLOTS) + 1.0          # 59
NOISE_S = 20.0
T_STIPA_FC = T_NOISE + NOISE_S + 2.0
T_PINK = T_STIPA_FC + STIPA_S + 2.0
PINK_S = 12.0
T_STIPA_PH = T_PINK + PINK_S + 2.0
END_SYNC = T_STIPA_PH + STIPA_S + 2.0
SYNC_T = MK.SYNC_T
SYNC_END = (END_SYNC, END_SYNC + 0.5, END_SYNC + 1.0)
TOTAL = END_SYNC + 1.0 + 5.5


def cues():
    return {"fs": FS, "names": NAMES, "sweep_s": MK.SWEEP_S, "slot_s": SLOT_S, "f0": MK.F0, "f1": MK.F1,
            "sync": list(SYNC_T) + list(SYNC_END), "total": TOTAL,
            "slots": [{"t": SLOT0 + i * SLOT_S, "ch": c} for i, c in enumerate(SWEEP_SLOTS)],
            "noise": {"t": T_NOISE, "dur": NOISE_S},
            "stipa": [{"t": T_STIPA_FC, "dur": STIPA_S, "ch": [4], "seed": 7, "label": "FC"},
                      {"t": T_STIPA_PH, "dur": STIPA_S, "ch": [0, 1], "seed": 7, "label": "FL+FR"}],
            "pink": {"t": T_PINK, "dur": PINK_S, "ch": [4]}, "dialogue_dbfs": DIALOGUE_DBFS}


def render(level_db):
    x = np.zeros((int(TOTAL * FS), 8)); a = 10 ** (level_db / 20); s = MK.sweep() * a; c = MK.chirp()
    for tt in SYNC_T + SYNC_END:
        i = int(round(tt * FS)); x[i:i + len(c), 0] += c
    C = cues()
    for sl in C["slots"]:
        i = int(round(sl["t"] * FS))
        for ch in sl["ch"]:
            x[i:i + len(s), ch] += s
    for st in C["stipa"]:
        sig = stipa_signal(st["seed"]); i = int(round(st["t"] * FS))
        for ch in st["ch"]:
            x[i:i + len(sig), ch] += sig
    p = pink_noise(PINK_S); i = int(round(C["pink"]["t"] * FS)); x[i:i + len(p), 4] += p
    return x


def main():
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    x = render(float(opt.get("level", -20)))
    rng = np.random.default_rng(1)
    pcm = np.clip(np.round(x * 32767 + rng.random(x.shape) - rng.random(x.shape)), -32768, 32767).astype("<i2")
    w = wave.open("dialoguetest-hw.wav", "wb"); w.setnchannels(8); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes()); w.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "dialoguetest-hw.wav", "-c:a", "flac", "dialoguetest-hw.flac"], check=True)
    json.dump(cues(), open("dialoguetest-cues.json", "w"), indent=1)
    print(f"dialoguetest-hw.flac: {TOTAL:.0f} s. Record at least {TOTAL + 12:.0f} s, started about 8 s before the playback.")
    print(f"  noise-floor silence at {T_NOISE:.0f}-{T_NOISE + NOISE_S:.0f} s: keep the room as it is when you watch (projector, fan, AC ON), nobody talking.")


if __name__ == "__main__":
    main()
