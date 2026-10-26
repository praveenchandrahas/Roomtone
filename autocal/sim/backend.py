"""SimBackend: plays through the simulated CamillaDSP profile and the simulated room, in virtual time (no sleeping unless speed > 0)."""
import copy

import numpy as np

from .. import profile as PF
from ..backend import Backend, Capture, BLOCK_S, FS
from .room import SimRoom, active_windows


class SimBackend(Backend):
    """Test/simulation backend.

    room         SimRoom (ground truth)
    profile      the room.json that is "live in CamillaDSP" (default: set-eq's defaults); change with set_profile() to simulate applying a proposal
    faults       list of {type: clip|xrun, t: seconds into the recording, dur, stage: None|name}. 'clip' adds a loud burst at the mic
    other_streams / mic_present   preflight conditions
    speed        virtual seconds per wall second (0 = as fast as possible); useful for watching the GUI
    stop_latency_s   modelled time between the abort decision and silence (pw-play kill + buffer)
    """
    simulated = True
    name = "simulated room"

    def __init__(self, room=None, profile=None, seed=0, faults=(), other_streams=(), mic_present=True, speed=0.0, stop_latency_s=0.03):
        self.room = room or SimRoom(seed=seed)
        self.profile = copy.deepcopy(profile) if profile is not None else PF.default_room()
        self.seed, self.faults = seed, list(faults)
        self.other_streams, self.mic_present, self.speed, self.stop_latency_s = list(other_streams), mic_present, speed, stop_latency_s
        self.offset_cm = (0.0, 0.0)
        self.callbacks = []                    # (t, stage or None, fn)
        self.calls = 0
        self.latency_s = 0.037
        self.log = []
        self.last = None                       # the most recent Capture (tests read abort timing from it)

    # -- state -------------------------------------------------------------------------------------------------------------------------
    def preflight(self):
        return {"mic": "sim-mic (iMM-6S model)" if self.mic_present else None, "other_streams": list(self.other_streams),
                "profile": "simulated CamillaDSP profile", "notes": ["simulated: no audio hardware is touched"]}

    def current_room(self):
        return copy.deepcopy(self.profile)

    def set_profile(self, room):
        self.profile = copy.deepcopy(room)

    def set_position(self, index, offset_cm):
        self.offset_cm = tuple(offset_cm)

    def at(self, t, fn, stage=None):
        """Run fn() when the recording time of a call (in `stage`, default any) passes t. Used by tests to simulate a user pressing abort."""
        self.callbacks.append((t, stage, fn))

    # -- audio -------------------------------------------------------------------------------------------------------------------------
    def record_noise(self, seconds, monitor=None):
        return self.play_record(np.zeros((int(seconds * FS), 8)), monitor, stage="noise", pre_s=0.0, post_s=0.0)

    def play_record(self, x8, monitor=None, stage="play", pre_s=1.0, post_s=1.0):
        self.check_cap(x8)
        self.calls += 1
        rng = np.random.default_rng(self.seed * 7919 + self.calls)
        lead = int((pre_s + self.latency_s) * FS)
        xin = np.concatenate([np.zeros((lead, 8)), x8, np.zeros((int(post_s * FS), 8))])
        n = len(xin)
        acoustic = np.zeros(n)
        for a, b in active_windows(xin):                       # only the parts with signal are filtered and convolved
            seg = np.concatenate([xin[a:b], np.zeros((int(0.3 * FS), 8))])
            m = self.room.propagate(PF.apply_profile(self.profile, seg), self.offset_cm)
            k = min(len(m), n - a)
            acoustic[a:a + k] += m[:k]
        faults = [f for f in self.faults if f.get("stage") in (None, stage)]
        mic = self.room.finish(acoustic, rng, faults)
        mic = np.clip(np.round(mic * 2 ** 23) / 2 ** 23, -1.0, 1.0)       # 24-bit quantisation and hard clipping
        nb = int(BLOCK_S * FS)
        cap = Capture(x=mic, fs=FS, play_start_t=pre_s + self.latency_s, info={"stage": stage})
        pending = sorted([c for c in self.callbacks if c[1] in (None, stage)], key=lambda c: c[0])
        for i in range(0, len(mic), nb):
            blk = mic[i:i + nb]; t = (i + len(blk)) / FS
            while pending and pending[0][0] <= t:
                pending.pop(0)[2]()
            if self.speed > 0:
                import time
                time.sleep(BLOCK_S / self.speed)
            if monitor is not None:
                why = monitor(t, blk)
                if why:
                    cap.aborted, cap.trigger_t = why, t
                    cap.stop_t = t + self.stop_latency_s
                    cap.x = mic[:min(len(mic), int(cap.stop_t * FS))]
                    self.last = cap
                    return cap
        cap.stop_t = len(mic) / FS
        self.last = cap
        return cap
