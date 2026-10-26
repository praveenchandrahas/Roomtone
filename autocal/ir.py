"""Impulse-response library: sync-chirp placement, log-sweep deconvolution, arrival, band level, complex H.

These functions are COPIED from tools/sweeptest/analyze-sweeptest.py (which is a hyphenated CLI and cannot be imported) so that autocal can
use them as a library. tests/test_autocal_ir.py runs both implementations on the same synthetic recording and requires bit-identical
output, so the validated analyser and this library cannot drift apart. The only intended differences:

* `placement` raises `PlacementError` instead of `SystemExit` (a library must not kill the process);
* `deconv` takes an optional `stim` (the waveform that was actually played). With `stim=None` it is identical to the original, which assumes
  the plain 20 Hz-20 kHz sweep. autocal plays band-limited, shaped sweeps and deconvolves with exactly what was played;
* `load_mono` is shared by every recording and applies the microphone calibration (tools/miccal.py), as all analysers do.

Time convention (same as the original): `deconv` returns (h, pre); index `pre*FS` of h is "zero extra delay", i.e. the instant a sweep would
arrive if the path had the same delay as the sync chirps (which are played on FL). All arrivals are therefore RELATIVE TO FL: there is no
absolute timing reference in this rig (see plan section 6).
"""
import importlib.util
import os
import wave

import numpy as np
from scipy.fft import rfft, irfft
from scipy.signal import fftconvolve, butter, sosfiltfilt, hilbert

FS = 48000
HERE = os.path.dirname(os.path.abspath(__file__))


class PlacementError(RuntimeError):
    """The sync chirps were not found, or the two sync blocks disagree (wrong recording, or a stutter during playback)."""


def _load_miccal():
    p = os.path.join(HERE, "..", "tools", "miccal.py")
    spec = importlib.util.spec_from_file_location("miccal", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MICCAL = _load_miccal()


def apply_mic_cal(x, fs=FS):
    """Removes the measurement microphone's response (zero phase). On by default; MIC_CAL=none switches it off."""
    return MICCAL.apply(x, fs)


def read_wav(path):
    """Returns (samples float64 (n, channels), fs) from a 16/24-bit PCM wav."""
    w = wave.open(path)
    raw, sw, nc, fs = w.readframes(w.getnframes()), w.getsampwidth(), w.getnchannels(), w.getframerate()
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
        x = np.where(x >= 1 << 23, x - (1 << 24), x) / 2**23
    elif sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) / 32768.0
    else:
        raise ValueError("unsupported sample width (use 16 or 24 bit)")
    return x.reshape(-1, nc).astype(np.float64), fs


def write_wav24(path, x, fs=FS):
    """Writes mono/multichannel float samples (|x| <= 1) as a 24-bit PCM wav (the raw recordings of a real run)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    q = np.clip(np.round(x * 2**23), -2**23, 2**23 - 1).astype(np.int32)
    b = np.stack([q & 255, (q >> 8) & 255, (q >> 16) & 255], -1).astype(np.uint8)
    w = wave.open(path, "wb")
    w.setnchannels(x.shape[1]); w.setsampwidth(3); w.setframerate(fs)
    w.writeframes(b.tobytes()); w.close()


def load_mono(path):
    """Channel 1 of a wav, mic calibration applied (identical to the analysers' load_mono, but raises instead of exiting)."""
    x, fs = read_wav(path)
    if fs != FS:
        raise ValueError(f"{path}: sample rate {fs} (need {FS})")
    return apply_mic_cal(x[:, 0].astype(np.float64), FS)


def chirp(ms=30, f0=400.0, f1=8000.0):
    """The 30 ms Hann-windowed log chirp used for sync and pilot marks (unit amplitude, same as the analyser)."""
    n = int(FS * ms / 1000); k = np.log(f1 / f0); T = n / FS; t = np.arange(n) / FS
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))) * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def sweep_sig(C):
    """The plain log sweep described by cues C (sweep_s, f0, f1) with the original fades."""
    T, f0, f1 = C["sweep_s"], C["f0"], C["f1"]
    n = int(T * FS); t = np.arange(n) / FS; k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))
    nf, no = int(0.05 * FS), int(0.02 * FS)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x


def block_corr(x, times):
    c = chirp(); span = int((times[-1] - times[0]) * FS) + len(c)
    tpl = np.zeros(span)
    for tt in times:
        i = int(round((tt - times[0]) * FS)); tpl[i:i + len(c)] += c
    return np.abs(fftconvolve(x, tpl[::-1], mode="valid"))


def refine(a, k):
    """Parabolic peak refinement (sub-sample)."""
    if 0 < k < len(a) - 1:
        y0, y1, y2 = a[k - 1], a[k], a[k + 1]; return k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-30)
    return float(k)


def placement(x, C):
    """Recording time of file t=0 (a), clock ratio (b) and a description, from the 2 x 3 sync chirps (FL)."""
    s, e = C["sync"][:3], C["sync"][3:]
    r = block_corr(x, s)
    k1 = int(np.argmax(r)); med = np.median(r)
    if r[k1] / (med + 1e-12) < 12:
        raise PlacementError("no sync chirps found: is this the right recording? (check ./rec.sh check)")
    D = int((e[0] - s[0]) * FS); tol = int(0.05 * FS)

    def partner(centre):
        lo, hi = centre - tol, centre + tol
        if lo < 0 or hi >= len(r):
            return None
        k = lo + int(np.argmax(r[lo:hi]))
        return k if r[k] > 0.5 * r[k1] else None
    kb = partner(k1 + D); ka = partner(k1 - D)
    if kb is not None:
        A, B = refine(r, k1) / FS, refine(r, kb) / FS
    elif ka is not None:
        A, B = refine(r, ka) / FS, refine(r, k1) / FS
    else:
        t = k1 / FS

        def rms(t0, t1):
            seg = x[int(max(0, t0) * FS):int(max(0, t1) * FS)]
            return np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
        after = rms(t + (C["slots"][0]["t"] - s[0]) + 1.0, t + (C["slots"][0]["t"] - s[0]) + 6.0)
        before = rms(t - 8.0, t - 4.0)
        if before > 3 * max(after, 1e-6):
            return refine(r, k1) / FS - e[0], 1.0, "end sync block only (the recording started late, after the first chirps)"
        return refine(r, k1) / FS - s[0], 1.0, "start sync block only (the recording stopped before the end chirps)"
    b = (B - A) / (e[0] - s[0])
    if abs(b - 1) > 3e-4:
        raise PlacementError(f"start and end sync blocks disagree by {1e6 * (b - 1):.0f} ppm: wrong recording, or a stutter during playback")
    return A - b * s[0], b, f"start and end sync blocks (clock drift {1e6 * (b - 1):+.0f} ppm)"


def deconv(x, C, a, b, slot, stim=None):
    """Complex impulse response of one slot (see module docstring for the time origin). `stim` = the waveform played in the slot."""
    N = 1 << 19; pre = 0.2
    t0 = a + b * slot["t"] - pre
    i0 = int(round(t0 * FS))
    if i0 < 0 or i0 + int((C["slot_s"] + 0.2) * FS) > len(x):
        return None
    y = np.zeros(N); seg = x[i0:i0 + int((C["slot_s"] + pre) * FS)]; y[:len(seg)] = seg
    s = sweep_sig(C) if stim is None else stim
    xs = np.zeros(N); j = int(pre * FS); xs[j:j + len(s)] = s
    Y, X = rfft(y), rfft(xs)
    f = np.fft.rfftfreq(N, 1 / FS); P = np.abs(X) ** 2
    band = (f >= 12) & (f <= 21000)
    H = np.where(band, Y * np.conj(X) / (P + 1e-5 * P[band].max()), 0)
    h = np.roll(irfft(H, N), int(pre * FS))
    return h, pre


def bp_env(h, lo, hi):
    sos = butter(4, [lo, hi], btype="band", fs=FS, output="sos")
    return np.abs(hilbert(sosfiltfilt(sos, h)))


def arrival(h, pre, lo, hi, frac=0.5):
    """First arrival (ms, relative to the nominal sweep start) from the band-limited envelope; (ms, index of the envelope maximum)."""
    c = int(pre * FS); lo_i, hi_i = c - int(0.02 * FS), c + int(0.08 * FS)
    env = bp_env(h, lo, hi)[lo_i:hi_i]
    m = env.max(); k = int(np.argmax(env > frac * m))
    k0 = k
    if k > 0:
        e0, e1 = env[k - 1], env[k]; k0 = k - 1 + (frac * m - e0) / (e1 - e0 + 1e-30)
    return (lo_i + k0 - c) / FS * 1000.0, int(np.argmax(env)) + lo_i


def spectrum(h, pre, t_arr_ms, n=1 << 16):
    """Complex response from the IR gated at arrival-2 ms .. +300 ms (half-Hann on the tail), time reference kept."""
    c = int(pre * FS); k0 = c + int((t_arr_ms - 2.0) / 1000 * FS); k1 = k0 + int(0.3 * FS)
    g = h[k0:k1].copy()
    nt = int(0.1 * FS); g[-nt:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    nh = int(0.001 * FS); g[:nh] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nh) / nh)
    z = np.zeros(n); z[k0:k0 + len(g)] = g
    return np.fft.rfftfreq(n, 1 / FS), rfft(z)


def group_delay_ms(f, H, lo, hi, pre):
    m = (f >= lo) & (f <= hi)
    ph = np.unwrap(np.angle(H[m])); w = 2 * np.pi * f[m]
    return -np.polyfit(w, ph, 1)[0] * 1000.0 - pre * 1000.0


def thirds(f, H, centres):
    p = np.abs(H) ** 2; out = []
    for fc in centres:
        m = (f >= fc * 2 ** (-1 / 6)) & (f < fc * 2 ** (1 / 6)); out.append(10 * np.log10(p[m].mean() + 1e-30) if m.any() else np.nan)
    return np.array(out)


# ---- additions (not in the original analyser) -------------------------------------------------------------------------------------------

def band_level_db(f, H, lo, hi):
    """Mean power of the complex response in [lo, hi] Hz, in dB (the plan's 'band energy of the gated response, not peak')."""
    m = (f >= lo) & (f <= hi)
    return 10 * np.log10(np.mean(np.abs(H[m]) ** 2) + 1e-30)


def smooth_db(f, H, grid, width_oct):
    """Power-average |H|^2 within +-width/2 octave around each grid frequency, in dB. A grid point with no bins gets the nearest bin."""
    p = np.abs(H) ** 2
    out = np.empty(len(grid))
    lo_r, hi_r = 2 ** (-width_oct / 2), 2 ** (width_oct / 2)
    for i, fc in enumerate(grid):
        i0, i1 = np.searchsorted(f, fc * lo_r), np.searchsorted(f, fc * hi_r)
        if i1 <= i0:
            i0 = max(0, min(len(f) - 1, np.searchsorted(f, fc) - 1)); i1 = i0 + 1
        out[i] = 10 * np.log10(p[i0:i1].mean() + 1e-30)
    return out


def band_snr_db(h, pre, t_arr_ms, lo, hi, n=1 << 16, noise_pow=None):
    """Signal-to-noise ratio of one deconvolved slot in [lo, hi] Hz.

    Signal: the IR from arrival-2 ms for 180 ms. Noise: by default the IR region BEFORE the zero-delay point (0 .. pre-20 ms, also 180 ms): there a
    linear system has no response, so it holds only noise (the sweep's harmonic images fall further back, outside this window). That is a single 180 ms
    sample: in a 10 Hz band it holds about two independent noise samples, so one reading scatters by several dB. `noise_pow`, when given (from
    `noise_ir_power`, measured on separate silent recordings), replaces it and is stable to well under 1 dB."""
    c = int(pre * FS); L = c - int(0.02 * FS)
    k0 = c + int((t_arr_ms - 2.0) / 1000 * FS)
    sig = np.zeros(n); noi = np.zeros(n)
    g = h[k0:k0 + L].copy(); g[:int(0.001 * FS)] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(int(0.001 * FS)) / int(0.001 * FS))
    nt = int(0.06 * FS); g[-nt:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    sig[:len(g)] = g
    q = h[:L].copy(); q[-nt:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    noi[:len(q)] = q
    f = np.fft.rfftfreq(n, 1 / FS); m = (f >= lo) & (f <= hi)
    ps = np.mean(np.abs(rfft(sig))[m] ** 2)
    pn = np.mean(np.abs(rfft(noi))[m] ** 2) if noise_pow is None else noise_pow
    return float(10 * np.log10((ps + 1e-30) / (pn + 1e-30)))


def noise_ir_power(segments, stim, lo, hi, slot_s, n=1 << 16):
    """Noise power in [lo, hi] Hz of the deconvolved IR, from silent recordings, using exactly the processing `deconv` applies to a slot with waveform
    `stim`. `segments` is a list of mono arrays recorded with nothing playing (for example 20 s before and 20 s after a position). Each is cut into
    pieces of one slot length; every piece is deconvolved and its noise-only IR is measured in 180 ms windows over the lags where the whole stimulus
    still overlaps the piece, then all windows are averaged (power). Returns the mean power in the same units as the `ps` of band_snr_db, or None if
    the recordings are too short for one piece."""
    N = 1 << 19; pre = 0.2
    seglen = int((slot_s + pre) * FS); L = int((pre - 0.02) * FS); nt = int(0.06 * FS)
    taper = np.ones(L); taper[-nt:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(nt) / nt)
    xs = np.zeros(N); j = int(pre * FS); xs[j:j + len(stim)] = stim
    X = rfft(xs); fN = np.fft.rfftfreq(N, 1 / FS); P = np.abs(X) ** 2
    band = (fN >= 12) & (fN <= 21000)
    inv = np.where(band, np.conj(X) / (P + 1e-5 * P[band].max()), 0)
    f = np.fft.rfftfreq(n, 1 / FS); m = (f >= lo) & (f <= hi)
    last = int((slot_s - len(stim) / FS) * FS) + j          # last index with the whole stimulus still inside the piece
    pw = []
    for seg in segments:
        for a in range(0, len(seg) - seglen + 1, seglen):
            y = np.zeros(N); y[:seglen] = seg[a:a + seglen]
            h = np.roll(irfft(rfft(y) * inv, N), j)
            k = 0
            while k + L <= last:
                z = np.zeros(n); z[:L] = h[k:k + L] * taper
                pw.append(np.mean(np.abs(rfft(z))[m] ** 2)); k += L
    return float(np.mean(pw)) if pw else None
