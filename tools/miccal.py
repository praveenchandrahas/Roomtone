"""Microphone calibration for the recording analysers.

Every analyser reads its recording through a `load_mono()` that passes the samples through `apply()` here, so the response of the measurement microphone
is removed before anything is measured. The calibration file gives the microphone's deviation from flat in dB (REW/UMIK format: header lines starting with
`*` or `"`, then `frequency  dB [phase]`). As in REW, the values are SUBTRACTED. The correction is zero-phase (done in the frequency domain), so timing,
arrival times and reflection delays are not changed.

Which file: `--mic-cal=FILE` on the analyser's command line, else the environment variable MIC_CAL, else the first `*.txt` in `data/mic/` of this repository.
`--mic-cal=none` (or MIC_CAL=none) switches the correction off. A calibration file is valid for ONE microphone: replace `data/mic/*.txt` with your own.
"""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {}
_said = set()


def read_cal(path):
    """Returns (freq_hz, db) arrays from a REW-style calibration file (extra columns such as phase are ignored)."""
    f, d = [], []
    with open(path, errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s[0] in '*"#;':
                continue
            parts = s.replace(",", " ").split()
            try:
                fr, db = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                continue
            if fr > 0:
                f.append(fr)
                d.append(db)
    if len(f) < 3:
        raise ValueError(f"{path}: no calibration points found")
    order = np.argsort(f)
    return np.array(f)[order], np.array(d)[order]


def chosen_path():
    """The calibration file in use, or None when switched off or none exists."""
    want = None
    for a in sys.argv[1:]:
        if a.startswith("--mic-cal="):
            want = a.split("=", 1)[1]
    want = want or os.environ.get("MIC_CAL", "")
    if want.lower() == "none":
        return None
    if want:
        return want
    found = sorted(glob.glob(os.path.join(HERE, "..", "data", "mic", "*.txt")))
    return found[0] if found else None


def apply(x, fs):
    """Removes the microphone's response from the samples x (1-D float array) recorded at sample rate fs."""
    path = chosen_path()
    if path is None:
        if "off" not in _said:
            _said.add("off")
            print("mic calibration: NOT applied (levels and curves include the microphone's own response)")
        return x
    if path not in _cache:
        _cache[path] = read_cal(path)
    f, db = _cache[path]
    n = len(x)
    nfft = 1 << int(np.ceil(np.log2(n + fs)))
    fr = np.fft.rfftfreq(nfft, 1.0 / fs)
    c = np.interp(np.log10(np.maximum(fr, f[0])), np.log10(f), db)       # beyond the file's range the end values are held
    y = np.fft.irfft(np.fft.rfft(x, nfft) * 10.0 ** (-c / 20.0), nfft)[:n]
    if path not in _said:
        _said.add(path)
        print(f"mic calibration: applied {os.path.basename(path)} ({db.min():+.1f} to {db.max():+.1f} dB, subtracted; valid only for the microphone it was made for)")
    return y
