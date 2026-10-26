"""Ground-truth simulated room for the SDHT rig.

What is modelled (all of it is a SIMULATION chosen to look like the rig, not a measurement of it):
* per-speaker geometry -> delay (distance / 343 m/s) that depends on the microphone position, per-speaker gain and polarity, a dead-channel option;
* Z906-like speakers: every satellite is a 140 Hz high-passed satellite PLUS a feed into one common subwoofer path (low-pass, a broad bass
  rise, modal resonances = minimum-phase peaking resonances with decay time Q/(pi f), a null) that lags the satellites by `sub_lag_ms` (6.5 ms);
  the LFE channel drives the sub path only. The two ceiling heights are small drivers (high-pass 150 Hz) with their own voicing, no sub path;
* the baseline geometry and gains are derived from set-eq.py's ALIGN, so the DEFAULT CamillaDSP profile is "almost right" and a scenario
  then perturbs the room (a moved surround, a quiet centre, a changed 50 Hz mode, ...), which is the situation autocal exists for;
* five microphone positions: distances change with the offset, and every mode's gain/frequency changes a little with the offset (standing waves);
* noise (pink-ish fan noise), a mic clock offset in ppm, an optional mic response (the inverse of tools/miccal's calibration, so the calibration
  step is exercised), second-harmonic distortion of the sub, and injected faults (a loud burst that clips the mic).
The room itself is LTI; `impulse_response(ch, offset_cm)` is the full path from the DAC output of channel `ch` to the mic (before the mic's clock).
"""
import numpy as np
from scipy.signal import sosfilt, butter, oaconvolve
from scipy import fft as sfft

from .. import ir as IR
from .. import profile as PF
from ..stimulus import HW_ORDER, RESULT_ORDER

FS = 48000
C = 343.0
SAT_HP_HZ = 140.0
HEIGHT_HP_HZ = 150.0
IR_LEN_S = 0.9

# name: (azimuth deg, elevation deg)  (azimuth 0 = front, positive = right)
POSE = {"FL": (-30, 0), "FR": (30, 0), "FC": (0, 0), "SL": (-100, 0), "SR": (100, 0), "RL": (-135, 50), "RR": (135, 50)}
SUB_POS = (-1.0, 2.0, -0.7)

# Sub path resonances: (freq Hz, gain dB, Q). A broad bass rise first, then modes, then the null (negative gain).
# Chosen so the DEFAULT profile (drone2) lands near the owner's 20 dB hump, with a few dB of error left for the fitter.
SUB_SHAPE = [(55.0, 16.0, 0.6), (95.0, 9.0, 0.9)]
SUB_MODES = [(47.0, 11.0, 9.0), (63.0, 10.0, 8.0), (84.0, 9.0, 8.0), (107.0, 5.0, 7.0), (140.0, 4.0, 6.0), (155.0, 7.0, 6.0), (177.0, 4.0, 6.0),
             (250.0, 4.0, 6.0)]
SUB_NULL = (72.0, -12.0, 6.0)

BASE_SAT_DB = -22.0                # satellite 500-2000 Hz transfer gain, mic amplitude / DAC amplitude
SUB_LEVEL_DB = 3.0                # sub path gain relative to the satellite level, before the shape/modes (tuned so the hump is ~+20 dB)


def _frac_delay_kernel(frac, half=16):
    n = np.arange(-half, half + 1)
    return np.sinc(n - frac) * np.kaiser(2 * half + 1, 8.0)


class SimRoom:
    """A ground-truth room. `scenario` names a set of perturbations (see `SCENARIOS`)."""

    SCENARIOS = ("default", "clean", "flipped", "dead", "null", "noisy")

    def __init__(self, scenario="default", seed=0, noise_dbfs=-74.0, mic_ppm=30.0, sub_lag_ms=6.5, mic_response=True,
                 sub_distortion_db=None, baseline_room=None, sensitivity_db=0.0):
        if scenario not in self.SCENARIOS:
            raise ValueError(f"unknown scenario {scenario}; choose from {self.SCENARIOS}")
        self.scenario, self.seed = scenario, seed
        self.noise_dbfs, self.mic_ppm, self.sub_lag_ms = noise_dbfs, mic_ppm, sub_lag_ms
        self.mic_response = mic_response
        self.sub_distortion_db = sub_distortion_db          # LFE second harmonic level in dB re fundamental (None = clean)
        rng = np.random.default_rng(seed + 1000)
        base = baseline_room or PF.default_room()
        # --- geometry derived from the baseline alignment: arrival = distance/343 + profile delay is the same for every speaker ---
        t_far = 10.496e-3
        self.coords = {}
        for ch, (az, el) in POSE.items():
            d = (t_far - base["align"][ch]["delay_ms"] * 1e-3) * C
            a, e = np.radians(az), np.radians(el)
            self.coords[ch] = np.array([d * np.cos(e) * np.sin(a), d * np.cos(e) * np.cos(a), d * np.sin(e)])
        self.coords["LFE"] = np.array(SUB_POS, float)
        # --- gains (dB): raw = what trims were chosen against, so the default profile matches the bed to FL; heights ~0.5 dB over the bed ---
        self.gain_db = {}
        for ch in ("FL", "FR", "FC", "SL", "SR"):
            self.gain_db[ch] = BASE_SAT_DB - base["align"][ch]["trim_db"]
        for ch in ("RL", "RR"):
            self.gain_db[ch] = BASE_SAT_DB + 0.5 - (base["align"][ch]["trim_db"] + base["height_tone"]["trim_db"])
        self.gain_db["LFE"] = BASE_SAT_DB - base["align"]["LFE"]["trim_db"]
        for ch in self.gain_db:
            self.gain_db[ch] += sensitivity_db                      # overall mic/amp sensitivity (tests move the level ramp with this)
        self.polarity = {ch: 1 for ch in HW_ORDER}
        self.dead = set()
        self.height_bump = {"RL": [], "RR": []}
        self.sub_modes = list(SUB_MODES)
        self.null = SUB_NULL
        self.null_shift = 0.004                              # fractional frequency shift of the null per 20 cm
        # heights voicing (what the default tone filters were fitted against) + an uncorrected error in the default scenario
        self.height_voicing = [("Peaking", 310.0, 7.5, 1.0), ("Peaking", 1700.0, 7.0, 2.5), ("Highshelf", 6000.0, -2.5, 0.8)]
        self.height_individual = {"RL": [("Peaking", 2800.0, 3.0, 5.0)], "RR": [("Peaking", 3500.0, -3.0, 5.0)]}
        if scenario in ("default", "flipped", "dead", "null", "noisy"):
            self.coords["SL"] = self._push(self.coords["SL"], 0.45)       # the left surround was moved 45 cm farther away
            self.gain_db["FC"] -= 2.5                                     # centre is quieter than when the trims were set
            self.sub_modes[0] = (52.0, 17.0, 9.0)                         # the 47 Hz mode moved to 52 Hz and got much stronger
            for h in ("RL", "RR"):
                self.height_bump[h].append(("Peaking", 1000.0, 3.0, 1.5))  # a shared mid bump the default tone does not cover
        if scenario == "flipped":
            self.polarity["RL"] = -1
        if scenario == "dead":
            self.dead.add("SR")
        if scenario == "null":
            self.null = (68.0, -26.0, 7.0)
            self.null_shift = 0.03
        if scenario == "noisy":
            self.noise_dbfs = -50.0
        self._mode_dirs = rng.uniform(0, 2 * np.pi, (len(SUB_MODES) + 1, 2)); self._mode_psi = rng.uniform(0, 2 * np.pi, len(SUB_MODES) + 1)
        self._ripple = {ch: [("Peaking", float(rng.uniform(300, 6000)), float(rng.uniform(-1.2, 1.2)), float(rng.uniform(1.5, 4)))
                             for _ in range(3)] for ch in HW_ORDER}
        self._reflect = {ch: (float(rng.uniform(4.0, 9.0)), float(rng.uniform(0.15, 0.3))) for ch in HW_ORDER}
        self._cache = {}

    @staticmethod
    def _push(v, extra_m):
        return v * (np.linalg.norm(v) + extra_m) / np.linalg.norm(v)

    # ---- ground truth -----------------------------------------------------------------------------------------------------------------

    def distance_m(self, ch, offset_cm=(0.0, 0.0)):
        mic = np.array([offset_cm[0] / 100.0, offset_cm[1] / 100.0, 0.0])
        return float(np.linalg.norm(self.coords[ch] - mic))

    def physical_delay_ms(self, ch, offset_cm=(0.0, 0.0)):
        """Acoustic delay of the satellite path of `ch` (for LFE: the sub path including the Z906 lag)."""
        d = self.distance_m(ch, offset_cm) / C * 1000
        return d + (self.sub_lag_ms if ch == "LFE" else 0.0)

    def raw_gain_db(self, ch):
        return self.gain_db[ch]

    # ---- impulse responses ------------------------------------------------------------------------------------------------------------

    def _spatial(self, k, offset_cm, f):
        """Mode k's gain scale and frequency scale at a mic offset (standing-wave pattern, zero change at the centre)."""
        lam = C / f
        ph = 2 * np.pi * (offset_cm[0] / 100.0 * np.cos(self._mode_dirs[k, 0]) + offset_cm[1] / 100.0 * np.sin(self._mode_dirs[k, 1])) / lam
        psi = self._mode_psi[k]
        return 1.0 + 0.8 * (np.cos(ph + psi) - np.cos(psi)), 1.0 + 0.003 * np.sin(ph + psi)

    def _sub_sos(self, offset_cm):
        rows = [butter(4, SAT_HP_HZ, btype="low", fs=FS, output="sos")]
        for f, g, q in SUB_SHAPE:
            rows.append(PF.biquad_sos("Peaking", f, g, q))
        for k, (f, g, q) in enumerate(self.sub_modes):
            gs, fs_ = self._spatial(k, offset_cm, f)
            rows.append(PF.biquad_sos("Peaking", f * fs_, g * gs, q))
        f, g, q = self.null
        shift = 1.0 + self.null_shift * (offset_cm[0] + 0.7 * offset_cm[1]) / 20.0
        rows.append(PF.biquad_sos("Peaking", f * shift, g, q))
        return np.vstack(rows)

    def _place(self, h, delay_ms):
        """Shift impulse response h by delay_ms (fractional) inside a fixed-length buffer."""
        n = int(IR_LEN_S * FS)
        ds = delay_ms * FS / 1000.0
        d = int(np.floor(ds)); k = _frac_delay_kernel(ds - d)
        out = np.zeros(n)
        g = np.convolve(h[:n - d - len(k)], k)
        s = d - 16
        lo = max(0, -s)
        seg = g[lo:lo + n - max(s, 0)]
        out[max(s, 0):max(s, 0) + len(seg)] = seg
        return out

    def impulse_response(self, ch, offset_cm=(0.0, 0.0)):
        """Full path DAC(ch) -> mic at a mic offset (cm; x right, y forward). Cached per (channel, offset)."""
        key = (ch, tuple(float(v) for v in offset_cm))
        if key in self._cache:
            return self._cache[key]
        n = int(IR_LEN_S * FS)
        if ch in self.dead:
            self._cache[key] = np.zeros(n); return self._cache[key]
        g = 10 ** (self.gain_db[ch] / 20) * self.polarity[ch]
        imp = np.zeros(n); imp[0] = 1.0
        total = np.zeros(n)
        if ch != "LFE":
            hp = HEIGHT_HP_HZ if ch in ("RL", "RR") else SAT_HP_HZ
            rows = [butter(2, hp, btype="high", fs=FS, output="sos")]
            if ch in ("RL", "RR"):
                rows += [PF.biquad_sos(t, f0, gg, q) for t, f0, gg, q in self.height_voicing + self.height_individual[ch] + self.height_bump[ch]]
            rows += [PF.biquad_sos(t, f0, gg, q) for t, f0, gg, q in self._ripple[ch]]
            h = sosfilt(np.vstack(rows), imp)
            td = self.physical_delay_ms(ch, offset_cm)
            direct = self._place(h, td)
            rd, ra = self._reflect[ch]
            total += g * (direct + ra * 0.6 * self._place(h, td + rd) * (-1))   # one floor/wall reflection, 8-12 dB down
            if ch not in ("RL", "RR"):
                sub = sosfilt(self._sub_sos(offset_cm), imp)
                ts = self.physical_delay_ms("LFE", offset_cm)
                total += g * 10 ** (SUB_LEVEL_DB / 20) * self._place(sub, ts)
        else:
            sub = sosfilt(self._sub_sos(offset_cm), imp)
            total += g * 10 ** (SUB_LEVEL_DB / 20) * self._place(sub, self.physical_delay_ms("LFE", offset_cm))
        self._cache[key] = total
        return total

    # ---- rendering --------------------------------------------------------------------------------------------------------------------

    def propagate(self, y8, offset_cm):
        """Sum over channels of (DAC signal * path impulse response) for the DAC signals y8 (m, 8). Output length m + IR - 1."""
        m = y8.shape[0]
        mic = np.zeros(m + int(IR_LEN_S * FS) - 1)
        for i, ch in enumerate(HW_ORDER):
            x = y8[:, i]
            if not np.any(x) or ch in self.dead:
                continue
            if ch == "LFE" and self.sub_distortion_db is not None:
                a = 10 ** (self.sub_distortion_db / 20)
                x = x + a * (x * x - np.mean(x * x)) / (np.max(np.abs(x)) + 1e-12)   # x^2/peak: a 2nd harmonic of about a*peak/2
            mic += oaconvolve(x, self.impulse_response(ch, offset_cm))
        return mic

    def finish(self, mic, rng, faults=()):
        """Mic response, clock offset, noise and injected faults, applied to the summed acoustic signal."""
        n = len(mic)
        if self.mic_response:
            mic = apply_mic_response(mic)
        mic = apply_clock_offset(mic, self.mic_ppm)
        mic = mic + self.noise(n, rng)
        for ft in faults:
            if ft["type"] == "clip":
                i0 = int(ft["t"] * FS); i1 = min(n, i0 + int(ft.get("dur", 0.2) * FS))
                if i0 < n:
                    mic[i0:i1] += 1.5 * np.sin(2 * np.pi * 300 * np.arange(i1 - i0) / FS)
            elif ft["type"] == "xrun":
                i0 = int(ft["t"] * FS); k = int(ft.get("dur", 0.01) * FS)
                if i0 + k < n:
                    mic[i0:] = np.concatenate([mic[i0 + k:], np.zeros(k)])
        return mic

    def noise(self, n, rng):
        """Pink-ish fan noise at noise_dbfs rms."""
        m = sfft.next_fast_len(n, real=True)
        X = sfft.rfft(rng.standard_normal(m)); f = sfft.rfftfreq(m, 1 / FS)
        x = sfft.irfft(X / np.sqrt(np.maximum(f, 20.0)), m)[:n]
        return x * 10 ** (self.noise_dbfs / 20) / np.sqrt(np.mean(x ** 2))


def apply_mic_response(x):
    """The measurement mic's own response: the INVERSE of tools/miccal's correction (miccal subtracts the file's dB values)."""
    path = IR.MICCAL.chosen_path()
    if path is None:
        return x
    f, db = IR.MICCAL.read_cal(path)
    nfft = sfft.next_fast_len(len(x) + FS, real=True)
    fr = sfft.rfftfreq(nfft, 1.0 / FS)
    c = np.interp(np.log10(np.maximum(fr, f[0])), np.log10(f), db)
    return sfft.irfft(sfft.rfft(x, nfft) * 10.0 ** (c / 20.0), nfft)[:len(x)]


def apply_clock_offset(x, ppm):
    """Resamples x as a microphone clock running `ppm` fast would (Catmull-Rom interpolation): sample n is taken at source index n/(1+ppm)."""
    if abs(ppm) < 1e-9:
        return x
    n = len(x)
    pos = np.arange(n) / (1.0 + ppm * 1e-6)
    i = np.floor(pos).astype(np.int64); mu = pos - i
    i = np.clip(i, 1, n - 3)
    p0, p1, p2, p3 = x[i - 1], x[i], x[i + 1], x[i + 2]
    return 0.5 * (2 * p1 + (-p0 + p2) * mu + (2 * p0 - 5 * p1 + 4 * p2 - p3) * mu ** 2 + (-p0 + 3 * p1 - 3 * p2 + p3) * mu ** 3)


def active_windows(x, gap_s=0.4, fs=FS):
    """Index windows [a, b) covering the non-zero parts of x (n, 8), merging parts closer than gap_s. Lets the simulator skip the silences."""
    act = np.flatnonzero(np.any(x != 0, axis=1))
    if len(act) == 0:
        return []
    cuts = np.flatnonzero(np.diff(act) > gap_s * fs)
    starts = np.concatenate([[act[0]], act[cuts + 1]]); ends = np.concatenate([act[cuts], [act[-1]]]) + 1
    return list(zip(starts.tolist(), ends.tolist()))
