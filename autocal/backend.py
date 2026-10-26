"""Backend interface: how autocal plays a multichannel stimulus and records the microphone.

A Backend plays an (n, 8) float array in hardware order (FL FR RL RR FC LFE SL SR) and records one microphone channel for the same time,
streaming the recording to a `monitor` callback in blocks of BLOCK_S seconds. The callback may return a reason string, which makes the backend
stop playback immediately (the abort path: it must complete within ABORT_BUDGET_S of the event that caused it).

* `autocal.sim.backend.SimBackend`    simulated room, no audio, virtual time (tested in the sandbox).
* `autocal.pipewire.PipewireBackend`  pw-play / pw-record (UNTESTED ON HARDWARE).
"""
from dataclasses import dataclass, field

import numpy as np

from .stimulus import assert_cap

FS = 48000
BLOCK_S = 0.05            # monitor granularity
ABORT_BUDGET_S = 0.25     # playback must be stopped within this time of an abort event


class BackendError(RuntimeError):
    """Preflight or I/O problem (mic missing, another stream on the sink, process died)."""


@dataclass
class Capture:
    """Result of one play+record call. `x` is the raw mono mic recording (mic calibration is applied later, in analysis)."""
    x: np.ndarray
    fs: int = FS
    aborted: str = None            # reason, or None
    trigger_t: float = None        # recording time (s) at which the monitor asked for the abort
    stop_t: float = None           # recording time (s) at which playback was actually stopped
    play_start_t: float = 0.0      # approximately when playback began in the recording
    info: dict = field(default_factory=dict)

    @property
    def duration(self):
        return len(self.x) / self.fs


class Backend:
    """Base class. Subclasses implement preflight, current_room, record_noise, play_record, set_position."""
    simulated = False
    name = "backend"

    def preflight(self):
        """dict: mic (str or None), other_streams (list of str), profile (str), notes (list)."""
        raise NotImplementedError

    def current_room(self):
        """The room.json that is live in CamillaDSP now."""
        raise NotImplementedError

    def set_position(self, index, offset_cm):
        """Tell the backend the mic was moved (the simulator changes its geometry; real hardware ignores it)."""

    def record_noise(self, seconds, monitor=None):
        raise NotImplementedError

    def play_record(self, x8, monitor=None, stage="play", pre_s=1.0, post_s=1.0):
        """Plays x8 (asserting the level cap first) and records. Returns a Capture."""
        raise NotImplementedError

    @staticmethod
    def check_cap(x8):
        assert_cap(x8, what="backend playback")

    def close(self):
        pass
