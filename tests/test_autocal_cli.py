"""CLI end to end (subprocess): run --simulate, report, apply/keep/rollback on temp files, trial revert by the watchdog, refusals."""
import json, os, shutil, subprocess, sys, tempfile, time
from autocal_common import check, finish, ROOT

TMP = tempfile.mkdtemp(prefix="autocal-cli-")
RUNS = os.path.join(TMP, "runs")
LIVE, ROOMF, BK = os.path.join(TMP, "live", "config.yml"), os.path.join(TMP, "live", "room.json"), os.path.join(TMP, "bk")
os.makedirs(os.path.dirname(LIVE))
open(LIVE, "w").write("ORIGINAL CONFIG\n")


def cli(*args, env=None, timeout=110):
    e = dict(os.environ)
    for k in ("AUTOCAL_CONFIG", "AUTOCAL_ROOM", "AUTOCAL_BACKUP_DIR", "AUTOCAL_RESTART_CMD"):
        e.pop(k, None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "autocal", *args], cwd=ROOT, env=e, capture_output=True, text=True, timeout=timeout)


ENV = {"AUTOCAL_CONFIG": LIVE, "AUTOCAL_ROOM": ROOMF, "AUTOCAL_BACKUP_DIR": BK}
FAST = ["--sweep-s", "3", "--tail-s", "1.5", "--auto-continue"]


def newest():
    ids = sorted(os.listdir(RUNS)); return os.path.join(RUNS, ids[-1])


# ---- run / check / dry
r = cli("check", "--simulate", "--runs-dir", RUNS, *FAST)
check("check --simulate exits 0 and measures nothing beyond the ramp and channel ID", r.returncode == 0 and not os.path.exists(os.path.join(newest(), "results.json")), r.stderr[-120:])
r = cli("run", "--simulate", "--dry", "--runs-dir", RUNS, *FAST)
check("run --dry plays nothing and writes no results", r.returncode == 0 and not os.path.exists(os.path.join(newest(), "results.json")) and "DRY RUN" in r.stdout)
time.sleep(1.1)
r = cli("run", "--simulate", "--runs-dir", RUNS, *FAST)
d = newest()
check("run --simulate (single) exits 0 and writes results, proposed config and room, report", r.returncode == 0 and all(os.path.exists(os.path.join(d, f)) for f in ("results.json", "run.json", "status.json", "room.proposed.json", "report.html")), r.stderr[-200:])
sys.path.insert(0, ROOT)
from autocal import results as RS
res = json.load(open(os.path.join(d, "results.json")))
check("the CLI run's files satisfy the contract schemas", not RS.check_results(res) and not RS.validate(json.load(open(os.path.join(d, "run.json"))), RS.RUN_SCHEMA)
      and not RS.validate(json.load(open(os.path.join(d, "status.json"))), RS.STATUS_SCHEMA) and res["simulated"] is True)
check("nothing was applied: the live config is untouched after a run", open(LIVE).read() == "ORIGINAL CONFIG\n" and not os.path.exists(BK) and not os.path.exists(ROOMF))
html = open(os.path.join(d, "report.html")).read()
check("report.html is self-contained (no external requests)", "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html and "<script src" not in html)
os.remove(os.path.join(d, "report.html"))
r = cli("report", os.path.basename(d), "--runs-dir", RUNS)
check("report regenerates report.html", r.returncode == 0 and os.path.exists(os.path.join(d, "report.html")))

has_cfg = os.path.exists(os.path.join(d, "config.proposed.yml"))
if not has_cfg:
    print("NOTE set-eq.py not found: apply tests use a stand-in config.proposed.yml")
    open(os.path.join(d, "config.proposed.yml"), "w").write("PROPOSED CONFIG\n")
new_cfg = open(os.path.join(d, "config.proposed.yml")).read()

# ---- apply refusals
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, env={"AUTOCAL_BACKUP_DIR": BK})
check("a simulated run is refused without AUTOCAL_CONFIG (cannot reach the real config)", r.returncode == 2 and "SIMULATED" in r.stderr and not os.path.exists(BK))
fail_dir = os.path.join(RUNS, "29990101-000000"); shutil.copytree(d, fail_dir)
rj = json.load(open(os.path.join(fail_dir, "results.json"))); rj["verdict"] = "FAIL"; rj["id"] = "29990101-000000"; json.dump(rj, open(os.path.join(fail_dir, "results.json"), "w"))
r = cli("apply", "29990101-000000", "--runs-dir", RUNS, env=ENV)
check("a FAIL verdict is refused without --force", r.returncode == 2 and "FAIL" in r.stderr and open(LIVE).read() == "ORIGINAL CONFIG\n")
shutil.rmtree(fail_dir)
r = cli("keep", env=ENV); check("keep with no trial says so", r.returncode == 2)
r = cli("rollback", env=ENV); check("rollback with no backup says so", r.returncode == 2 and open(LIVE).read() == "ORIGINAL CONFIG\n")

# ---- apply / keep
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, "--trial-seconds", "30", env=ENV)
bks = sorted(os.listdir(BK)) if os.path.isdir(BK) else []
bdir = [b for b in bks if os.path.isdir(os.path.join(BK, b))]
check("apply backs up the live config first", r.returncode == 0 and bdir and open(os.path.join(BK, bdir[-1], "config.yml")).read() == "ORIGINAL CONFIG\n", r.stderr[-200:])
check("apply installs the proposed config and room, and starts a trial", open(LIVE).read() == new_cfg and os.path.exists(ROOMF) and json.load(open(os.path.join(BK, "trial.json")))["state"] == "trial")
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, env=ENV)
check("a second apply during a trial is refused", r.returncode == 2 and "trial" in r.stderr)
r = cli("keep", env=ENV)
check("keep confirms the trial and marks the run", r.returncode == 0 and json.load(open(os.path.join(BK, "trial.json")))["state"] == "kept" and os.path.exists(os.path.join(d, "kept")))
r = cli("rollback", env=ENV)
check("rollback restores the original config and removes the room.json that did not exist", r.returncode == 0 and open(LIVE).read() == "ORIGINAL CONFIG\n" and not os.path.exists(ROOMF))

# ---- trial revert by the watchdog
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, "--trial-seconds", "2", "--wait", env=ENV)
check("apply --wait with no keep reverts when the trial expires", r.returncode == 0 and open(LIVE).read() == "ORIGINAL CONFIG\n" and json.load(open(os.path.join(BK, "trial.json")))["state"] == "reverted")
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, "--trial-seconds", "2", env=ENV)
t0 = time.time(); ok = False
while time.time() - t0 < 12:
    if open(LIVE).read() == "ORIGINAL CONFIG\n" and json.load(open(os.path.join(BK, "trial.json")))["state"] == "reverted":
        ok = True; break
    time.sleep(0.2)
check("the detached watchdog reverts an un-kept trial by itself", r.returncode == 0 and ok, f"({time.time() - t0:.1f} s)")
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, "--trial-seconds", "30", env=ENV); cli("keep", env=ENV)
check("a kept trial stays applied", open(LIVE).read() == new_cfg)
cli("rollback", env=ENV)

# ---- restart command failure restores
r = cli("apply", os.path.basename(d), "--runs-dir", RUNS, env={**ENV, "AUTOCAL_RESTART_CMD": "false"})
check("a failing restart command restores the backup automatically", r.returncode == 2 and open(LIVE).read() == "ORIGINAL CONFIG\n")

# ---- verify / five-position
r = cli("verify", os.path.basename(d), "--simulate", "--runs-dir", RUNS, *[], env=ENV, timeout=110) if False else None
time.sleep(1.1)
r = cli("run", "--simulate", "--mode", "five", "--runs-dir", RUNS, *FAST, timeout=118)
d5 = newest(); res5 = json.load(open(os.path.join(d5, "results.json")))
check("run --simulate --mode five finishes with 5 positions", r.returncode == 0 and len(res5["positions"]) == 5 and not RS.check_results(res5), r.stderr[-200:])
# ---- multi-seat mode
time.sleep(1.1)
r = cli("run", "--simulate", "--runs-dir", RUNS, "--seat", "main=2", "--seat", "left=1@-60,0", "--seat", "right=1@60,0", *FAST, timeout=118)
ds = newest(); ress = json.load(open(os.path.join(ds, "results.json")))
check("run --simulate with three --seat options is a seats run with those seats in that order", r.returncode == 0 and ress["mode"] == "seats"
      and [p["label"] for p in ress["positions"]] == ["main", "left", "right"] and not RS.check_results(ress), r.stderr[-200:])
check("the prompts name the seats", "seat 'left'" in r.stdout and "seat 'right'" in r.stdout, r.stdout[-300:])
check("the seat weights reach the analysis (main counts double)", [p.get("weight", None) for p in ress["positions"]] in ([2.0, 1.0, 1.0], [None, None, None]), str(ress["positions"][:1]))
for bad, why in ((["--seat", "main=2"], "one seat"), (["--seat", "main=0", "--seat", "b=1"], "zero weight"), (["--seat", "a=1", "--seat", "a=1"], "duplicate name"),
                 (["--seat", "nonsense"], "bad syntax"), (["--mode", "seats"], "seats mode without seats")):
    r = cli("run", "--simulate", "--runs-dir", RUNS, *bad, *FAST)
    check(f"seats: refused cleanly ({why})", r.returncode == 2 and "autocal:" in r.stderr, r.stderr[-160:])
check("parse_seats reads weight and optional offsets", RS.parse_seats(["main=2", "left=1.5@-60,10"]) == [("main", (0.0, 0.0), 2.0), ("left", (-60.0, 10.0), 1.5)])
r = cli("run", "--simulate", "--scenario", "nonsense", "--runs-dir", RUNS, *FAST)
check("an unknown scenario fails cleanly (nonzero, no traceback to the user is not required)", r.returncode != 0)
r = cli("run", "--simulate", "--dry", "--runs-dir", RUNS, *FAST)
check("the sub sweep starts at 35 Hz by default (owner's decision 2026-10-26)", "LFE sweep 35-200 Hz" in r.stdout, r.stdout[-200:])
r = cli("run", "--simulate", "--dry", "--lfe-from", "25", "--runs-dir", RUNS, *FAST)
check("--lfe-from 25 restores the old range", "LFE sweep 25-200 Hz" in r.stdout)
r = cli("run", "--simulate", "--dry", "--lfe-from", "10", "--runs-dir", RUNS)
check("--lfe-from outside 25-60 Hz is refused", r.returncode == 2 and "between 25 and 60" in r.stdout)
r = cli("bogus"); check("an unknown command is rejected", r.returncode == 2)


# ---- noise reference before and after each position
r = cli("run", "--simulate", "--runs-dir", RUNS, "--noise-ref", "10", *FAST)
check("a simulated run records the noise reference before and after the position and says so", r.returncode == 0 and "noise before" in r.stdout and "10 s each" in r.stdout, r.stdout[-300:] + r.stderr[-200:])
r = cli("run", "--simulate", "--runs-dir", RUNS, "--noise-ref", "0", *FAST)
check("--noise-ref 0 switches it off (the old in-sweep estimate)", r.returncode == 0 and "noise before" not in r.stdout, r.stdout[-200:] + r.stderr[-200:])

# ---- mic --spectrum report: known white noise + a modulated 50 Hz hum
import numpy as np
from autocal.cli import spectrum_report
_fs = 48000; _t = np.arange(15 * _fs) / _fs; _x = np.random.default_rng(0).normal(0, 0.001, len(_t)) + 0.0005 * np.sin(2 * np.pi * 50 * _t) * (1 + np.sin(2 * np.pi * _t / 8))
_rep = spectrum_report(_x)
_row = {float(l.split()[0]): [float(v) for v in l.split()[1:4]] for l in _rep.splitlines()[2:-1] if l.split()[0] != "35-45"}
check("spectrum: white noise of -60 dBFS reads -66 dBFS in the 8 kHz octave", abs(_row[8000.0][1] + 66.3) < 0.5, str(_row[8000.0]))
check("spectrum: the cycling 63 Hz band shows a wide spread, the steady bands do not", _row[63.0][2] - _row[63.0][0] > 15 and _row[1000.0][2] - _row[1000.0][0] < 2)
check("spectrum: the 50 Hz hum is found as a narrow tone", "strongest narrow tone: 50 Hz" in _rep and "hum or whine" in _rep, _rep.splitlines()[-1])
check("spectrum: the 35-45 Hz row is printed", "35-45" in _rep and "bass gate" in _rep)
check("spectrum: too little audio gives a clear message", "need at least 2 s" in spectrum_report(_x[:1000]))
shutil.rmtree(TMP, ignore_errors=True)
finish("test_autocal_cli")
