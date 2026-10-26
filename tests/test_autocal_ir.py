"""Golden tests: autocal/ir.py must give results IDENTICAL to tools/sweeptest/analyze-sweeptest.py on a synthetic recording.
Run: python3 tests/test_autocal_ir.py   (needs numpy, scipy; no hardware)"""
import importlib.util
import os
import tempfile
import wave

import numpy as np

from autocal_common import check, finish, ROOT
os.environ.setdefault("MIC_CAL", "none")

from autocal import ir as IR


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


MK = load(os.path.join(ROOT, "tools", "sweeptest", "make-sweeptest.py"), "mk_sweeptest")
AS = load(os.path.join(ROOT, "tools", "sweeptest", "analyze-sweeptest.py"), "an_sweeptest")

# ---- synthetic recording of the existing sweeptest stimulus: per-speaker delays, a reflection, clock drift, noise -----------------------------
C = MK.cues()
x8 = MK.render(-20.0)
FS = MK.FS
delays_ms = [3.2, 3.5, 2.1, 2.3, 2.9, 4.4, 2.0, 2.2]
n = x8.shape[0] + FS * 3
mic = np.zeros(n)
for ch in range(8):
    for d_ms, a in ((delays_ms[ch], 0.05 * (1 if ch % 2 == 0 else -1)), (delays_ms[ch] + 6.1, 0.012)):
        d = int(round(d_ms * FS / 1000)); mic[int(1.3 * FS) + d:int(1.3 * FS) + d + x8.shape[0]] += a * x8[:, ch]
ppm = 18.0                                                    # mic clock 18 ppm fast
src = np.arange(n) / (1 + ppm * 1e-6)
mic = np.interp(src, np.arange(n), mic)
mic += np.random.default_rng(3).standard_normal(n) * 10 ** (-72 / 20)

a1, b1, how1 = IR.placement(mic, C)
a2, b2, how2 = AS.placement(mic, C)
check("placement identical", a1 == a2 and b1 == b2 and how1 == how2, f"(a={a1:.6f}, ppm={1e6 * (b1 - 1):+.1f})")
check("placement recovers the 18 ppm clock offset", abs(1e6 * (b1 - 1) - ppm) < 2.5)

same = True; same_arr = True; same_spec = True; same_gd = True; same_th = True
cent = np.array([50, 100, 200, 500, 1000, 2000, 4000, 8000.0])
for i in (0, 1, 2, 5, 12):
    sl = C["slots"][i]
    d1 = IR.deconv(mic, C, a1, b1, sl); d2 = AS.deconv(mic, C, a2, b2, sl)
    same &= bool(np.array_equal(d1[0], d2[0])) and d1[1] == d2[1]
    lo, hi = (40, 120) if sl["ch"] == [5] else (300, 4000)
    r1 = IR.arrival(d1[0], d1[1], lo, hi); r2 = AS.arrival(d2[0], d2[1], lo, hi)
    same_arr &= r1 == r2
    f1, H1 = IR.spectrum(d1[0], d1[1], r1[0]); f2, H2 = AS.spectrum(d2[0], d2[1], r2[0])
    same_spec &= bool(np.array_equal(f1, f2) and np.array_equal(H1, H2))
    same_gd &= IR.group_delay_ms(f1, H1, 150, 400, d1[1]) == AS.group_delay_ms(f2, H2, 150, 400, d2[1])
    same_th &= bool(np.array_equal(IR.thirds(f1, H1, cent), AS.thirds(f2, H2, cent), equal_nan=True))
check("deconv output bit-identical (5 slots)", same)
check("arrival identical", same_arr)
check("spectrum (complex H) identical", same_spec)
check("group delay and thirds identical", same_gd and same_th)

# the library recovers the ground truth: arrival re FL = delay difference
d = IR.deconv(mic, C, a1, b1, C["slots"][1]); t_fr = IR.arrival(d[0], d[1], 300, 4000)[0]
d = IR.deconv(mic, C, a1, b1, C["slots"][0]); t_fl = IR.arrival(d[0], d[1], 300, 4000)[0]
check("FR-FL arrival matches the synthetic delay difference (0.3 ms) within 0.05 ms", abs((t_fr - t_fl) - 0.3) < 0.05, f"({t_fr - t_fl:.3f} ms)")

check("chirp and sweep_sig identical", np.array_equal(IR.chirp(), AS.chirp()) and np.array_equal(IR.sweep_sig(C), AS.sweep_sig(C)))
check("chirp equals make-sweeptest's chirp shape", np.allclose(IR.chirp(), MK.chirp(amp=1.0), atol=1e-12))

# error paths: both fail on noise, the library with an exception instead of SystemExit
noise = np.random.default_rng(5).standard_normal(FS * 150) * 0.01
try:
    AS.placement(noise, C); e1 = None
except SystemExit as e:
    e1 = e
try:
    IR.placement(noise, C); e2 = None
except IR.PlacementError as e:
    e2 = e
check("no sync chirps: analyser exits, library raises PlacementError", e1 is not None and e2 is not None)

# wav loading and mic calibration shared with the analysers
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "t.wav")
    sig = (np.sin(2 * np.pi * 440 * np.arange(FS) / FS) * 0.3)
    st = np.stack([sig, sig * 0.5], 1)
    IR.write_wav24(p, st)
    for cal in ("none", os.path.join(ROOT, "data", "mic", "dayton-imm-6s-calibration.txt")):
        os.environ["MIC_CAL"] = cal
        check(f"load_mono identical to the analyser (MIC_CAL={'none' if cal == 'none' else 'file'})", np.array_equal(IR.load_mono(p), AS.load_mono(p)))
    os.environ["MIC_CAL"] = "none"
    x, fs = IR.read_wav(p)
    check("write_wav24/read_wav round trip within 24-bit resolution", np.max(np.abs(x - st)) < 2e-7 and fs == FS)

# additions
f = np.fft.rfftfreq(1 << 16, 1 / FS); H = np.where((f > 400) & (f < 600), 1.0, 0.1)
check("band_level_db: a flat band of 1.0 reads 0 dB", abs(IR.band_level_db(f, H, 420, 580)) < 1e-9)
sm = IR.smooth_db(f, H, np.array([500.0, 5000.0]), 1 / 6)
check("smooth_db follows the response", abs(sm[0]) < 0.3 and abs(sm[1] + 20) < 0.3, f"({sm})")

# ---- noise reference: separate silent recordings give a stable noise power; one 180 ms window does not
from autocal import stimulus as _ST
_rng = np.random.default_rng(5)
_stim = _ST.sweep_at_level("FL", -20.0, 7.0); _slot = 9.5
_ref = IR.noise_ir_power([_rng.normal(0, 0.01, 20 * FS), _rng.normal(0, 0.01, 20 * FS)], _stim, 35, 45, _slot)
_ref2 = IR.noise_ir_power([_rng.normal(0, 0.01, 20 * FS), _rng.normal(0, 0.01, 20 * FS)], _stim, 35, 45, _slot)
_rep = [10 * np.log10(IR.noise_ir_power([_rng.normal(0, 0.01, 20 * FS), _rng.normal(0, 0.01, 20 * FS)], _stim, 35, 45, _slot)) for _ in range(8)]
check("noise_ir_power: repeated 40 s noise recordings scatter by under 1 dB (one 180 ms window scatters by about 4 dB)", np.std(_rep) < 1.0 and abs(10 * np.log10(_ref / _ref2)) < 2.5, f"(sd {np.std(_rep):.2f} dB)")
check("noise_ir_power: 3 dB more noise reads 3 dB higher", abs(10 * np.log10(IR.noise_ir_power([_rng.normal(0, 0.01 * 10 ** (3 / 20), 20 * FS), _rng.normal(0, 0.01 * 10 ** (3 / 20), 20 * FS)], _stim, 35, 45, _slot) / _ref) - 3.0) < 1.5)
check("noise_ir_power: too little audio gives None", IR.noise_ir_power([np.zeros(FS)], _stim, 35, 45, _slot) is None)
# a known IR (a 40 Hz burst) in noise: the SNR from a reference equals the SNR from the in-sweep window on average, and moves with the noise level
_h = np.zeros(1 << 19); _pre = 0.2; _c = int(_pre * FS)
_t = np.arange(int(0.1 * FS)) / FS; _h[_c:_c + len(_t)] = 0.02 * np.sin(2 * np.pi * 40 * _t) * np.hanning(len(_t))
_noise = lambda sd: _rng.normal(0, sd, len(_h))
_hs = _h + 0.0 * _noise(1)
_pn = IR.noise_ir_power([_rng.normal(0, 0.01, 20 * FS)], _stim, 35, 45, _slot)
_s1 = IR.band_snr_db(_hs, _pre, 0.0, 35, 45, noise_pow=_pn); _s2 = IR.band_snr_db(_hs, _pre, 0.0, 35, 45, noise_pow=_pn * 10 ** 0.6)
check("band_snr_db with a given noise power: 6 dB more noise gives 6 dB less SNR", abs((_s1 - _s2) - 6.0) < 1e-6, f"({_s1:.2f}, {_s2:.2f})")
finish("test_autocal_ir")
