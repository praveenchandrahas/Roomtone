"""Run-time plumbing shared by the runner and the backends: control files, atomic status.json, the live safety monitor.

Control surface (schema section 2): the GUI/CLI writes empty files `control/continue-<prompt id>` and `control/abort`; the core polls them at
least every 0.25 s, and the SafetyMonitor polls the abort file on EVERY block (50 ms) so playback stops within the 250 ms budget.
"""
import json
import os
import time
from datetime import datetime, timezone

import numpy as np

from .backend import BLOCK_S, FS

PEAK_ABORT_DBFS = -6.0          # mic peak above this stops playback
CLIP_LEVEL = 0.999              # |sample| at or above this counts as clipped
HARMONIC_ABORT_DBC = -20.0      # sub second harmonic above this stops an LFE sweep


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_write_json(path, obj):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


class Control:
    """The control/ directory of a run."""

    def __init__(self, run_dir):
        self.dir = os.path.join(run_dir, "control")
        os.makedirs(self.dir, exist_ok=True)

    def aborted(self):
        return os.path.exists(os.path.join(self.dir, "abort"))

    def continued(self, prompt_id):
        return os.path.exists(os.path.join(self.dir, f"continue-{prompt_id}"))

    def press_continue(self, prompt_id):
        open(os.path.join(self.dir, f"continue-{prompt_id}"), "w").close()

    def request_abort(self):
        open(os.path.join(self.dir, "abort"), "w").close()


class StatusWriter:
    """Owns status.json. `update` merges fields; `flush` writes atomically. `tick` writes at most every `min_interval` s (the GUI polls 2 Hz)."""

    def __init__(self, run_dir, run_id, mode, simulated, positions_total, min_interval=0.2):
        self.path = os.path.join(run_dir, "status.json")
        self.min_interval = min_interval
        self._last = 0.0
        self.s = {"id": run_id, "state": "idle", "mode": mode, "position": 1, "positions_total": positions_total, "step": "", "progress": 0.0,
                  "message": "", "mic": {"peak_dbfs": None, "rms_dbfs": None, "noise_floor_dbfs": None}, "level_dbfs": None, "prompt": None,
                  "simulated": simulated, "error": None, "updated": now_iso()}

    def update(self, force=True, **kw):
        mic = kw.pop("mic", None)
        self.s.update(kw)
        if mic:
            self.s["mic"].update(mic)
        if force:
            self.flush()
        else:
            self.tick()

    def flush(self):
        self.s["updated"] = now_iso()
        atomic_write_json(self.path, self.s)
        self._last = time.monotonic()

    def tick(self):
        if time.monotonic() - self._last >= self.min_interval:
            self.flush()


def dbfs(v):
    return float(20 * np.log10(max(float(v), 1e-9)))


class SafetyMonitor:
    """Callable used as the backends' monitor. Returns an abort reason (str) or None for each recorded block.

    Aborts on: the control/abort file, clipped mic samples, mic peak above PEAK_ABORT_DBFS, and (during LFE sweeps given in `lfe_sweeps`)
    a second harmonic above HARMONIC_ABORT_DBC measured at the mic. Also keeps the live meter (peak/rms) for status.json.
    `lfe_sweeps`: list of dicts {t0 (s, relative to the start of the recording), T, f0, f1}.
    """

    def __init__(self, control=None, status=None, peak_abort=PEAK_ABORT_DBFS, check_clipping=True, lfe_sweeps=(), level_dbfs=None):
        self.control, self.status = control, status
        self.peak_abort, self.check_clipping = peak_abort, check_clipping
        self.lfe_sweeps = list(lfe_sweeps)
        self.level_dbfs = level_dbfs
        self.ring = np.zeros(int(0.3 * FS)); self.nring = 0
        self.max_peak = 0.0
        self.reason = None

    def __call__(self, t, block):
        if self.control is not None and self.control.aborted():
            return self._stop("user abort (control/abort)")
        if len(block):
            pk = float(np.max(np.abs(block))); self.max_peak = max(self.max_peak, pk)
            if self.check_clipping and pk >= CLIP_LEVEL:
                return self._stop(f"mic clipping ({int(np.sum(np.abs(block) >= CLIP_LEVEL))} clipped samples at t={t:.2f} s)")
            if dbfs(pk) > self.peak_abort:
                return self._stop(f"mic peak {dbfs(pk):.1f} dBFS above the {self.peak_abort:.0f} dBFS limit at t={t:.2f} s")
            n = len(block)
            self.ring = np.concatenate([self.ring[n:], block]) if n < len(self.ring) else block[-len(self.ring):]
            self.nring += n
            if self.status is not None:
                self.status.update(force=False, mic={"peak_dbfs": round(dbfs(pk), 1), "rms_dbfs": round(dbfs(np.sqrt(np.mean(block ** 2))), 1)})
        for sw in self.lfe_sweeps:
            if sw["t0"] + 1.0 < t < sw["t0"] + sw["T"]:
                f = sw["f0"] * (sw["f1"] / sw["f0"]) ** ((t - 0.1 - sw["t0"]) / sw["T"])
                if 28.0 <= f <= 100.0 and self.nring >= len(self.ring):
                    r = harmonic_dbc(self.ring, f)
                    if r is not None and r > HARMONIC_ABORT_DBC:
                        return self._stop(f"sub second harmonic {r:.1f} dBc at {f:.0f} Hz (limit {HARMONIC_ABORT_DBC:.0f} dBc)")
        return None

    def _stop(self, why):
        self.reason = why
        return why


def harmonic_dbc(x, f, fs=FS):
    """Level of the 2nd harmonic relative to the fundamental over the buffer x (single-bin DFTs, Hann window); None if no fundamental."""
    n = len(x); w = np.hanning(n); t = np.arange(n) / fs
    a1 = abs(np.sum(x * w * np.exp(-2j * np.pi * f * t))); a2 = abs(np.sum(x * w * np.exp(-2j * np.pi * 2 * f * t)))
    if a1 <= 1e-9:
        return None
    return float(20 * np.log10(a2 / a1 + 1e-12))
