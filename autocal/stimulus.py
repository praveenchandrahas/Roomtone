"""Stimulus generation for autocal: band-limited shaped sweeps, sync/pilot chirps, level-ramp bursts, channel-ID chirps.

Safety rules enforced HERE (and checked again by every Backend before anything is played):
* every generated waveform has a peak <= CAP_DBFS (-20 dBFS). `assert_cap` raises `LevelCapError` otherwise;
* per-channel bands: heights start at 100 Hz (small drivers), satellites at 40 Hz, LFE 25-200 Hz;
* -12 dB below 35 Hz (a smooth step between 30 and 35 Hz; undone in the deconvolution, which uses the shaped waveform);
* no steady tones: only sweeps, short chirps and short noise bursts.

Channel order: HW_ORDER is the file/hardware order (FL FR RL RR FC LFE SL SR, as CamillaDSP and the USB interface see it).
RESULT_ORDER is the order used in results.json (FL FR FC LFE SL SR RL RR; RL and RR are the ceiling heights).
"""
import numpy as np

FS = 48000
HW_ORDER = ["FL", "FR", "RL", "RR", "FC", "LFE", "SL", "SR"]
RESULT_ORDER = ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"]
HEIGHTS = ("RL", "RR")
BED = ("FL", "FR", "FC", "SL", "SR")
CAP_DBFS = -20.0                  # hard cap on the generator PEAK level
RAMP_START_DBFS = -50.0
RAMP_STEP_DB = 6.0
RAMP_SNR_TARGET_DB = 30.0         # (raw-level criterion of the first version; the ramp now uses the correlation criteria below)
RAMP_COH_TARGET_DB = 40.0         # ramp stops when the burst's correlation peak is this far above the correlation of noise alone
EXPECTED_IR_OK_DB = 40.0          # expected peak-to-noise of the sweeps' impulse responses: at or above this, no remark
EXPECTED_IR_MIN_DB = 30.0         # below this the run stops ("too quiet"); in between it warns
SWEEP_BOOST_DB = 12.0             # main sweeps run this far above the ramp's stopping level (a sweep concentrates energy), capped at CAP
LOW_STEP_DB, LOW_STEP_F = -12.0, (30.0, 35.0)

BANDS = {"FL": (40.0, 20000.0), "FR": (40.0, 20000.0), "FC": (40.0, 20000.0), "SL": (40.0, 20000.0), "SR": (40.0, 20000.0),
         "RL": (100.0, 20000.0), "RR": (100.0, 20000.0), "LFE": (25.0, 200.0)}


class LevelCapError(AssertionError):
    """A waveform exceeded the -20 dBFS generator cap. Never caught inside autocal: it must stop the run."""


def db_to_amp(db):
    return 10 ** (db / 20)


def peak_dbfs(x):
    p = float(np.max(np.abs(x))) if np.size(x) else 0.0
    return 20 * np.log10(p + 1e-12)


def assert_cap(x, cap=CAP_DBFS, what="stimulus"):
    """The -20 dBFS hard cap assertion. Tolerance 1e-6 relative (float rounding only)."""
    if np.size(x) and np.max(np.abs(x)) > db_to_amp(cap) * (1 + 1e-6):
        raise LevelCapError(f"{what}: peak {peak_dbfs(x):.2f} dBFS exceeds the {cap:.0f} dBFS cap")


def sweep_gain_shape(finst):
    """Amplitude factor vs instantaneous frequency: -12 dB below 30 Hz, smooth to 0 dB at 35 Hz."""
    lo, hi = LOW_STEP_F
    u = np.clip((finst - lo) / (hi - lo), 0, 1); u = u * u * (3 - 2 * u)
    return db_to_amp(LOW_STEP_DB) + (1 - db_to_amp(LOW_STEP_DB)) * u


def shaped_sweep(ch, sweep_s=7.0, fs=FS):
    """Unit-peak log sweep for channel name `ch` in its band, low end shaped. Returns (waveform, f0, f1)."""
    f0, f1 = BANDS[ch]
    n = int(sweep_s * fs); t = np.arange(n) / fs; k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * sweep_s / k * (np.exp(t / sweep_s * k) - 1))
    x *= sweep_gain_shape(f0 * np.exp(t / sweep_s * k))
    nf, no = int(0.05 * fs), int(0.02 * fs)
    x[:nf] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
    x[-no:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(no) / no)
    return x, f0, f1


def chirp(ms=30, f0=400.0, f1=8000.0, fs=FS):
    """Unit-amplitude sync/pilot chirp (identical shape to ir.chirp)."""
    n = int(fs * ms / 1000); k = np.log(f1 / f0); T = n / fs; t = np.arange(n) / fs
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))) * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def sweep_at_level(ch, level_db, sweep_s=7.0):
    """The waveform that is PLAYED for a slot (this is what the deconvolution must use as `stim`)."""
    x, _, _ = shaped_sweep(ch, sweep_s)
    return x * db_to_amp(level_db)


class Timeline:
    """One measurement timeline (one microphone position): sync block, pilot + sweep for each channel (FL repeated at the end), end sync block.

    Layout (seconds): 3 sync chirps (FL) at lead; for each slot a pilot chirp on the same channel 0.5 s before its sweep; the sweep;
    `tail_s` of silence; 3 closing sync chirps. `cues` is JSON-serialisable and has the keys ir.placement/ir.deconv need
    (fs, sweep_s, slot_s, f0, f1, sync, slots[t, ch]) plus per-slot f0/f1/pilot_t/level_db.
    """

    def __init__(self, sweep_s=7.0, tail_s=2.0, level_db=-32.0, sync_db=CAP_DBFS, repeat_fl=True, lead_s=2.0, order=None):
        if level_db > CAP_DBFS or sync_db > CAP_DBFS:
            raise LevelCapError(f"requested levels {level_db}/{sync_db} dBFS exceed the {CAP_DBFS:.0f} dBFS cap")
        self.sweep_s, self.tail_s, self.level_db, self.sync_db = sweep_s, tail_s, level_db, sync_db
        names = list(order or HW_ORDER) + (["FL"] if repeat_fl else [])
        pitch = sweep_s + tail_s + 0.5
        t_sync = (lead_s, lead_s + 0.5, lead_s + 1.0)
        slot0 = lead_s + 3.5
        slots = []
        for i, nm in enumerate(names):
            f0, f1 = BANDS[nm]
            t = slot0 + 0.5 + i * pitch
            slots.append({"t": t, "ch": [HW_ORDER.index(nm)], "name": nm, "f0": f0, "f1": f1, "pilot_t": t - 0.5, "level_db": level_db})
        end = slots[-1]["t"] + sweep_s + tail_s + 1.0
        self.cues = {"fs": FS, "names": HW_ORDER, "sweep_s": sweep_s, "slot_s": sweep_s + tail_s, "f0": 20.0, "f1": 20000.0,
                     "sync": list(t_sync) + [end, end + 0.5, end + 1.0], "total": end + 1.0 + 2.5, "slots": slots, "level_db": level_db,
                     "sync_db": sync_db}
        self.total = self.cues["total"]

    def render(self):
        """(n, 8) float array in hardware order. Asserts the cap before returning."""
        x = np.zeros((int(self.total * FS) + 1, 8))
        c = chirp() * db_to_amp(self.sync_db); cp = chirp() * db_to_amp(min(self.sync_db, self.level_db + 6.0))
        for tt in self.cues["sync"]:
            i = int(round(tt * FS)); x[i:i + len(c), 0] += c
        for sl in self.cues["slots"]:
            ch = sl["ch"][0]
            i = int(round(sl["pilot_t"] * FS)); x[i:i + len(cp), ch] += cp
            s = sweep_at_level(sl["name"], sl["level_db"], self.sweep_s)
            i = int(round(sl["t"] * FS)); x[i:i + len(s), ch] += s
        assert_cap(x, what="timeline")
        return x

    def describe(self):
        """Text timeline for --dry."""
        lines = [f"timeline {self.total:.0f} s; sweeps at {self.level_db:.1f} dBFS peak, sync chirps at {self.sync_db:.1f} dBFS (cap {CAP_DBFS:.0f})"]
        lines.append(f"  {self.cues['sync'][0]:6.1f} s  sync chirps x3 on FL")
        for sl in self.cues["slots"]:
            lines.append(f"  {sl['t']:6.1f} s  {sl['name']:<3} sweep {sl['f0']:.0f}-{sl['f1']:.0f} Hz, {self.sweep_s:.0f} s (pilot chirp 0.5 s before)")
        lines.append(f"  {self.cues['sync'][3]:6.1f} s  closing sync chirps x3")
        return "\n".join(lines)


def ramp_levels(start=RAMP_START_DBFS, step=RAMP_STEP_DB, cap=CAP_DBFS):
    """Generator levels of the level ramp: start, start+step, ... never above the cap."""
    out, lv = [], start
    while lv <= cap + 1e-9:
        out.append(round(lv, 3)); lv += step
    if out[-1] < cap - 1e-9:
        out.append(cap)
    return out


def pink_burst(level_dbfs, dur=1.0, lo=50.0, hi=4000.0, seed=1, fs=FS):
    """Band-limited pink noise burst with 20 ms fades, normalised so its PEAK is level_dbfs (so the cap applies directly)."""
    rng = np.random.default_rng(seed)
    n = int(dur * fs); X = np.fft.rfft(rng.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / fs)
    g = np.where((f >= lo) & (f <= hi), 1 / np.sqrt(np.maximum(f, 1.0)), 0.0)
    x = np.fft.irfft(X * g, n)
    nf = int(0.02 * fs); w = np.ones(n); w[:nf] = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf); w[-nf:] = w[:nf][::-1]
    x *= w
    x *= db_to_amp(level_dbfs) / np.max(np.abs(x))
    assert_cap(x, what="burst")
    return x


def _xcorr(mic, tmpl, fs=FS):
    """c[k] = sum_i mic[k+i] * tmpl[i] for every lag k with the template fully inside the recording."""
    n = len(tmpl); L = 1 << int(np.ceil(np.log2(len(mic) + n)))
    return np.fft.irfft(np.fft.rfft(mic, L) * np.conj(np.fft.rfft(tmpl, L)), L)[:len(mic) - n + 1]


def burst_report(mic, burst, floor_dbfs, noise=None, fs=FS, detect_db=18.0):
    """Finds the known burst in a mic recording by cross-correlation (a matched filter, so it works far below the room noise) and reports
    its level at the mic, free of the bias of 'loudest 100 ms frame of a fluctuating room'.

    The null spread (what the correlation does with noise alone) is measured from the run's own noise-floor recording `noise` when given:
    the recording of the burst cannot serve for that, because the room response makes the correlation non-zero around the peak.
    Returns dict: detected, coherent_snr_db (correlation peak over that spread; noise alone peaks at about 13 dB, so detection needs
    detect_db = 18), lag_s, level_dbfs (rms of the burst alone at the mic: power in the burst window minus the mean noise-floor power;
    equals the floor when not detected), over_floor_db."""
    mic = np.asarray(mic, dtype=np.float64); n = len(burst)
    none = {"detected": False, "coherent_snr_db": 0.0, "lag_s": None, "level_dbfs": float(floor_dbfs), "over_floor_db": 0.0}
    if len(mic) < n + int(0.2 * fs):
        return none
    tmpl = np.asarray(burst, dtype=np.float64) / np.sqrt(np.sum(np.asarray(burst, dtype=np.float64) ** 2))     # unit energy: white noise of std s gives std s
    c = _xcorr(mic, tmpl, fs)
    k = int(np.argmax(np.abs(c))); peak = float(abs(c[k]))
    if noise is not None and len(noise) >= n + fs // 2:
        c0 = _xcorr(np.asarray(noise, dtype=np.float64), tmpl, fs)
    else:                                                    # fallback: lags that cannot overlap the burst at all (needs a long recording)
        keep = np.ones(len(c), bool); keep[max(0, k - n):k + n] = False
        c0 = c[keep]
    spread = 1.4826 * float(np.median(np.abs(c0 - np.median(c0)))) if c0.size > 100 else 0.0
    coh = float(20 * np.log10(peak / max(spread, 1e-12))) if spread > 0 else 0.0
    detected = coh >= detect_db
    p_win = float(np.mean(mic[k:k + n] ** 2)); p_floor = 10 ** (floor_dbfs / 10)
    level = float(10 * np.log10(p_win - p_floor)) if (detected and p_win > p_floor) else float(floor_dbfs)     # below the floor: found, but not measurable
    return {"detected": bool(detected), "coherent_snr_db": coh, "lag_s": k / fs, "level_dbfs": level, "over_floor_db": float(level - floor_dbfs)}


def ramp_decision_coh(steps, target_db=RAMP_COH_TARGET_DB):
    """steps: list of (generator level dBFS, correlation SNR dB or 0 when the burst was not found). Returns (stop_level, coherent_snr_at_stop, reached).

    Stops at the first level whose correlation reaches target_db; if none does, the highest level tried (which is at most the cap)."""
    for lv, c in steps:
        if c >= target_db:
            return lv, c, True
    lv, c = steps[-1]
    return lv, c, False


def expected_ir_snr_db(coh_db, sweep_s, sweep_level_dbfs, ramp_level_dbfs):
    """Peak-to-noise of the sweeps' deconvolved impulse response, from the ramp burst's correlation SNR: the sweep is `sweep_s` long instead of
    1 s (+10 log10) and plays at sweep_level instead of ramp_level (+ the difference). Conservative: a sweep has a lower crest factor than the pink
    burst (more energy at the same peak level), which is ignored here."""
    return float(coh_db + 10 * np.log10(max(sweep_s, 1e-3)) + (sweep_level_dbfs - ramp_level_dbfs))


def ramp_decision(steps, floor_dbfs, target_db=RAMP_SNR_TARGET_DB, cap=CAP_DBFS):
    """steps: list of (generator level dBFS, mic rms dBFS) in ramp order. Returns (stop_level, snr_db_at_stop, reached_target).

    The ramp stops at the first level where the mic is >= target_db above the noise floor; if none, the highest level tried (<= cap)."""
    for lv, m in steps:
        if m - floor_dbfs >= target_db:
            return lv, m - floor_dbfs, True
    lv, m = steps[-1]
    return lv, m - floor_dbfs, False


PEAK_BUDGET_DBFS = -10.0          # the mic peak predicted for the main sweeps must stay below this (the abort limit is -6)


def sweep_level_from_ramp(ramp_level, ramp_mic_peak_dbfs=None):
    """Main sweep level: ramp stopping level + SWEEP_BOOST_DB, never above the cap, never below the ramp start.

    If the mic peak seen at the ramp's stopping level is known, the boost is reduced so that (peak + boost) stays under PEAK_BUDGET_DBFS:
    a very sensitive rig (or a strong room mode) must not push the sweeps into the -6 dBFS abort."""
    boost = SWEEP_BOOST_DB
    if ramp_mic_peak_dbfs is not None:
        boost = min(boost, PEAK_BUDGET_DBFS - ramp_mic_peak_dbfs)
    return float(min(CAP_DBFS, max(RAMP_START_DBFS, ramp_level + max(0.0, boost))))


ID_SLOT_S = 0.8


def id_stimulus(level_db, fs=FS):
    """Channel-ID: one quiet chirp per speaker (hardware order), 0.8 s apart. LFE gets a 40-120 Hz chirp. Returns (x (n,8), cues)."""
    n = int((0.5 + 8 * ID_SLOT_S + 0.5) * fs); x = np.zeros((n, 8)); times = []
    for i, nm in enumerate(HW_ORDER):
        t = 0.5 + i * ID_SLOT_S
        c = chirp(ms=150, f0=40.0, f1=120.0) if nm == "LFE" else chirp()
        k = int(t * fs); x[k:k + len(c), i] = c * db_to_amp(level_db); times.append(t)
    assert_cap(x, what="channel-ID")
    return x, {"times": times, "names": HW_ORDER}
