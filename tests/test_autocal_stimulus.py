"""Tests for autocal/stimulus.py (caps, bands, ramp logic, timeline) and autocal/profile.py (room.json, consistency with set-eq.py).
Run: python3 tests/test_autocal_stimulus.py   (needs numpy, scipy)"""
import json
import os
import subprocess
import sys

import numpy as np

from autocal_common import check, finish

from autocal import stimulus as S, profile as PF, ir as IR

# ---- the -20 dBFS cap -------------------------------------------------------------------------------------------------------------------
tl = S.Timeline(sweep_s=3.0, tail_s=1.5, level_db=-26.0)
x = tl.render()
check("timeline peak is at or below the -20 dBFS cap", S.peak_dbfs(x) <= -20.0 + 1e-6, f"({S.peak_dbfs(x):.3f} dBFS)")
check("sweeps run at the requested level, sync chirps at the cap", abs(S.peak_dbfs(x[int(tl.cues['slots'][1]['t'] * 48000):int((tl.cues['slots'][1]['t'] + 3) * 48000), 1]) + 26.0) < 0.05
      and abs(S.peak_dbfs(x[:, 0][int(2 * 48000):int(2.1 * 48000)]) + 20.0) < 0.05)
for bad in (-19.0, 0.0):
    try:
        S.Timeline(level_db=bad); ok = False
    except S.LevelCapError:
        ok = True
    check(f"a requested level of {bad} dBFS is refused", ok)
try:
    S.assert_cap(np.array([0.2]), what="x"); ok = False
except S.LevelCapError:
    ok = True
check("assert_cap raises above the cap and passes at it", ok)
S.assert_cap(np.array([S.db_to_amp(-20.0)]))
for lv in (-20.0, -26.0, -50.0):
    check(f"pink burst peak equals {lv} dBFS", abs(S.peak_dbfs(S.pink_burst(lv)) - lv) < 0.01)
try:
    S.pink_burst(-15.0); ok = False
except S.LevelCapError:
    ok = True
check("a burst above the cap is refused", ok)
idx, cues = S.id_stimulus(-20.0)
check("channel-ID chirps respect the cap", S.peak_dbfs(idx) <= -20.0 + 1e-6)

# ---- bands --------------------------------------------------------------------------------------------------------------------------------
def spectrum_edges(ch, sweep_s=7.0):
    w, f0, f1 = S.shaped_sweep(ch, sweep_s)
    F = np.abs(np.fft.rfft(w)); f = np.fft.rfftfreq(len(w), 1 / 48000)
    return f, 20 * np.log10(F / F.max() + 1e-12), f0, f1

for ch, lo, hi in (("RL", 100, 20000), ("RR", 100, 20000), ("FL", 40, 20000), ("SR", 40, 20000), ("LFE", 25, 200)):
    f, db, f0, f1 = spectrum_edges(ch)
    below = np.max(db[f < lo * 0.7]); above = np.max(db[f > hi * 1.4]) if hi < 20000 else -99.0
    check(f"{ch} sweep is band-limited to {lo}-{hi} Hz", (f0, f1) == (lo, hi) and below < -25 and above < -25, f"(below the band {below:.0f} dB, above {above:.0f} dB)")
w, _, _ = S.shaped_sweep("FL", 7.0)
t = np.arange(len(w)) / 48000; finst = 40 * np.exp(t / 7.0 * np.log(20000 / 40))
amp = lambda lo, hi: np.max(np.abs(w[(finst > lo) & (finst < hi)]))
check("satellite sweeps start at 40 Hz at full amplitude (the -12 dB step only matters below 35 Hz)", amp(60, 80) > 0.95)
w2, _, _ = S.shaped_sweep("LFE", 7.0)
f2 = 25 * np.exp(t[:len(w2)] / 7.0 * np.log(200 / 25))
r = np.max(np.abs(w2[(f2 > 25) & (f2 < 28)])) / np.max(np.abs(w2[(f2 > 40) & (f2 < 60)]))
check("LFE sweep at 25-28 Hz is about 12 dB down", abs(20 * np.log10(r) + 12) < 1.0, f"({20 * np.log10(r):.1f} dB)")
check("no steady tones: the sweep frequency rises continuously", bool(np.all(np.diff(finst) > 0)))

# ---- ramp logic -----------------------------------------------------------------------------------------------------------------------------
check("ramp levels run from -50 dBFS in 6 dB steps and stop at the cap", S.ramp_levels() == [-50.0, -44.0, -38.0, -32.0, -26.0, -20.0])
steps = [(-50, -80), (-44, -74), (-38, -68), (-32, -62), (-26, -56)]
lv, snr, reached = S.ramp_decision(steps, -90.0)
check("ramp stops at the first level 30 dB over the noise floor", (lv, reached) == (-26, True) and 30 <= snr < 36, f"(stop {lv}, {snr:.0f} dB)")
lv, snr, reached = S.ramp_decision(steps[:3], -80.0)
check("ramp that never gets 30 dB uses the highest level tried and says so", reached is False and lv == -38)
check("sweep level = ramp level + boost, never above the cap or below the ramp start",
      S.sweep_level_from_ramp(-50) == -38 and S.sweep_level_from_ramp(-26) == -20 and S.sweep_level_from_ramp(-20) == -20 and S.sweep_level_from_ramp(-80) == -50)
check("the boost shrinks when the mic peak at the stopping level is already high (keeps the predicted peak under -10 dBFS)", S.sweep_level_from_ramp(-38, -20.0) == -38 + 10 and S.sweep_level_from_ramp(-38, -9.0) == -38 and S.sweep_level_from_ramp(-38, -40.0) == -26)

# ---- timeline structure ------------------------------------------------------------------------------------------------------------------------
c = tl.cues
check("timeline: 9 slots (8 speakers, FL repeated), 6 sync chirps, cues carry what ir.py needs", len(c["slots"]) == 9 and c["slots"][-1]["name"] == "FL" and len(c["sync"]) == 6
      and all(k in c for k in ("fs", "sweep_s", "slot_s", "f0", "f1", "sync", "slots")))
check("pilot chirp 0.5 s before each sweep", all(abs(s["t"] - s["pilot_t"] - 0.5) < 1e-9 for s in c["slots"]))
json.dumps(c)
check("hardware order FL FR RL RR FC LFE SL SR, result order FL FR FC LFE SL SR RL RR", S.HW_ORDER == ["FL", "FR", "RL", "RR", "FC", "LFE", "SL", "SR"]
      and S.RESULT_ORDER == ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"])
check("--dry text lists every sweep", all(n in tl.describe() for n in S.HW_ORDER))

# ---- profile / room.json -----------------------------------------------------------------------------------------------------------------------------
room = PF.default_room(); PF.validate_room(room)
check("default room validates and has 8 channels", len(room["align"]) == 8)
bad = json.loads(json.dumps(room)); del bad["align"]["SL"]
try:
    PF.validate_room(bad); ok = False
except PF.RoomError:
    ok = True
check("a room missing a channel is rejected", ok)
se = PF.find_set_eq()
if se:
    r = subprocess.run([sys.executable, se, "--dump-room"], capture_output=True, text=True)
    check("embedded default room equals `set-eq.py --dump-room`", r.returncode == 0 and json.loads(r.stdout) == PF.DEFAULT_ROOM)
    fs = PF.set_eq_filter_sets()
    check("embedded drone2 set equals set-eq.py FILTER_SETS['drone2']", [tuple(a) for a in fs["drone2"]] == [tuple(a) for a in PF.BASE_SETS["drone2"]])
else:
    print("SKIP set-eq consistency (sibling repo linux-theater-dsp not found)")
imp = np.zeros((48000, 8)); imp[100, :] = 1.0
y = PF.apply_profile(room, imp)
check("profile model: FL delayed by 10 samples (0.2 ms), SL by 144 (3.0 ms)", np.argmax(np.abs(y[:, 0])) == 110 and abs(np.argmax(np.abs(y[:, 6])) - 244) <= 2)
f = np.array([63.0, 1000.0])
resp = np.abs(PF.response(room, "FL", f))
check("model response at 63 Hz shows the drone2 cut, 1 kHz is flat", 20 * np.log10(resp[0]) < -3 and abs(20 * np.log10(resp[1])) < 0.1)
# the frequency-domain response equals the time-domain model
n = 1 << 15; imp = np.zeros((n, 8)); imp[0, 4] = 1.0
yt = PF.apply_profile(room, imp)[:, 4]; Ht = np.fft.rfft(yt); ff = np.fft.rfftfreq(n, 1 / 48000)
Hf = PF.response(room, "FC", ff[1:2000])
check("frequency-domain response matches the time-domain model (FC, 20 Hz-2.9 kHz)", np.max(np.abs(20 * np.log10(np.abs(Ht[1:2000]) / np.abs(Hf)))) < 0.05)
m = PF.merge_filters([{"name": "a", "freq": 60.0, "gain": -3.0, "q": 4.0}], [{"name": "b", "freq": 60.5, "gain": -2.0, "q": 4.0}, {"name": "c", "freq": 90.0, "gain": -4.0, "q": 4.0}])
check("merge_filters folds a near-identical filter in and appends a new one", len(m) == 2 and abs(m[0]["gain"] + 5.0) < 1e-6)

# ---- burst_report: matched-filter detection and unbiased level of the ramp burst (what the real rig needs below the room-noise peaks) -----------
import numpy as _np
_rng = _np.random.default_rng(5)
_FS = S.FS
_burst = S.pink_burst(-26.0)
_noise = lambda sec, db: _rng.standard_normal(int(sec * _FS)) * 10 ** (db / 20)
_nf = _noise(4.0, -55.0); _floor = 20 * _np.log10(_np.sqrt(_np.mean(_nf ** 2)))
_ir = _np.zeros(int(0.3 * _FS)); _ir[int(0.004 * _FS)] = 1.0; _ir[int(0.03 * _FS)] = 0.5; _ir[int(0.12 * _FS)] = -0.3          # a delayed, reverberant path
def _rec(gain_db):
    m = _noise(2.2, -55.0); y = _np.convolve(_burst, _ir)[:len(_burst)] * 10 ** (gain_db / 20); i = int(0.6 * _FS)
    m[i:i + len(y)] += y; return m
_rms_true = lambda g: 20 * _np.log10(_np.sqrt(_np.mean((_np.convolve(_burst, _ir)[:len(_burst)] * 10 ** (g / 20)) ** 2)))
_r = S.burst_report(_rec(-5.0), _burst, _floor, noise=_nf)
check("burst_report finds a burst 12 dB ABOVE the noise floor through a reverberant path, at the right lag", _r["detected"] and abs(_r["lag_s"] - 0.604) < 0.002 and _r["coherent_snr_db"] > 40, f"({_r})")
check("burst_report: the burst's level is estimated within 1.5 dB", abs(_r["level_dbfs"] - _rms_true(-5.0)) < 1.5 and 10 < _r["over_floor_db"] < 14, f"({_r['level_dbfs']:.1f} vs {_rms_true(-5.0):.1f}, {_r['over_floor_db']:.1f} over the floor)")
_r = S.burst_report(_rec(-27.0), _burst, _floor, noise=_nf)
check("burst_report still finds a burst 10 dB BELOW the noise rms (matched filter), which the raw level would call silence (the level below the floor is a noisy estimate: it is only used to say 'too quiet')", _r["detected"] and _r["over_floor_db"] <= 0.0, f"({_r})")
_fa = 0
for _ in range(40):
    _fa += S.burst_report(_noise(2.2, -55.0), _burst, _floor, noise=_nf)["detected"]
check("burst_report: noise alone is not detected (0 false alarms in 40 recordings)", _fa == 0, f"({_fa})")


# ---- ramp decision by correlation, with the first real rig's numbers (24, 31, 37 dB at -32, -26, -20 dBFS; nothing found below) -----------------
_steps = [(-50.0, 0.0), (-44.0, 0.0), (-38.0, 0.0), (-32.0, 24.0), (-26.0, 31.0), (-20.0, 37.0)]
_lv, _c, _reached = S.ramp_decision_coh(_steps)
check("ramp_decision_coh: the first rig never reaches 40 dB, so the ramp ends at the cap with 37 dB", _lv == -20.0 and _c == 37.0 and _reached is False)
_e = S.expected_ir_snr_db(37.0, 7.0, -20.0, -20.0)
check("expected_ir_snr_db: 37 dB at the cap with 7 s sweeps -> about 45 dB peak-to-noise (above the 40 'comfortable' line)", abs(_e - (37 + 10 * _np.log10(7.0))) < 1e-9 and _e >= S.EXPECTED_IR_OK_DB, f"({_e:.1f})")
check("expected_ir_snr_db: a boost from the ramp level to the sweep level adds the same number of dB", abs(S.expected_ir_snr_db(40.0, 7.0, -26.0, -38.0) - S.expected_ir_snr_db(40.0, 7.0, -38.0, -38.0) - 12.0) < 1e-9)
check("ramp_decision_coh stops at the first level that reaches 40 dB", S.ramp_decision_coh([(-50.0, 31.0), (-44.0, 43.0), (-38.0, 50.0)]) == (-44.0, 43.0, True))

finish("test_autocal_stimulus")
