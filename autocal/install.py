"""apply / keep / rollback: the only code in autocal that changes the live system, and only when called explicitly.

* `apply RUN` backs up the live config.yml and room.json, installs the run's proposal, restarts CamillaDSP, and starts a TRIAL (60 s by default):
  unless `keep` is run in time, a detached watchdog restores the backup. `rollback` restores the latest backup at any time.
* Paths come from the environment, so tests (and --simulate runs) never touch the real system:
    AUTOCAL_CONFIG      live CamillaDSP config.yml. If set, apply works by plain file copy (+ AUTOCAL_RESTART_CMD if given). Unset = the real rig:
                        ~/.config/camilladsp/config.yml, installed through set-eq.py's restart + relink (UNTESTED ON HARDWARE).
    AUTOCAL_ROOM        live room.json (default ~/.config/camilladsp/room.json)
    AUTOCAL_BACKUP_DIR  backups and the trial state (default ~/.local/share/autocal/backups)
* A simulated run is refused unless AUTOCAL_CONFIG points somewhere else (it must never reach the real config).

NOTE for the owner: set-eq.py reads the live room.json implicitly (use --no-room to ignore it), so the router's automatic profile switches keep the
applied values. Needs a set-eq.py with the live-room support (linux-theater-dsp, 2026-10-26 or later).
"""
import json
import os
import shutil
import subprocess
import sys
import time

from . import profile as PF
from .safety import now_iso

DEFAULT_CONFIG = "~/.config/camilladsp/config.yml"
DEFAULT_BACKUPS = "~/.local/share/autocal/backups"
TRIAL_S = 60.0


class ApplyError(RuntimeError):
    pass


def live_config_path():
    return os.path.expanduser(os.environ.get("AUTOCAL_CONFIG", DEFAULT_CONFIG))


def file_mode():
    return "AUTOCAL_CONFIG" in os.environ


def backup_root():
    return os.path.expanduser(os.environ.get("AUTOCAL_BACKUP_DIR", DEFAULT_BACKUPS))


def state_path():
    return os.path.join(backup_root(), "trial.json")


def read_state():
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_state(st):
    os.makedirs(backup_root(), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, state_path())


def write_proposed_config(room_path, out_path):
    """Builds the CamillaDSP config for a room file with set-eq.py --room --write-config (which validates it with `camilladsp -c` if present).
    Returns (ok, note)."""
    se = PF.find_set_eq()
    if not se:
        return False, "set-eq.py not found (set SET_EQ=/path/to/set-eq.py or keep linux-theater-dsp next to theater-measurement)"
    try:
        r = subprocess.run([sys.executable, se, f"--room={room_path}", f"--write-config={out_path}"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run set-eq.py: {e}"
    if r.returncode != 0 or not os.path.exists(out_path):
        return False, (r.stderr or r.stdout).strip()[-300:] or f"set-eq.py exited {r.returncode}"
    return True, (r.stdout.strip().splitlines() or ["config written"])[-1]


def _restart(out):
    """Restart CamillaDSP and relink. File mode: only AUTOCAL_RESTART_CMD (argv string) if set. Real rig: systemctl + set-eq's relink."""
    cmd = os.environ.get("AUTOCAL_RESTART_CMD")
    if file_mode():
        if cmd:
            import shlex
            subprocess.run(shlex.split(cmd), check=True)
        return
    se = PF.find_set_eq()                                          # UNTESTED ON HARDWARE from here
    if not se:
        raise ApplyError("set-eq.py not found: cannot restart and relink CamillaDSP")
    sys.path.insert(0, os.path.dirname(se))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("set_eq_mod", se)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        was_via = ("mpv:output_FL", "camilladsp-capture:input_1") in m.current_links()
        subprocess.run(["systemctl", "--user", "restart", "camilladsp.service"], check=True)
        m.relink(was_via)
    finally:
        sys.path.pop(0)


def _check_config(path):
    exe = shutil.which("camilladsp")
    if exe:
        r = subprocess.run([exe, "-c", path], capture_output=True, text=True)
        if r.returncode != 0:
            raise ApplyError("camilladsp -c rejected the proposed config: " + (r.stdout + r.stderr).strip()[-300:])


def make_backup(run_id):
    ts = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(backup_root(), ts)
    n = 0
    while os.path.exists(d):
        n += 1; d = os.path.join(backup_root(), f"{ts}-{n}")
    os.makedirs(d)
    cfg = live_config_path()
    had = os.path.exists(cfg)
    if had:
        shutil.copy2(cfg, os.path.join(d, "config.yml"))
    PF.save_room(PF.current_room(), os.path.join(d, "room.json"))
    had_room = os.path.exists(PF.live_room_path())
    json.dump({"run": run_id, "time": now_iso(), "config_path": cfg, "room_path": PF.live_room_path(), "had_config": had, "had_room": had_room},
              open(os.path.join(d, "meta.json"), "w"), indent=1)
    return d


def latest_backup():
    try:
        ds = sorted(d for d in os.listdir(backup_root()) if os.path.exists(os.path.join(backup_root(), d, "meta.json")))
    except OSError:
        return None
    return os.path.join(backup_root(), ds[-1]) if ds else None


def apply_run(run_dir, trial_s=TRIAL_S, wait=False, force=False, out=print, spawn_watchdog=True):
    """Installs the run's proposal with a trial. Returns the trial state dict."""
    res_p = os.path.join(run_dir, "results.json")
    if not os.path.exists(res_p):
        raise ApplyError(f"{run_dir}: no results.json (the run did not finish)")
    res = json.load(open(res_p))
    cfg_p, room_p = os.path.join(run_dir, "config.proposed.yml"), os.path.join(run_dir, "room.proposed.json")
    if not (os.path.exists(cfg_p) and os.path.exists(room_p)):
        raise ApplyError("the run has no config.proposed.yml / room.proposed.json (set-eq.py could not build it): nothing to apply")
    if res.get("simulated") and not file_mode():
        raise ApplyError("this run is SIMULATED; refusing to install it on the real system. (Tests set AUTOCAL_CONFIG to a temp file.)")
    if res.get("verdict") == "FAIL" and not force:
        raise ApplyError("the run's verdict is FAIL; fix the cause first (or --force)")
    st = read_state()
    if st and st.get("state") == "trial" and time.time() < st.get("deadline_epoch", 0):
        raise ApplyError("a trial is already running: run `autocal keep` or `autocal rollback` first")
    PF.validate_room(json.load(open(room_p)))
    _check_config(cfg_p)
    bk = make_backup(res["id"])
    out(f"backup: {bk}")
    live = live_config_path()
    os.makedirs(os.path.dirname(live) or ".", exist_ok=True)
    shutil.copy2(cfg_p, live + ".autocal-new"); os.replace(live + ".autocal-new", live)
    rp = PF.live_room_path()
    os.makedirs(os.path.dirname(rp) or ".", exist_ok=True)
    shutil.copy2(room_p, rp + ".autocal-new"); os.replace(rp + ".autocal-new", rp)
    st = {"state": "trial", "run": res["id"], "run_dir": os.path.abspath(run_dir), "started": now_iso(), "deadline_epoch": time.time() + trial_s,
          "trial_s": trial_s, "backup": bk}
    write_state(st)
    try:
        _restart(out)
    except Exception as e:                                          # restart failed: never leave a half-applied system
        rollback(reason=f"restart failed: {e}", out=out)
        raise ApplyError(f"restarting CamillaDSP failed ({e}); the backup was restored") from e
    st["deadline_epoch"] = time.time() + trial_s                    # the trial clock starts when the new profile is running
    write_state(st)
    out(f"installed {os.path.basename(cfg_p)}. TRIAL: it reverts in {trial_s:.0f} s unless you run `autocal keep`.")
    if wait:
        return _wait_trial(out)
    if spawn_watchdog:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.Popen([sys.executable, "-m", "autocal", "_watchdog"], cwd=root, start_new_session=True, env=dict(os.environ),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return st


def _wait_trial(out):
    """Blocks until the trial is kept or expires (then reverts). Used by the watchdog and by `apply --wait`."""
    while True:
        st = read_state()
        if not st or st.get("state") != "trial":
            return st
        if time.time() >= st["deadline_epoch"]:
            out("trial expired without `keep`: restoring the backup")
            return rollback(reason="trial expired", out=out)
        time.sleep(0.1)


def keep(out=print):
    st = read_state()
    if not st or st.get("state") != "trial":
        raise ApplyError("no trial is running (nothing to keep)")
    if time.time() >= st["deadline_epoch"]:
        raise ApplyError("the trial already expired; run `autocal rollback` (or apply again)")
    st["state"] = "kept"; st["kept"] = now_iso()
    write_state(st)
    try:
        open(os.path.join(st["run_dir"], "kept"), "w").write(st["kept"])
    except OSError:
        pass
    out(f"kept run {st['run']}.")
    return st


def rollback(reason="requested", out=print):
    """Restores the latest backup (config.yml and room.json) and restarts CamillaDSP."""
    bk = latest_backup()
    if not bk:
        raise ApplyError("no backup found: nothing to roll back to")
    meta = json.load(open(os.path.join(bk, "meta.json")))
    live = live_config_path()
    if meta.get("had_config") and os.path.exists(os.path.join(bk, "config.yml")):
        shutil.copy2(os.path.join(bk, "config.yml"), live + ".autocal-old"); os.replace(live + ".autocal-old", live)
    elif os.path.exists(live):
        os.remove(live)
    rp = PF.live_room_path()
    if meta.get("had_room"):
        shutil.copy2(os.path.join(bk, "room.json"), rp + ".autocal-old"); os.replace(rp + ".autocal-old", rp)
    elif os.path.exists(rp):
        os.remove(rp)
    restart_err = None
    try:
        _restart(out)
    except Exception as e:                                          # the files are restored; say so even if the restart did not work
        restart_err = str(e)
        out(f"WARNING: files restored but restarting CamillaDSP failed: {e}")
    st = read_state() or {}
    if restart_err:
        st["restart_error"] = restart_err
    st.update({"state": "reverted", "reverted": now_iso(), "reason": reason, "backup": bk})
    write_state(st)
    out(f"rolled back to {bk} ({reason}).")
    return st


def watchdog():
    """Detached process started by `apply`: reverts at the deadline unless the trial was kept or rolled back meanwhile."""
    _wait_trial(lambda s: None)
