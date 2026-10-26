"""From one microphone recording of a Timeline to per-speaker measurements (arrival, level, SNR, complex response, pilot check).

All arrivals are relative to FL (the sync chirps are played on FL, see ir.py); there is no absolute timing reference in this rig.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import fftconvolve

from . import ir as IR
from .stimulus import HW_ORDER, sweep_at_level, chirp

FS = IR.FS
DEAD_SNR_DB = 8.0              # below this the slot holds no usable signal: the channel is reported dead
SNR_ABORT_LFE_40HZ_DB = 15.0   # abort if the sub (the main bass source) has less SNR than this at 35-45 Hz
SNR_FIT_MIN_40HZ_DB = 20.0     # a channel below this at 35-45 Hz is left out of the bass-fit average (and warned about)
# Rig data 2026-10-26 (single position, -20 dBFS cap): FL/FR/SR 28.5-29.6 dB, FC 22.8, LFE 20.2, SL 17.5 at 35-45 Hz. The first gate
# (25 dB for every channel, a guess from the plan) stopped a run whose broadband SNR was 33-43 dB and whose bass fit barely
# depends on the small satellites at 40 Hz.
SNR_WARN_DB = 30.0
PILOT_TOL_MS = 1.0


class MeasurementError(RuntimeError):
    """The recording cannot be analysed (no sync chirps, FL silent, SNR gate)."""


@dataclass
class ChannelMeasure:
    name: str
    t_arr_ms: float = None            # raw arrival (relative to the nominal sweep start, i.e. FL's sync chirp)
    arrival_ms: float = None          # re FL's sweep arrival
    f: np.ndarray = None
    H: np.ndarray = None              # complex response (gated IR, time origin kept)
    level_db: float = None            # 500-2000 Hz band level (LFE: 40-100 Hz): transfer gain mic/DAC, dB
    level_mid_db: float = None        # 300-4000 Hz band level (heights trim)
    snr_db: float = None              # in the arrival band
    snr40_db: float = None            # 35-45 Hz (None for heights, which start at 100 Hz); against the silent before/after recordings when given
    snr40_slot_db: float = None       # the same, but with the noise taken from the sweep's own IR (one 180 ms window: scatters by several dB)
    pilot_dev_ms: float = None
    dead: bool = False
    ir_t_ms: np.ndarray = None
    ir_h: np.ndarray = None
    notes: list = field(default_factory=list)


@dataclass
class PositionData:
    index: int
    label: str
    offset_cm: tuple
    weight: float
    ch: dict
    placement: str = ""
    drift_ppm: float = 0.0
    repeat_fl_rms_db: float = None
    repeat_fl_arrival_ms: float = None
    peak_dbfs: float = None
    clipped: int = 0
    noise_floor_dbfs: float = None


def _arr_band(name):
    return (40, 120) if name == "LFE" else (300, 4000)


def _pilot_offset_ms(x, a, b, slot):
    """Pilot chirp arrival relative to the nominal time, by matched filter (ms), or None if not found."""
    tp = a + b * slot["pilot_t"]
    i0 = int((tp - 0.02) * FS); n = int(0.12 * FS)
    if i0 < 0 or i0 + n > len(x):
        return None
    seg = x[i0:i0 + n]
    c = chirp(); r = np.abs(fftconvolve(seg, c[::-1], mode="valid"))
    k = int(np.argmax(r))
    if r[k] / (np.median(r) + 1e-12) < 8:
        return None
    return (IR.refine(r, k) / FS - 0.02) * 1000.0


def downsample_ir(h, pre, t0_ms=-5.0, t1_ms=95.0, max_points=2000):
    """A window of the IR around the zero-delay point, decimated to <= max_points by keeping the largest-magnitude sample of each block."""
    c = int(pre * FS); i0 = c + int(t0_ms * FS / 1000); i1 = c + int(t1_ms * FS / 1000)
    seg = h[i0:i1]; n = len(seg)
    step = int(np.ceil(n / max_points)); m = n // step
    blocks = seg[:m * step].reshape(m, step)
    pick = np.argmax(np.abs(blocks), axis=1)
    vals = blocks[np.arange(m), pick]
    t = (i0 - c + np.arange(m) * step + pick) / FS * 1000
    return t, vals


def analyse_recording(x_raw, cues, index=1, label="centre", offset_cm=(0.0, 0.0), weight=1.0, noise_floor_dbfs=None, apply_cal=True, noise_segments=None):
    """Analyses one recording made from `cues` (a Timeline's cues). Returns a PositionData.

    `noise_segments` (optional): mono recordings made with nothing playing (the runner records 20 s before and 20 s after each position). When given, the
    40 Hz signal-to-noise (snr40_db, which the bass gate reads) is measured against the noise in those recordings instead of the single 180 ms
    window of the sweep's own IR; the in-sweep value is kept as snr40_slot_db and the two are compared in the notes."""
    x = IR.apply_mic_cal(np.asarray(x_raw, float)) if apply_cal else np.asarray(x_raw, float)
    try:
        a, b, how = IR.placement(x, cues)
    except IR.PlacementError as e:
        raise MeasurementError(str(e)) from e
    pos = PositionData(index, label, tuple(offset_cm), weight, {}, placement=how, drift_ppm=(b - 1) * 1e6,
                       peak_dbfs=float(20 * np.log10(np.max(np.abs(x_raw)) + 1e-12)), clipped=int(np.sum(np.abs(x_raw) >= 0.999)),
                       noise_floor_dbfs=noise_floor_dbfs)
    raw = {}
    for si, sl in enumerate(cues["slots"]):
        name = sl["name"]
        stim = sweep_at_level(name, sl["level_db"], cues["sweep_s"])
        d = IR.deconv(x, cues, a, b, sl, stim=stim)
        if d is None:
            continue
        h, pre = d
        lo, hi = _arr_band(name)
        t_arr, _ = IR.arrival(h, pre, lo, hi)
        f, H = IR.spectrum(h, pre, t_arr)
        cm = ChannelMeasure(name, t_arr_ms=t_arr, f=f, H=H)
        cm.snr_db = IR.band_snr_db(h, pre, t_arr, 30, 120) if name == "LFE" else IR.band_snr_db(h, pre, t_arr, 300, 4000)
        if name in ("RL", "RR"):
            cm.snr40_db = None
        else:
            cm.snr40_db = cm.snr40_slot_db = IR.band_snr_db(h, pre, t_arr, 35, 45)
            if noise_segments:
                pn = IR.noise_ir_power(noise_segments, stim, 35, 45, cues["slot_s"])
                if pn is not None:
                    cm.snr40_db = IR.band_snr_db(h, pre, t_arr, 35, 45, noise_pow=pn)
        cm.dead = cm.snr_db < DEAD_SNR_DB
        cm.level_db = IR.band_level_db(f, H, 40, 100) if name == "LFE" else IR.band_level_db(f, H, 500, 2000)
        cm.level_mid_db = IR.band_level_db(f, H, 300, 4000)
        cm.ir_t_ms, cm.ir_h = downsample_ir(h, pre)
        if name != "LFE" and not cm.dead:
            po = _pilot_offset_ms(x, a, b, sl)
            cm.pilot_dev_ms = po
        raw.setdefault(name, []).append(cm)
    if "FL" not in raw:
        raise MeasurementError("FL sweep (the timing reference) is not in this recording")
    first = {n: v[0] for n, v in raw.items()}
    ref = first["FL"].t_arr_ms
    pil_ref = first["FL"].pilot_dev_ms
    for n, cm in first.items():
        cm.arrival_ms = cm.t_arr_ms - ref
        if cm.pilot_dev_ms is not None and pil_ref is not None:
            cm.pilot_dev_ms = (cm.pilot_dev_ms - pil_ref) - cm.arrival_ms        # disagreement between pilot and sweep timing
            if abs(cm.pilot_dev_ms) > PILOT_TOL_MS:
                cm.notes.append(f"pilot chirp disagrees with the sweep by {cm.pilot_dev_ms:+.2f} ms: possible xrun or glitch")
        else:
            cm.pilot_dev_ms = None
        if cm.dead:
            cm.notes.append("no usable signal: channel silent, disconnected or muted")
    pos.ch = first
    if len(raw["FL"]) > 1:
        a1, a2 = raw["FL"][0], raw["FL"][1]
        m = (a1.f >= 40) & (a1.f <= 8000)
        cent = np.geomspace(40, 8000, 40)
        d = IR.thirds(a1.f, a1.H, cent) - IR.thirds(a2.f, a2.H, cent)
        pos.repeat_fl_rms_db = float(np.sqrt(np.nanmean(d ** 2)))
        pos.repeat_fl_arrival_ms = a2.t_arr_ms - a1.t_arr_ms
    return pos


def analyse_channel_id(x_raw, id_cues, pre_s=0.3, ratio_min=10.0):
    """Channel-ID recording -> {name: {alive, ratio}}. A channel is alive if its chirp is found (matched filter peak / median >= ratio_min)
    in a window after its nominal time. Catches dead channels, a muted/unplugged speaker and a mic that hears nothing."""
    from .stimulus import chirp as _chirp
    x = np.asarray(x_raw, float); out = {}
    for name, t in zip(id_cues["names"], id_cues["times"]):
        c = _chirp(ms=150, f0=40.0, f1=120.0) if name == "LFE" else _chirp()
        r = np.abs(fftconvolve(x, c[::-1], mode="valid"))
        lo = max(0, int((t + pre_s - 0.05) * FS)); hi = min(len(r), int((t + pre_s + 0.45) * FS))
        pk = float(np.max(r[lo:hi])) if hi > lo else 0.0
        ratio = pk / (float(np.median(r)) + 1e-12)
        out[name] = {"alive": bool(ratio >= ratio_min), "ratio": round(ratio, 1)}
    return out
