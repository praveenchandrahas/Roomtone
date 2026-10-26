"""The measurement run: preflight -> noise floor -> level ramp -> channel ID -> one or five positions -> analysis -> results.

Writes the run directory of schema section 2 (run.json, status.json, control/, results.json, room.proposed.json, config.proposed.yml,
report.html, details.json, raw/*.wav for real runs). Never changes the live system: `apply` is a separate, explicit command (install.py).
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from . import analysis as AN, ir as IR, measure as M, profile as PF, results as RS, safety as SF, stimulus as ST
from .backend import FS, BackendError

POLL_S = 0.1


class RunAborted(Exception):
    """The run was stopped (user, safety monitor, failed gate). `kind` = 'aborted' or 'error'."""

    def __init__(self, reason, kind="aborted"):
        super().__init__(reason)
        self.reason, self.kind = reason, kind


@dataclass
class RunConfig:
    mode: str = "single"
    runs_dir: str = "runs"
    sweep_s: float = 7.0
    tail_s: float = 2.0
    noise_s: float = 6.0
    target: str = "owner_hump"
    hump_db: float = 20.0
    dry: bool = False
    check_only: bool = False
    auto_continue: bool = False
    prompt_timeout_s: float = None
    allow_boost: bool = False
    interactive_stdin: bool = False
    out: object = None                       # file-like for terminal messages (default stdout)
    fixed_level_db: float = None             # skip the ramp's sweep-level decision (tests)


def new_run_id(runs_dir):
    """YYYYMMDD-HHMMSS, bumped by a second if that directory exists."""
    t = datetime.now()
    while True:
        rid = t.strftime("%Y%m%d-%H%M%S")
        if not os.path.exists(os.path.join(runs_dir, rid)):
            return rid
        t = datetime.fromtimestamp(t.timestamp() + 1)


class Run:
    def __init__(self, cfg, backend):
        self.cfg, self.be = cfg, backend
        self.out = cfg.out or sys.stdout
        os.makedirs(cfg.runs_dir, exist_ok=True)
        self.id = new_run_id(cfg.runs_dir)
        self.dir = os.path.join(cfg.runs_dir, self.id)
        os.makedirs(os.path.join(self.dir, "raw"), exist_ok=True)
        self.control = SF.Control(self.dir)
        self.positions = RS.POSITION_LABELS if cfg.mode == "five" else RS.POSITION_LABELS[:1]
        self.status = SF.StatusWriter(self.dir, self.id, cfg.mode, backend.simulated, len(self.positions))
        self.details = {"stages": {}}
        self.created = SF.now_iso()
        self.progress0 = 0.0

    # ---- helpers ----------------------------------------------------------------------------------------------------------------------
    def say(self, s):
        print(s, file=self.out, flush=True)

    def st(self, state, step="", message="", progress=None, **kw):
        if progress is not None:
            kw["progress"] = float(min(1.0, max(0.0, progress)))
        self.status.update(state=state, step=step, message=message, **kw)

    def monitor(self, **kw):
        return SF.SafetyMonitor(control=self.control, status=self.status, **kw)

    def check_abort(self):
        if self.control.aborted():
            raise RunAborted("aborted by the user (control/abort)")

    def play(self, x8, stage, mon, pre_s=1.0, post_s=1.0):
        """Backend play+record with abort handling. Returns the Capture; raises RunAborted if the monitor stopped playback."""
        self.check_abort()
        cap = self.be.play_record(x8, mon, stage=stage, pre_s=pre_s, post_s=post_s)
        if cap.aborted:
            raise RunAborted(f"playback stopped: {cap.aborted}")
        return cap

    # ---- steps ------------------------------------------------------------------------------------------------------------------------
    def start(self):
        room = self.be.current_room()
        self.room = PF.validate_room(room)
        run = {"id": self.id, "created": self.created, "mode": self.cfg.mode, "simulated": self.be.simulated,
               "target": {"name": self.cfg.target, "hump_db": self.cfg.hump_db}, "room_before": self.room, "version": 1}
        RS.write_json(os.path.join(self.dir, "run.json"), run)
        self.st("idle", "start", "starting", 0.0)

    def preflight(self):
        self.st("preflight", "preflight", "checking microphone and audio streams", 0.02)
        pf = self.be.preflight()
        self.details["preflight"] = pf
        if not pf.get("mic"):
            raise RunAborted("no measurement microphone found", "error")
        if pf.get("other_streams"):
            raise RunAborted("another audio stream is active on the output (" + ", ".join(pf["other_streams"]) + "): close mpv/Chrome and retry", "error")
        if pf.get("mic_muted"):
            raise RunAborted(f"the microphone {pf['mic']} is muted in PipeWire: unmute it and set the level you calibrated (./rec.sh gain 60%)", "error")
        lvl = pf.get("mic_level_pct")
        self.say(f"preflight: mic {pf['mic']}" + (f" (capture level {lvl:.0f}%)" if lvl is not None else "") + f"; profile {pf.get('profile')}")
        nf = self.be.record_noise(self.cfg.noise_s, self.monitor(check_clipping=False))
        if nf.aborted:
            raise RunAborted(f"noise floor: {nf.aborted}")
        floor = SF.dbfs(np.sqrt(np.mean(nf.x ** 2)))
        self.floor = floor
        self.noise_x = nf.x
        self.details["noise_floor_dbfs"] = floor
        self.status.update(mic={"noise_floor_dbfs": round(floor, 1)})
        if floor > -35.0:
            raise RunAborted(f"noise floor {floor:.0f} dBFS is too high to measure (fan, neighbours, or mic gain)", "error")
        frames = nf.x[:len(nf.x) // 4800 * 4800].reshape(-1, 4800)
        # the ramp is read as the loudest 100 ms frame of the mic, so it is compared with the loudest 100 ms frame of the noise (like with like)
        self.floor_frame = SF.dbfs(np.sqrt(np.max(np.mean(frames ** 2, 1)))) if len(frames) else floor
        self.details["noise_floor_loudest_frame_dbfs"] = self.floor_frame
        self.say(f"noise floor {floor:.1f} dBFS rms (loudest 100 ms frame {self.floor_frame:.1f})")

    def ramp(self):
        self.st("ramp", "level ramp", "finding the playback level", 0.06)
        steps = []; peaks = []; reports = []
        for lv in ST.ramp_levels():
            self.check_abort()
            burst = ST.pink_burst(lv)
            x = np.zeros((int(1.6 * FS), 8)); x[int(0.3 * FS):int(0.3 * FS) + len(burst), 0] = burst
            self.st("ramp", "level ramp", f"FL burst at {lv:.0f} dBFS", level_dbfs=lv)
            cap = self.play(x, "ramp", self.monitor(), pre_s=0.3, post_s=0.3)
            rep = ST.burst_report(cap.x, burst, self.floor, noise=self.noise_x)
            c = rep["coherent_snr_db"] if rep["detected"] else 0.0
            steps.append((lv, c)); peaks.append(SF.dbfs(np.max(np.abs(cap.x)))); reports.append(rep)
            found = (f"burst found at +{rep['lag_s'] - 0.3:.2f} s, correlation {rep['coherent_snr_db']:.0f} dB" if rep["detected"]
                     else f"burst NOT found (correlation {rep['coherent_snr_db']:.0f} dB; noise alone gives about 13, finding needs 18)")
            self.say(f"  ramp {lv:6.1f} dBFS -> {found}")
            try:                                   # keep the mic recording of every step: it is how a silent or misaligned burst is diagnosed afterwards
                IR.write_wav24(os.path.join(self.dir, f"ramp_{lv:+05.1f}dBFS.wav".replace("+", "p").replace("-", "m")), cap.x)
            except OSError:
                pass
            if c >= ST.RAMP_COH_TARGET_DB:
                break
        lv, coh, reached = ST.ramp_decision_coh(steps)
        snr = coh
        self.ramp_steps = steps
        self.ramp_level = lv
        stop_peak = peaks[[a for a, _ in steps].index(lv)]
        self.sweep_level = self.cfg.fixed_level_db if self.cfg.fixed_level_db is not None else ST.sweep_level_from_ramp(lv, stop_peak)
        top = reports[-1]
        exp_ir = ST.expected_ir_snr_db(coh, self.cfg.sweep_s, self.sweep_level, lv) if reports[[a for a, _ in steps].index(lv)]["detected"] else 0.0
        self.details["ramp"] = {"steps": [[a, round(b, 1)] for a, b in steps], "stop_level_dbfs": lv, "snr_db": round(snr, 1), "reached_target": reached,
                                "sweep_level_dbfs": self.sweep_level, "mic_peak_at_stop_dbfs": round(stop_peak, 1),
                                "burst_found": [bool(r["detected"]) for r in reports], "correlation_db": [round(r["coherent_snr_db"], 1) for r in reports],
                                "expected_ir_snr_db": round(exp_ir, 1)}
        if not reached:
            self.details["ramp"]["note"] = f"correlation never reached {ST.RAMP_COH_TARGET_DB:.0f} dB up to the {ST.CAP_DBFS:.0f} dBFS cap"
            if not top["detected"]:
                raise RunAborted(f"the microphone did not pick up the bursts at all, not even by correlation ({top['coherent_snr_db']:.0f} dB; noise alone gives about 13) "
                                 f"up to the {ST.CAP_DBFS:.0f} dBFS cap. Either the bursts are not reaching the speakers (were they audible?) or the mic does not hear them "
                                 f"(muted, wrong input, level). Run 'python3 -m autocal mic' and tap the mic; the ramp recordings are in the run folder", "error")
            if exp_ir < ST.EXPECTED_IR_MIN_DB:
                raise RunAborted(f"too quiet to measure: at the {ST.CAP_DBFS:.0f} dBFS cap the burst correlates at {coh:.0f} dB, so the sweeps would reach only about "
                                 f"{exp_ir:.0f} dB peak-to-noise (needs {ST.EXPECTED_IR_MIN_DB:.0f}). Raise the Z906 volume a notch (or reduce the room noise, e.g. the laptop fan) and retry", "error")
            if exp_ir < ST.EXPECTED_IR_OK_DB:
                self.say(f"WARNING: at the cap the sweeps should reach about {exp_ir:.0f} dB peak-to-noise (comfortable is {ST.EXPECTED_IR_OK_DB:.0f}+): expect noisy bass")
                self.details["ramp"]["warning"] = f"expected IR peak-to-noise {exp_ir:.0f} dB"
            else:
                self.say(f"the sweeps should reach about {exp_ir:.0f} dB peak-to-noise at the cap: enough")
        self.say(f"sweep level {self.sweep_level:.1f} dBFS (ramp stopped at {lv:.0f} dBFS, burst correlation {coh:.0f} dB; expected sweep peak-to-noise about {exp_ir:.0f} dB)")
        self.st("ramp", "level ramp", f"sweep level {self.sweep_level:.0f} dBFS", 0.12, level_dbfs=self.sweep_level)

    def channel_id(self):
        self.st("channel_id", "channel ID", "one quiet chirp per speaker", 0.14)
        x, cues = ST.id_stimulus(min(ST.CAP_DBFS, self.ramp_level))
        cap = self.play(x, "id", self.monitor(), pre_s=0.3, post_s=0.3)
        ident = M.analyse_channel_id(cap.x, cues)
        self.details["channel_id"] = ident
        dead = [n for n, v in ident.items() if not v["alive"]]
        self.dead_id = dead
        if "FL" in dead or len(dead) >= 4:
            raise RunAborted("channel ID: " + ("FL is silent" if "FL" in dead else f"{len(dead)} of 8 speakers are silent") +
                             ": wrong microphone, muted amp, or cabling. Nothing measured.", "error")
        self.say("channel ID: " + ("all 8 speakers answer" if not dead else "SILENT: " + ", ".join(dead)))

    def wait_prompt(self, pid, text, step, prog):
        """Shows a prompt (status.json + terminal + beep) and waits for control/continue-<id>, Enter on a terminal, or abort."""
        self.st("waiting_user", step, text, prog, prompt={"id": pid, "text": text, "action": "continue"})
        self.say(f"\a>>> {text}   (GUI: Continue button; terminal: press Enter)")
        if self.cfg.auto_continue:
            self.control.press_continue(pid)
        t0 = time.monotonic()
        while True:
            self.check_abort()
            if self.control.continued(pid):
                break
            if self.cfg.interactive_stdin and _stdin_ready():
                sys.stdin.readline(); break
            if self.cfg.prompt_timeout_s is not None and time.monotonic() - t0 > self.cfg.prompt_timeout_s:
                raise RunAborted(f"no answer to the prompt '{pid}' within {self.cfg.prompt_timeout_s:.0f} s")
            time.sleep(POLL_S)
        self.status.update(prompt=None)

    def measure_position(self, k, label, offset, weight, ref_pos=None):
        """Runs the main timeline at position k (0-based) and returns the PositionData, with the move check for k > 0."""
        total = len(self.positions)
        base = 0.15 + 0.7 * k / total
        retries = 0
        while True:
            tl = ST.Timeline(sweep_s=self.cfg.sweep_s, tail_s=self.cfg.tail_s, level_db=self.sweep_level)
            sweeps = []
            for sl in tl.cues["slots"]:
                if sl["name"] == "LFE":
                    sweeps.append({"t0": 1.0 + sl["t"], "T": self.cfg.sweep_s, "f0": sl["f0"], "f1": sl["f1"]})
            self.st("sweeping", f"position {k + 1}/{total}: {label}", "playing sync chirps and sweeps", base, position=k + 1, level_dbfs=self.sweep_level)
            mon = _StepMonitor(self.monitor(lfe_sweeps=sweeps, level_dbfs=self.sweep_level), self, tl, base, 0.7 / total, k + 1, total, label)
            cap = self.play(tl.render(), "main", mon, pre_s=1.0, post_s=1.0)
            self.st("analysing", f"position {k + 1}/{total}: analysing", "deconvolving the sweeps", base + 0.7 / total * 0.9, position=k + 1)
            try:
                pd = M.analyse_recording(cap.x, tl.cues, index=k + 1, label=label, offset_cm=offset, weight=weight, noise_floor_dbfs=self.floor)
            except M.MeasurementError as e:
                raise RunAborted(f"position {k + 1}: {e}", "error")
            if not self.cfg.dry and not self.be.simulated:
                IR.write_wav24(os.path.join(self.dir, "raw", f"pos{k + 1}.wav"), cap.x)
            bad = [n for n, c in pd.ch.items() if c.snr40_db is not None and not c.dead and c.snr40_db < M.SNR_ABORT_40HZ_DB]
            if bad:
                raise RunAborted(f"SNR at 40 Hz below {M.SNR_ABORT_40HZ_DB:.0f} dB for {', '.join(bad)} ("
                                 + ", ".join(f"{n} {pd.ch[n].snr40_db:.0f} dB" for n in bad) + "): too quiet or too noisy for a trustworthy bass fit", "error")
            if ref_pos is not None:
                moved = max((abs(pd.ch[n].arrival_ms - ref_pos.ch[n].arrival_ms) for n in ("FR", "SL", "SR")
                             if n in pd.ch and n in ref_pos.ch and not pd.ch[n].dead), default=0.0)
                pd.move_check_ms = moved
                if (moved < 0.15 or moved > 1.2) and retries < 2:
                    retries += 1
                    why = "does not seem to have moved" if moved < 0.15 else "seems to have moved too far"
                    self.say(f"position {k + 1}: arrival times changed by only {moved:.2f} ms: the mic {why}")
                    self.wait_prompt(f"move-{k + 1}-retry{retries}", f"The mic {why} (arrival change {moved:.2f} ms, expected 0.3-0.6). "
                                     f"{RS.PROMPT_TEXT[k]}", "mic move check", base)
                    self.be.set_position(k + 1, offset)
                    continue
                if moved < 0.15 or moved > 1.2:
                    pd.warn_move = f"position {k + 1}: mic move not confirmed (arrival change {moved:.2f} ms); results from this position are suspect"
            return pd

    def run(self):
        """Executes the run. Returns the final status dict."""
        try:
            self.start()
            if self.cfg.dry:
                return self.dry_run()
            self.preflight()
            self.ramp()
            self.channel_id()
            if self.cfg.check_only:
                RS.write_json(os.path.join(self.dir, "check.json"), self.details)
                self.st("done", "check", "check finished: nothing was measured beyond steps 1-3", 1.0)
                return self.status.s
            positions = []
            for k, (label, offset, weight) in enumerate(self.positions):
                if k > 0:
                    self.wait_prompt(f"move-{k + 1}", RS.PROMPT_TEXT[k], f"move the mic ({label})", 0.15 + 0.7 * k / len(self.positions))
                    self.be.set_position(k + 1, offset)
                positions.append(self.measure_position(k, label, offset, weight, positions[0] if k else None))
            self.st("analysing", "analysis", "fitting delays, trims, bass EQ and heights", 0.9, position=len(self.positions))
            self.finish(positions)
            self.st("done", "done", "finished: review the report, then apply explicitly", 1.0, position=len(self.positions))
        except RunAborted as e:
            self.status.update(state=e.kind if e.kind == "error" else "aborted", message=e.reason, error=e.reason, prompt=None, step="stopped")
            self.say(f"STOPPED: {e.reason}")
        except KeyboardInterrupt:
            self.status.update(state="aborted", message="interrupted (Ctrl-C)", error="interrupted (Ctrl-C)", prompt=None)
            self.say("STOPPED: interrupted (Ctrl-C)")
        except BackendError as e:
            self.status.update(state="error", message=str(e), error=str(e), prompt=None)
            self.say(f"ERROR: {e}")
        except ST.LevelCapError as e:
            self.status.update(state="error", message=str(e), error=str(e), prompt=None)
            self.say(f"ERROR (level cap): {e}")
            raise
        return self.status.s

    def dry_run(self):
        tl = ST.Timeline(sweep_s=self.cfg.sweep_s, tail_s=self.cfg.tail_s, level_db=-32.0)
        self.say("DRY RUN: nothing is played.")
        self.say("level ramp (FL pink bursts): " + ", ".join(f"{v:.0f}" for v in ST.ramp_levels()) + f" dBFS peak; stops at {ST.RAMP_SNR_TARGET_DB:.0f} dB over the noise floor")
        self.say(f"sweep level = ramp stop level + {ST.SWEEP_BOOST_DB:.0f} dB, capped at {ST.CAP_DBFS:.0f} dBFS (example below uses -32)")
        self.say(tl.describe())
        self.say(f"positions: {len(self.positions)} ({', '.join(p[0] for p in self.positions)})")
        self.st("done", "dry run", "dry run: nothing played", 1.0)
        return self.status.s

    # ---- analysis and outputs ---------------------------------------------------------------------------------------------------------
    def finish(self, positions):
        meas = AN.Measurement(positions)
        prev = find_previous(self.cfg.runs_dir, self.be.simulated, exclude=self.id)
        opts = AN.Options(target=self.cfg.target, hump_db=self.cfg.hump_db, allow_boost=self.cfg.allow_boost)
        prop = AN.propose(meas, self.room, opts, previous=prev)
        for p in positions:
            if getattr(p, "warn_move", None):
                prop["warnings"].append(p.warn_move)
                if prop["verdict"] == "PASS":
                    prop["verdict"] = "WARN"
        if self.dead_id:
            prop["warnings"].append("channel ID found silent speaker(s): " + ", ".join(self.dead_id))
        res = RS.build_results(self.id, self.created, self.cfg.mode, self.be.simulated, self.cfg.target, self.cfg.hump_db, meas, prop)
        errs = RS.check_results(res)
        if errs:
            raise RuntimeError("results.json violates the contract: " + "; ".join(errs[:5]))
        PF.save_room(prop["new_room"], os.path.join(self.dir, "room.proposed.json"))
        from . import install
        ok, note = install.write_proposed_config(os.path.join(self.dir, "room.proposed.json"), os.path.join(self.dir, "config.proposed.yml"))
        if not ok:
            res["warnings"].append("config.proposed.yml NOT generated: " + note)
            if res["verdict"] == "PASS":
                res["verdict"] = "WARN"
        else:
            self.details["config_note"] = note
        RS.write_json(os.path.join(self.dir, "results.json"), res)
        self.details["positions"] = [{"index": p.index, "label": p.label, "placement": p.placement, "drift_ppm": round(p.drift_ppm, 1),
                                      "repeat_fl_rms_db": p.repeat_fl_rms_db, "peak_dbfs": round(p.peak_dbfs, 1),
                                      "move_check_ms": getattr(p, "move_check_ms", None),
                                      "snr": {n: [None if c.snr_db is None else round(c.snr_db, 1), None if c.snr40_db is None else round(c.snr40_db, 1)] for n, c in p.ch.items()},
                                      "pilot_dev_ms": {n: c.pilot_dev_ms for n, c in p.ch.items()}} for p in positions]
        self.details["loo"] = prop["fit"].get("loo")
        self.details["fit_notes"] = prop["notes"]
        self.details["sweep_level_dbfs"] = self.sweep_level
        RS.write_json(os.path.join(self.dir, "details.json"), _jsonable(self.details))
        from . import report
        report.write_report(self.dir, res)
        self.say(f"\n{res['verdict']}: {res['summary']}")
        for w in res["warnings"]:
            self.say(f"  warning: {w}")
        self.say(f"run {self.id}: {self.dir}/report.html")
        self.say(f"to apply (explicit, with a 60 s trial):  {res['commands']['apply']}")
        return res


class _StepMonitor:
    """Wraps the safety monitor and keeps step/progress in status.json current (which sweep is playing)."""

    def __init__(self, inner, run, tl, base, span, pos, total, label):
        self.inner, self.run, self.tl, self.base, self.span, self.pos, self.total, self.label = inner, run, tl, base, span, pos, total, label

    def __call__(self, t, blk):
        why = self.inner(t, blk)
        if why:
            return why
        tt = t - 1.0
        step = "sync chirps"
        for sl in self.tl.cues["slots"]:
            if sl["pilot_t"] - 0.1 <= tt < sl["t"] + self.tl.sweep_s + self.tl.tail_s:
                step = f"{sl['name']} sweep"
        self.run.status.update(force=False, step=f"position {self.pos}/{self.total}: {step}", progress=min(0.99, self.base + self.span * min(1.0, max(0.0, tt / self.tl.total)) * 0.85))
        return None


def _stdin_ready():
    import select
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return o


def find_previous(runs_dir, simulated, exclude=None):
    """results.json of the newest run that was KEPT (autocal keep writes a 'kept' marker) with the same simulated flag, or None."""
    try:
        for rid in sorted(os.listdir(runs_dir), reverse=True):
            if rid == exclude:
                continue
            d = os.path.join(runs_dir, rid)
            if os.path.exists(os.path.join(d, "kept")) and os.path.exists(os.path.join(d, "results.json")):
                r = json.load(open(os.path.join(d, "results.json")))
                if r.get("simulated") == simulated:
                    return r
    except OSError:
        pass
    return None
