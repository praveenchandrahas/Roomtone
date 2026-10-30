"""Command line: python3 -m autocal {check,run,report,apply,keep,rollback,verify} ..."""
import argparse
import json
import time
import os
import sys

from . import install, profile as PF, results as RS, runner as RN, safety as SF
from . import stimulus as ST
from .stimulus import LevelCapError


def _runs_dir(a):
    return a.runs_dir or os.environ.get("AUTOCAL_RUNS_DIR", "runs")


def resolve_run(runs_dir, arg):
    if os.path.isdir(arg) and os.path.exists(os.path.join(arg, "run.json")):
        return arg
    if arg == "latest":
        ids = sorted(d for d in os.listdir(runs_dir) if os.path.exists(os.path.join(runs_dir, d, "results.json"))) if os.path.isdir(runs_dir) else []
        if ids:
            return os.path.join(runs_dir, ids[-1])
    d = os.path.join(runs_dir, arg)
    if os.path.isdir(d):
        return d
    raise SystemExit(f"run '{arg}' not found in {runs_dir}")


def build_backend(a, sim_params=None):
    if a.simulate:
        from .sim.backend import SimBackend
        from .sim.room import SimRoom
        sp = dict(sim_params or {})
        room = SimRoom(scenario=sp.get("scenario", "default"), seed=sp.get("seed", 0), noise_dbfs=sp.get("noise_dbfs", -74.0),
                       sub_distortion_db=sp.get("sub_distortion_db"))
        faults = []
        for f in sp.get("faults", []):
            typ, stage, t = f.split(":")[:3]
            faults.append({"type": typ, "stage": None if stage in ("", "any") else stage, "t": float(t)})
        prof = PF.current_room() if sp.get("use_live_room") else PF.default_room()
        return SimBackend(room=room, profile=prof, seed=sp.get("seed", 0), faults=faults, speed=sp.get("speed", 0.0),
                          other_streams=["sim-stream"] if sp.get("other_stream") else [])
    from .pipewire import PipewireBackend
    return PipewireBackend()


def cmd_run(a, check_only=False):
    seats = None
    if a.seat:
        try:
            seats = RS.parse_seats(a.seat)
        except ValueError as e:
            print(f"autocal: {e}", file=sys.stderr); return 2
        a.mode = "seats"
    elif a.mode == "seats":
        print("autocal: --mode seats needs at least two --seat NAME=WEIGHT options", file=sys.stderr); return 2
    cfg = RN.RunConfig(seats=seats, mode=a.mode, noise_ref_s=a.noise_ref, runs_dir=_runs_dir(a), dry=a.dry, check_only=check_only, auto_continue=a.auto_continue,
                       prompt_timeout_s=a.prompt_timeout, sweep_s=a.sweep_s, tail_s=a.tail_s, target=a.target, hump_db=a.hump_db,
                       allow_boost=a.allow_boost, interactive_stdin=sys.stdin.isatty())
    sim_params = {"scenario": a.scenario, "seed": a.seed, "noise_dbfs": a.sim_noise, "speed": a.speed, "faults": a.sim_fault,
                  "sub_distortion_db": a.sim_sub_distortion, "other_stream": a.sim_other_stream}
    be = build_backend(a, sim_params)
    run = RN.Run(cfg, be)
    if a.simulate:
        json.dump({**sim_params, "sweep_s": a.sweep_s, "tail_s": a.tail_s}, open(os.path.join(run.dir, "sim.json"), "w"))
    try:
        st = run.run()
    finally:
        be.close()
    run.details["sweep_s"], run.details["tail_s"] = a.sweep_s, a.tail_s
    if os.path.exists(os.path.join(run.dir, "details.json")):
        d = json.load(open(os.path.join(run.dir, "details.json"))); d.update(sweep_s=a.sweep_s, tail_s=a.tail_s)
        RS.write_json(os.path.join(run.dir, "details.json"), d)
    return 0 if st["state"] == "done" else 3


def cmd_report(a):
    from . import report
    d = resolve_run(_runs_dir(a), a.run)
    rf = os.path.join(d, "results.json")
    if not os.path.exists(rf):
        why = ""
        try:
            why = json.load(open(os.path.join(d, "status.json"))).get("error") or ""
        except (OSError, ValueError):
            pass
        print(f"{d}: this run stopped before the analysis, so there is no report.\n" + (f"Reason: {why}\n" if why else "")
              + "The recording, if one was made, is in the run folder's raw/ directory.")
        return 2
    print(report.write_report(d, json.load(open(rf))))
    return 0


def cmd_apply(a):
    d = resolve_run(_runs_dir(a), a.run)
    try:
        install.apply_run(d, trial_s=a.trial_seconds, wait=a.wait, force=a.force)
    except install.ApplyError as e:
        print(f"apply refused: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_keep(a):
    try:
        install.keep()
    except install.ApplyError as e:
        print(f"keep: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_rollback(a):
    try:
        install.rollback()
    except install.ApplyError as e:
        print(f"rollback: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_mic(a):
    """Live mic meter: plays nothing. Tap or talk into the mic and watch the levels."""
    from .backend import BackendError
    from .pipewire import PipewireBackend
    be = PipewireBackend()
    print(f"microphone: {be.mic or '(set MIC=<name>, see ./rec.sh list)'}; {a.seconds:g} s; "
          + ("stay quiet and keep the room as it is for a measurement. Nothing is played." if a.spectrum else "tap the mic or talk into it. Nothing is played."))
    got = [] if a.spectrum else None
    try:
        fmt = be.meter(a.seconds, emit=(lambda *_: None) if a.spectrum else print, collect=got)
    except BackendError as e:
        print(f"STOPPED: {e}", file=sys.stderr); return 1
    print(f"pw-record delivered: {fmt}")
    if a.spectrum:
        import numpy as np
        x = np.concatenate(got) if got else np.zeros(0)
        print(spectrum_report(x))
    return 0


def spectrum_report(x, fs=48000):
    """Noise-floor report from a mic recording: level per octave band (10th / median / 90th percentile over 1 s windows, so one-off bangs do not count but a cycling source shows a wide spread),
    and the strongest narrow tone (mains hum shows here). Pure analysis; used by `autocal mic --spectrum`."""
    import numpy as np
    if len(x) < 2 * fs:
        return "spectrum: need at least 2 s of audio"
    x = x - np.mean(x)
    win = fs
    nwin = len(x) // win
    centres = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000]
    lines = ["noise floor per octave band (dBFS rms over 1 s windows):", f"  {'band Hz':>8}  {'p10':>6} {'median':>7} {'p90':>6}  {'spread':>6}"]
    freqs = np.fft.rfftfreq(win, 1 / fs)
    hann = np.hanning(win)
    spec = np.array([np.abs(np.fft.rfft(x[i * win:(i + 1) * win] * hann)) ** 2 for i in range(nwin)])
    for c in centres + [40]:
        lo, hi = (c / 2 ** 0.5, c * 2 ** 0.5) if c != 40 else (35.0, 45.0)
        m = (freqs >= lo) & (freqs < hi)
        pw = spec[:, m].sum(1) * 2 / (np.sum(hann ** 2) * win)
        d = 10 * np.log10(np.maximum(pw, 1e-18))
        p10, p90 = np.percentile(d, 10), np.percentile(d, 90)
        lines.append(f"  {('35-45' if c == 40 else format(c, 'g')):>8}  {p10:6.1f} {np.median(d):7.1f} {p90:6.1f}  {p90 - p10:6.1f}" + ("   <- the band autocal's bass gate looks at" if c == 40 else ""))
    avg = spec.mean(0)
    best = None
    for k in range(5, len(avg) - 5):
        if freqs[k] > 2000:
            break
        nb = np.median(np.r_[avg[k - 5:k - 1], avg[k + 2:k + 6]])
        r = 10 * np.log10(avg[k] / max(nb, 1e-30))
        if avg[k] >= avg[k - 1] and avg[k] >= avg[k + 1] and (best is None or r > best[1]):
            best = (freqs[k], r)
    lines.append(f"strongest narrow tone: {best[0]:.0f} Hz, {best[1]:.0f} dB above its neighbours" + ("  (a hum or whine: look for the source)" if best[1] >= 15 else "  (nothing stands out: broadband noise)") if best else "no tone found")
    return "\n".join(lines)


def cmd_verify(a):
    from . import verify
    d = resolve_run(_runs_dir(a), a.run)
    sim = json.load(open(os.path.join(d, "sim.json"))) if a.simulate and os.path.exists(os.path.join(d, "sim.json")) else {}
    sim["use_live_room"] = True
    be = build_backend(a, sim)
    def ask(t):                                     # wait for the person: without this the second sweep starts before the mic is moved
        print(">>> " + t)
        if sys.stdin.isatty():
            input(">>> Press Enter when the microphone is in place (Ctrl+C stops): ")
        else:
            print(">>> (no terminal: continuing in 20 s)"); time.sleep(20)
    ver = verify.run_verify(d, be, prompt=ask if not a.simulate else None)
    return 0 if ver["overall"] != "FAIL" else 3


def parser():
    p = argparse.ArgumentParser(prog="autocal", description="Automated room calibration for the SDHT rig. Never applies anything by itself.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(s):
        s.add_argument("--runs-dir", help="where runs are stored (default $AUTOCAL_RUNS_DIR or ./runs)")
        s.add_argument("--lfe-from", type=float, default=35.0, metavar="HZ",
                       help="where the sub sweep starts (default 35: below that the sub works hardest, distorts most and has the least SNR; 25 restores the old range)")

    for name in ("run", "check"):
        s = sub.add_parser(name, help="measure and propose" if name == "run" else "preflight, noise floor, level ramp and channel ID only")
        common(s)
        s.add_argument("--simulate", action="store_true", help="use the simulated room (no audio, no hardware)")
        s.add_argument("--mode", choices=["single", "five", "seats"], default="single")
        s.add_argument("--seat", action="append", default=[], metavar="NAME=WEIGHT[@X,Y]",
                       help="multi-seat mode (repeatable, implies --mode seats): a seat to measure and how much it counts, e.g. --seat main=2 --seat left=1 --seat right=1. "
                            "The first seat is the reference (the delays and trims follow the weighted mean). Optional @X,Y in cm (right, forward) is for the simulated room only")
        s.add_argument("--dry", action="store_true", help="print the timeline and levels; play nothing")
        s.add_argument("--noise-ref", type=float, default=20.0, metavar="SECONDS",
                       help="silence recorded before and after every position, the noise reference for the 40 Hz signal-to-noise (default 20; 0 = judge it from the sweep's own IR)")
        s.add_argument("--seed", type=int, default=0)
        s.add_argument("--scenario", default="default", help="simulation only: default clean flipped dead null noisy")
        s.add_argument("--auto-continue", action="store_true", help="answer the 'move the mic' prompts automatically (simulation and tests)")
        s.add_argument("--prompt-timeout", type=float, default=None, help="abort if a prompt is not answered within N seconds")
        s.add_argument("--speed", type=float, default=0.0, help="simulation only: virtual seconds per real second (0 = as fast as possible)")
        s.add_argument("--sweep-s", type=float, default=7.0)
        s.add_argument("--tail-s", type=float, default=2.0)
        s.add_argument("--target", default="owner_hump", choices=["owner_hump", "house", "flat"])
        s.add_argument("--hump-db", type=float, default=20.0)
        s.add_argument("--allow-boost", action="store_true", help="allow up to +3 dB boosts (Q <= 2); off by default (cut-only)")
        s.add_argument("--sim-noise", type=float, default=-74.0, help=argparse.SUPPRESS)
        s.add_argument("--sim-fault", action="append", default=[], help=argparse.SUPPRESS)
        s.add_argument("--sim-sub-distortion", type=float, default=None, help=argparse.SUPPRESS)
        s.add_argument("--sim-other-stream", action="store_true", help=argparse.SUPPRESS)
    s = sub.add_parser("mic", help="live microphone levels per channel (plays nothing): is the mic alive, muted, or cancelling?")
    s.add_argument("--spectrum", action="store_true", help="instead of the live meter, print the noise floor per octave band and the strongest narrow tone")
    s.add_argument("--seconds", type=float, default=20.0)
    s = sub.add_parser("report", help="regenerate report.html for a run"); common(s); s.add_argument("run")
    s = sub.add_parser("apply", help="install a run's proposal with a trial (explicit; backs up first)"); common(s); s.add_argument("run")
    s.add_argument("--trial-seconds", type=float, default=install.TRIAL_S); s.add_argument("--wait", action="store_true", help="stay until the trial is kept or reverted")
    s.add_argument("--force", action="store_true", help="apply even if the verdict is FAIL")
    sub.add_parser("keep", help="confirm the trial: keep the applied profile")
    sub.add_parser("rollback", help="restore the latest backup")
    s = sub.add_parser("verify", help="re-measure through the applied profile and grade it"); common(s); s.add_argument("run")
    s.add_argument("--simulate", action="store_true")
    sub.add_parser("_watchdog", help=argparse.SUPPRESS)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    lo = getattr(a, "lfe_from", None)
    if lo is not None:
        if not 25.0 <= lo <= 60.0:
            print("--lfe-from must be between 25 and 60 Hz"); return 2
        ST.BANDS["LFE"] = (float(lo), ST.BANDS["LFE"][1])
    try:
        if a.cmd == "run":
            return cmd_run(a)
        if a.cmd == "check":
            return cmd_run(a, check_only=True)
        if a.cmd == "mic":
            return cmd_mic(a)
        if a.cmd == "report":
            return cmd_report(a)
        if a.cmd == "apply":
            return cmd_apply(a)
        if a.cmd == "keep":
            return cmd_keep(a)
        if a.cmd == "rollback":
            return cmd_rollback(a)
        if a.cmd == "verify":
            return cmd_verify(a)
        if a.cmd == "_watchdog":
            install.watchdog(); return 0
    except LevelCapError as e:
        print(f"REFUSED (level cap): {e}", file=sys.stderr)
        return 4
    return 2
