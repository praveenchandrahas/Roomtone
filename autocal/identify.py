"""Identify one speaker: a short, quiet tone on that ONE channel, so the person can check which speaker sits on which output port.

Safety: peak at most IDENTIFY_MAX_DBFS (-40 dBFS, 20 dB below autocal's own -20 dBFS cap), at most IDENTIFY_MAX_S (2 s), one channel only, with 50 ms fades.
It is played through CamillaDSP (the same path as autocal's stimulus, so the high-pass and the other protections still apply). Nothing here changes any setting.
"""
import os
import subprocess
import tempfile
import time

import numpy as np

from . import ir as IR, stimulus as ST
from .stimulus import FS, assert_cap, db_to_amp

IDENTIFY_MAX_DBFS = -40.0
IDENTIFY_MAX_S = 2.0


class IdentifyError(ValueError):
    pass


def tone(name, level_db=IDENTIFY_MAX_DBFS, seconds=IDENTIFY_MAX_S, fs=FS):
    """(n, ports) array with a tone on speaker `name` only (the active layout). Raises IdentifyError for an unknown name or a level/length over the limits."""
    if name not in ST.COL:
        raise IdentifyError(f"unknown speaker {name!r}; this layout has: {', '.join(ST.RESULT_ORDER)}")
    if not level_db <= IDENTIFY_MAX_DBFS:
        raise IdentifyError(f"level {level_db} dBFS is over the {IDENTIFY_MAX_DBFS:.0f} dBFS limit for the identify tone")
    if not 0.2 <= seconds <= IDENTIFY_MAX_S:
        raise IdentifyError(f"length {seconds} s is outside 0.2 to {IDENTIFY_MAX_S:.0f} s")
    n = int(seconds * fs)
    t = np.arange(n) / fs
    if name == ST.SUB:
        y = 0.6 * np.sin(2 * np.pi * 50 * t) + 0.4 * np.sin(2 * np.pi * 100 * t)      # 50 and 100 Hz: audible without being a rumble
    else:
        y = np.sin(2 * np.pi * 500 * t)
    ramp = int(0.05 * fs)
    env = np.ones(n); env[:ramp] = np.linspace(0, 1, ramp); env[-ramp:] = np.linspace(1, 0, ramp)
    x = np.zeros((n, ST.NCH))
    x[:, ST.COL[name]] = y * env * db_to_amp(level_db)
    assert_cap(x, cap=IDENTIFY_MAX_DBFS, what="identify tone")
    return x


def play(name, level_db=IDENTIFY_MAX_DBFS, seconds=IDENTIFY_MAX_S, backend=None):
    """Plays the tone through the live backend (PipeWire) and returns when it has ended. `backend` is for tests."""
    x = tone(name, level_db, seconds)
    if backend is None:
        from .pipewire import PipewireBackend
        backend = PipewireBackend()
    wav = os.path.join(tempfile.mkdtemp(prefix="autocal-id-"), "identify.wav")
    IR.write_wav24(wav, x)
    proc = backend._start_player(wav)
    try:
        t_end = time.monotonic() + seconds + 1.0
        while proc.poll() is None and time.monotonic() < t_end:
            time.sleep(0.05)
    finally:
        backend._stop(proc)
