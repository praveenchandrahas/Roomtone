"""The CamillaDSP profile as autocal sees it: room.json (schema section 1), its filter sets, and its signal processing.

autocal measures THROUGH the current profile and proposes residual changes (new = now + residual). For that it must know what the profile does:
`bass_filters` (the base set + extras on the Z906 channels), the heights' high-pass / tone / trim and bass-to-LFE management, and the
per-channel delay and trim. `apply_profile` is a faithful (not bit-exact) time-domain model used by the simulator; `response` gives the
complex frequency response of one channel's profile for the analysis.

The numbers in DEFAULT_ROOM / BASE_SETS are copies of set-eq.py's module defaults (ALIGN, HEIGHT_TONE, drone2 = punch20x + two taste cuts).
the sim/analysis tests compare them with `set-eq.py --dump-room` and FILTER_SETS when the sibling repo is present, so they cannot
drift silently. The live room.json is read from AUTOCAL_ROOM (default ~/.config/camilladsp/room.json), else set-eq's dump, else this copy.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
from scipy.signal import sosfilt, sosfreqz, butter

from .stimulus import HW_ORDER, RESULT_ORDER

FS = 48000
BASS_CH = ["FL", "FR", "FC", "LFE", "SL", "SR"]       # set-eq CHANNELS = [0,1,4,5,6,7]
HEIGHTS_HP_HZ = 140.0                                 # set-eq --heights default; heights' bass goes (low-passed) to the LFE
LIVE_ROOM_DEFAULT = "~/.config/camilladsp/room.json"

DEFAULT_ROOM = {
    "version": 1,
    "align": {"FL": {"delay_ms": 0.2, "trim_db": 0.0}, "FR": {"delay_ms": 0.0, "trim_db": -1.9}, "FC": {"delay_ms": 0.97, "trim_db": -0.8},
              "LFE": {"delay_ms": 0.2, "trim_db": 0.0}, "SL": {"delay_ms": 3.0, "trim_db": -4.4}, "SR": {"delay_ms": 2.72, "trim_db": -0.7},
              "RL": {"delay_ms": 4.6, "trim_db": -0.6}, "RR": {"delay_ms": 4.7, "trim_db": -0.3}},
    "height_tone": {"filters": [{"name": "ht_310", "type": "Peaking", "freq": 310, "gain": -7.5, "q": 1.0},
                                {"name": "ht_1700", "type": "Peaking", "freq": 1700, "gain": -7.0, "q": 2.5},
                                {"name": "ht_hs", "type": "Highshelf", "freq": 6000, "gain": 2.5, "q": 0.8}], "trim_db": 1.0},
    "bass": {"base_set": "drone2", "extra_filters": []},
}

_PUNCH20X = [("q20_1", 47.06, -10.26, 5.0), ("q20_2", 60.66, -2.51, 5.0), ("q20_3", 85.3, -4.87, 5.0), ("q20_4", 107.09, -4.06, 5.0),
             ("q20_5", 142.27, -1.82, 5.0), ("q20_6", 154.88, -5.44, 4.99), ("q20_7", 176.64, -3.97, 5.0), ("q20_8", 250.37, -5.01, 5.0)]
BASE_SETS = {"punch20x": _PUNCH20X,
             "drone2": _PUNCH20X + [("d2_1", 72.0, -6.0, 8.0), ("d2_2", 84.0, -6.0, 4.0)]}


class RoomError(ValueError):
    pass


def default_room():
    return copy.deepcopy(DEFAULT_ROOM)


def validate_room(room):
    """Raises RoomError unless `room` has the shape of schema section 1 (all 8 channels, numeric fields)."""
    try:
        if room.get("version") != 1:
            raise RoomError("room.json version must be 1")
        for ch in RESULT_ORDER:
            a = room["align"][ch]
            float(a["delay_ms"]); float(a["trim_db"])
            if a["delay_ms"] < 0:
                raise RoomError(f"{ch}: negative delay")
        for f in room["height_tone"]["filters"] + room["bass"].get("extra_filters", []):
            float(f["freq"]); float(f["gain"]); float(f["q"])
        float(room["height_tone"]["trim_db"])
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise RoomError(f"invalid room.json: {e}") from e
    return room


def load_room(path):
    with open(path) as f:
        return validate_room(json.load(f))


def save_room(room, path):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(room, f, indent=2)
    os.replace(tmp, path)


def live_room_path():
    return os.path.expanduser(os.environ.get("AUTOCAL_ROOM", LIVE_ROOM_DEFAULT))


def find_set_eq():
    """Path of set-eq.py: $SET_EQ, else the sibling-workspace convention (../linux-theater-dsp/scripts/set-eq.py), else None."""
    cands = [os.environ.get("SET_EQ", "")]
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.normpath(os.path.join(here, "..", "..", "linux-theater-dsp", "scripts", "set-eq.py")))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def current_room():
    """The room that is live now: the live room.json if it exists, else `set-eq.py --dump-room`, else the embedded defaults."""
    p = live_room_path()
    if os.path.isfile(p):
        return load_room(p)
    se = find_set_eq()
    if se:
        try:
            r = subprocess.run([sys.executable, se, "--dump-room"], capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return validate_room(json.loads(r.stdout))
        except (OSError, ValueError, subprocess.SubprocessError, RoomError):
            pass
    return default_room()


def set_eq_filter_sets():
    """FILTER_SETS imported from the sibling set-eq.py (for the consistency test), or None."""
    se = find_set_eq()
    if not se:
        return None
    sys.path.insert(0, os.path.dirname(se))
    try:
        spec = importlib.util.spec_from_file_location("set_eq_mod", se)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m.FILTER_SETS
    finally:
        sys.path.pop(0)


def bass_filters(room):
    """All bass filters now live on the Z906 channels: base set + extras, as dicts {name, freq, gain, q}."""
    b = room["bass"]
    base = [dict(name=n, freq=f, gain=g, q=q) for n, f, g, q in BASE_SETS[b.get("base_set", "drone2")]]
    return base + [dict(f) for f in b.get("extra_filters", [])]


# ---- biquads ----------------------------------------------------------------------------------------------------------------------------

def biquad_sos(kind, f0, gain_db, q, fs=FS):
    """RBJ cookbook biquad as one second-order section. kind: Peaking | Highshelf."""
    A = 10 ** (gain_db / 40); w0 = 2 * np.pi * f0 / fs; al = np.sin(w0) / (2 * q); c = np.cos(w0)
    if kind == "Peaking":
        b = (1 + al * A, -2 * c, 1 - al * A); a = (1 + al / A, -2 * c, 1 - al / A)
    elif kind == "Highshelf":
        sq = 2 * np.sqrt(A) * al
        b = (A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq))
        a = ((A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq)
    else:
        raise ValueError(f"unsupported filter type {kind}")
    return np.array([[b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]]])


def sos_response(sos, f, fs=FS):
    """Complex response of an sos cascade at frequencies f (Hz)."""
    f = np.maximum(np.asarray(f, float), 1e-3)
    return sosfreqz(sos, worN=f, fs=fs)[1]


def peaking_db(f, f0, gain_db, q):
    """Magnitude (dB) of an RBJ peaking filter at f (array)."""
    return 20 * np.log10(np.abs(sos_response(biquad_sos("Peaking", f0, gain_db, q), f)) + 1e-30)


def filters_db(f, filters, kind="Peaking"):
    """Summed magnitude in dB of a list of filter dicts (type defaults to Peaking)."""
    out = np.zeros(len(f))
    for p in filters:
        out += 20 * np.log10(np.abs(sos_response(biquad_sos(p.get("type", kind), p["freq"], p["gain"], p["q"]), f)) + 1e-30)
    return out


def lr4_sos(fc, hp):
    s = butter(2, fc, btype="high" if hp else "low", fs=FS, output="sos")
    return np.vstack([s, s])


def delay_samples(ms):
    """CamillaDSP Delay with subsample:false works in whole samples."""
    return int(round(ms * FS / 1000.0))


def tone_sos(room):
    rows = [biquad_sos(p.get("type", "Peaking"), p["freq"], p["gain"], p["q"]) for p in room["height_tone"]["filters"]]
    return np.vstack(rows) if rows else np.zeros((0, 6))


def response(room, ch, f, parts=("bass", "tone", "hp", "trim", "delay")):
    """Complex response of the profile on channel `ch` (name) at frequencies f. Heights' bass-to-LFE path is NOT included (it is a cross-feed)."""
    f = np.asarray(f, float); h = np.ones(len(f), complex)
    a = room["align"][ch]
    if "bass" in parts and ch in BASS_CH:
        fl = bass_filters(room)
        if fl:
            h = h * np.prod([sos_response(biquad_sos("Peaking", p["freq"], p["gain"], p["q"]), f) for p in fl], axis=0)
    if ch in ("RL", "RR"):
        if "hp" in parts:
            h = h * sos_response(lr4_sos(HEIGHTS_HP_HZ, True), f)
        if "tone" in parts and len(tone_sos(room)):
            h = h * sos_response(tone_sos(room), f)
        if "trim" in parts:
            h = h * 10 ** (room["height_tone"]["trim_db"] / 20)
    if "trim" in parts:
        h = h * 10 ** (a["trim_db"] / 20)
    if "delay" in parts:
        h = h * np.exp(-2j * np.pi * f * delay_samples(a["delay_ms"]) / FS)
    return h


def apply_profile(room, x, fs=FS):
    """Time-domain model of the CamillaDSP profile for the simulator. x: (n, 8) in hardware order. Returns an array of the same shape.

    Order as in set-eq.py build_config: heights stage (high-pass, tone, trim; the summed heights low-passed and added to LFE),
    bass filters on FL FR FC LFE SL SR, then per-channel delay and trim."""
    idx = {n: i for i, n in enumerate(HW_ORDER)}
    n = x.shape[0]; y = x.astype(np.float64).copy()
    if np.any(x[:, idx["RL"]]) or np.any(x[:, idx["RR"]]):
        bus = sosfilt(lr4_sos(HEIGHTS_HP_HZ, False), x[:, idx["RL"]] + x[:, idx["RR"]])
        for h in ("RL", "RR"):
            v = sosfilt(lr4_sos(HEIGHTS_HP_HZ, True), x[:, idx[h]])
            ts = tone_sos(room)
            if len(ts):
                v = sosfilt(ts, v)
            y[:, idx[h]] = v * 10 ** (room["height_tone"]["trim_db"] / 20)
        y[:, idx["LFE"]] += bus
    fl = bass_filters(room)
    if fl:
        sos = np.vstack([biquad_sos("Peaking", p["freq"], p["gain"], p["q"]) for p in fl])
        for c in BASS_CH:
            if np.any(y[:, idx[c]]):
                y[:, idx[c]] = sosfilt(sos, y[:, idx[c]])
    for c in HW_ORDER:
        a = room["align"][c]; i = idx[c]
        d = delay_samples(a["delay_ms"])
        if d > 0:
            y[:, i] = np.concatenate([np.zeros(d), y[:n - d, i]])
        y[:, i] *= 10 ** (a["trim_db"] / 20)
    return y


def merge_filters(old, new, tol_oct=0.03, max_cut=-15.0):
    """Appends the residual filters `new` to `old`; a new filter within tol_oct of an old one and a similar Q is folded into it (gains add)."""
    out = [dict(f) for f in old]
    for nf in new:
        for o in out:
            if abs(np.log2(nf["freq"] / o["freq"])) < tol_oct and 0.5 < nf["q"] / o["q"] < 2:
                o["gain"] = round(max(max_cut, o["gain"] + nf["gain"]), 2)
                break
        else:
            out.append(dict(nf))
    return [f for f in out if abs(f["gain"]) >= 0.05]
