#!/usr/bin/env python3
"""Tests for tools/miccal.py and its use by the analysers (no hardware, no recordings)."""
import importlib.util
import os
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
FS = 48000
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    fails += (not cond)


def load(name, path):
    sp = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


os.environ.pop("MIC_CAL", None)
MC = load("miccal", os.path.join(TOOLS, "miccal.py"))
CAL = os.path.join(HERE, "..", "data", "mic", "dayton-imm-6s-calibration.txt")

f, db = MC.read_cal(CAL)
check("the repository's calibration file is read (header skipped, 20 Hz to 20 kHz)", len(f) > 200 and f[0] <= 21 and f[-1] >= 19000, (len(f), f[0], f[-1]))
check("values are within the documented +-2.5 dB", np.abs(db).max() < 2.5, np.abs(db).max())

rng = np.random.default_rng(1)
x = rng.standard_normal(FS * 6)
n = len(x)
nfft = 1 << int(np.ceil(np.log2(n + FS)))
fr = np.fft.rfftfreq(nfft, 1 / FS)
c = np.interp(np.log10(np.maximum(fr, f[0])), np.log10(f), db)
mic_heard = np.fft.irfft(np.fft.rfft(x, nfft) * 10 ** (c / 20), nfft)[:n]          # what a microphone with this deviation would record
fixed = MC.apply(mic_heard, FS)


def band_db(sig, f0, f1):
    S = np.abs(np.fft.rfft(sig * np.hanning(len(sig)))) ** 2
    q = np.fft.rfftfreq(len(sig), 1 / FS)
    return 10 * np.log10(S[(q >= f0) & (q < f1)].sum())


worst = max(abs(band_db(fixed, a, b) - band_db(x, a, b)) for a, b in ((25, 40), (60, 120), (200, 400), (1000, 2000), (7000, 8000), (16000, 18000)))
check("correction undoes a microphone with this response (all bands within 0.15 dB)", worst < 0.15, worst)
check("the correction is zero-phase (no change in timing)", np.argmax(np.correlate(fixed[:FS], mic_heard[:FS], "full")) == FS - 1)

os.environ["MIC_CAL"] = "none"
check("MIC_CAL=none leaves the samples untouched", np.array_equal(MC.apply(mic_heard, FS), mic_heard))
del os.environ["MIC_CAL"]
sys.argv.append("--mic-cal=none")
check("--mic-cal=none on the command line also switches it off", np.array_equal(MC.apply(mic_heard, FS), mic_heard))
sys.argv.pop()

# the analysers really use it
tmp = tempfile.mkdtemp()
wav = os.path.join(tmp, "noise.wav")
pcm = (np.clip(x * 0.1, -1, 1) * 32767).astype(np.int16)
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes())
for label, path in (("sweep analyser", os.path.join(TOOLS, "sweeptest", "analyze-sweeptest.py")),
                    ("sub analyser", os.path.join(TOOLS, "subtest", "analyze-subtest.py")),
                    ("delay analyser", os.path.join(TOOLS, "analyze-delaytest.py"))):
    try:
        A = load("an_" + label.split()[0], path)
        on = A.load_mono(wav)
        os.environ["MIC_CAL"] = "none"
        off = A.load_mono(wav)
        del os.environ["MIC_CAL"]
        diff = band_db(on, 7000, 8000) - band_db(off, 7000, 8000)
        want = -float(np.interp(np.log10(7500), np.log10(f), db))
        check(f"{label}: load_mono applies the calibration (7-8 kHz changes by {diff:+.2f} dB, expected about {want:+.2f})", abs(diff - want) < 0.5, (diff, want))
    except Exception as ex:                      # noqa: BLE001
        check(f"{label}: load_mono applies the calibration", False, f"{type(ex).__name__}: {ex}")

print("FAILED" if fails else "ALL PASSED", fails)
sys.exit(1 if fails else 0)
