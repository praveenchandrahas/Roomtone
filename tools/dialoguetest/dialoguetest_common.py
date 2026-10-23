"""Shared by make-dialoguetest.py and analyze-dialoguetest.py: the speech-test signal and its constants."""
import numpy as np
from scipy.signal import butter, sosfiltfilt

FS = 48000
OCT = [125, 250, 500, 1000, 2000, 4000, 8000]
# STIPA-style modulation pairs per octave band (Hz)
MODF = {125: (1.6, 8.0), 250: (1.0, 5.0), 500: (0.63, 3.15), 1000: (2.0, 10.0), 2000: (1.25, 6.25), 4000: (0.8, 4.0), 8000: (2.5, 12.5)}
# Approximate long-term male speech spectrum as a share of the total power per octave band (NOT the IEC table; indicative only)
SPEECH_SHARE = {125: 0.10, 250: 0.25, 500: 0.27, 1000: 0.18, 2000: 0.10, 4000: 0.07, 8000: 0.03}
# IEC 60268-16 male STI weights (alpha per octave, beta between neighbouring octaves)
ALPHA = [0.085, 0.127, 0.230, 0.233, 0.309, 0.224, 0.173]
BETA = [0.085, 0.078, 0.065, 0.011, 0.047, 0.095]
DIALOGUE_DBFS = -27.0          # film dialogue sits at about -27 dBFS RMS (dialnorm); the test plays at this level
STIPA_S = 22.0


def octave_sos(fc):
    return butter(4, [fc / 2 ** 0.5, min(fc * 2 ** 0.5, FS / 2 * 0.95)], btype="band", fs=FS, output="sos")


def octave_filter(x, fc):
    return sosfiltfilt(octave_sos(fc), x)


def stipa_signal(seed=7, dur=STIPA_S, level_db=DIALOGUE_DBFS):
    """Speech-shaped noise, each octave band intensity-modulated at two frequencies (a STIPA-style test signal; not IEC-certified)."""
    rng = np.random.default_rng(seed)
    n = int(dur * FS); t = np.arange(n) / FS
    out = np.zeros(n)
    for fc in OCT:
        band = octave_filter(rng.standard_normal(n), fc)
        band /= np.sqrt(np.mean(band ** 2))
        f1, f2 = MODF[fc]
        env = np.sqrt(np.maximum(1 + 0.5 * np.cos(2 * np.pi * f1 * t + rng.uniform(0, 6.28)) + 0.5 * np.cos(2 * np.pi * f2 * t + rng.uniform(0, 6.28)), 0))
        out += band * env * np.sqrt(SPEECH_SHARE[fc])
    nf = int(0.5 * FS)
    w = np.ones(n); w[:nf] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); w[-nf:] = w[:nf][::-1]
    out *= w
    return out * 10 ** (level_db / 20) / np.sqrt(np.mean(out ** 2))


def pink_noise(dur=12.0, seed=3, level_db=DIALOGUE_DBFS):
    rng = np.random.default_rng(seed); n = int(dur * FS)
    X = np.fft.rfft(rng.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / FS); f[0] = f[1]
    X /= np.sqrt(f); X[(f < 50)] = 0; X[f > 16000] = 0
    x = np.fft.irfft(X, n)
    nf = int(0.3 * FS); w = np.ones(n); w[:nf] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); w[-nf:] = w[:nf][::-1]
    x *= w
    return x * 10 ** (level_db / 20) / np.sqrt(np.mean(x ** 2))
