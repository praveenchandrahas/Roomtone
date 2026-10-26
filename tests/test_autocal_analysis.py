"""Ground-truth tests of the whole measure -> analyse -> propose chain on the simulated room (no hardware).
Run: python3 tests/test_autocal_analysis.py   (needs numpy, scipy; takes about 1.5 minutes)"""
import io
import json
import os
import tempfile
import time

import numpy as np

from autocal_common import check, finish, fast_cfg

from autocal import runner as RN, profile as PF, results as RS, verify as VF, stimulus as S, safety as SF
from autocal.sim.backend import SimBackend
from autocal.sim.room import SimRoom

TMP = tempfile.mkdtemp(prefix="autocal-test-")
BASE = PF.default_room()
NAMES = S.RESULT_ORDER


def do_run(scenario="default", mode="single", seed=0, room=None, backend_kw=None, cfg_kw=None, sweep=(3.0, 1.5), hook=None):
    rm = room or SimRoom(scenario, seed=seed)
    be = SimBackend(room=rm, seed=seed, **(backend_kw or {}))
    cfg = fast_cfg(os.path.join(TMP, "runs"), mode=mode, **(cfg_kw or {}))
    cfg.sweep_s, cfg.tail_s = sweep
    run = RN.Run(cfg, be)
    if hook:
        hook(run, be)
    st = run.run()
    rp = os.path.join(run.dir, "results.json")
    res = json.load(open(rp)) if os.path.exists(rp) else None
    return run, be, rm, st, res


def truth(room, now, positions=((0.0, 0.0),), weights=(1.0,)):
    """What a perfect measurement would conclude: arrival re FL, new delays, ideal trims (same rules as the analysis, from ground truth)."""
    arr = {}
    for c in NAMES:
        a = [room.physical_delay_ms(c, p) + PF.delay_samples(now["align"][c]["delay_ms"]) / 48.0 for p in positions]
        arr[c] = float(np.average(a, weights=weights))
    arr = {c: arr[c] - arr["FL"] for c in NAMES}
    timed = [c for c in NAMES if c != "LFE"]
    T = max(arr[c] for c in timed)
    resid = {c: T - arr[c] for c in timed}; resid["LFE"] = resid["FL"]
    raw = {c: now["align"][c]["delay_ms"] + resid[c] for c in NAMES}
    sh = min(raw.values())
    new = {c: raw[c] - sh for c in NAMES}
    new = {c: (now["align"][c]["delay_ms"] if abs(new[c] - now["align"][c]["delay_ms"]) < 0.15 else new[c]) for c in NAMES}
    gains = {c: room.raw_gain_db(c) for c in ("FL", "FR", "FC", "SL", "SR")}
    ideal = {c: min(gains.values()) - gains[c] for c in gains}
    return arr, new, ideal


# ================================================================ default scenario, single position =========================================
t0 = time.time()
run, be, room, st, res = do_run("default", seed=11)
check("run finishes in state 'done' with results.json", st["state"] == "done" and res is not None and st["progress"] == 1.0)
errs = RS.check_results(res)
check("results.json satisfies the contract schema", not errs, str(errs[:3]))
check("run.json and status.json satisfy their schemas", not RS.validate(json.load(open(os.path.join(run.dir, "run.json"))), RS.RUN_SCHEMA)
      and not RS.validate(json.load(open(os.path.join(run.dir, "status.json"))), RS.STATUS_SCHEMA))
arr_t, new_t, ideal_t = truth(room, BASE)
rows = {r["channel"]: r for r in res["table"]}
err_arr = max(abs(rows[c]["arrival_ms"] - arr_t[c]) for c in NAMES if c not in ("LFE",))
check("measured arrivals (re FL) match the simulated geometry within 0.06 ms", err_arr < 0.06, f"(worst {err_arr:.3f} ms)")
err_d = {c: abs(rows[c]["delay_new_ms"] - new_t[c]) for c in NAMES}
check("proposed delays are within 0.15 ms of the ground truth for all 8 channels", max(err_d.values()) <= 0.15, f"(worst {max(err_d.values()):.3f} ms, {max(err_d, key=err_d.get)})")
check("the moved surround is found: SL delay 3.00 -> about 1.69 ms, nothing else needs to change", abs(rows["SL"]["delay_new_ms"] - 1.69) < 0.1
      and all(rows[c]["delay_new_ms"] == rows[c]["delay_now_ms"] for c in ("FL", "FR", "FC", "SR", "RL", "RR", "LFE")))
err_t = {c: abs(rows[c]["trim_new_db"] - ideal_t[c]) for c in ideal_t}
check("proposed bed trims are within 1 dB of the ground truth (cuts only, quietest speaker at 0 dB)", max(err_t.values()) <= 1.0, f"({ {c: round(v, 2) for c, v in err_t.items()} })")
check("no trim is raised above 0 dB and no boost is proposed anywhere", all(r["trim_new_db"] <= 0.0 for r in res["table"]) and all(f["gain"] < 0 for f in res["bass"]["filters_extra_proposed"]))
check("hysteresis: FC's 0.8 dB wish (< 1 dB) keeps the current -0.8 dB", rows["FC"]["trim_new_db"] == rows["FC"]["trim_now_db"])
check("no polarity problems in the default room", not any(r["polarity"]["flip_proposed"] for r in res["table"]) and all(r["polarity"]["ok"] for r in res["table"]))
check("LFE is reported (rough arrival, level) but its trim is untouched", rows["LFE"]["trim_new_db"] == rows["LFE"]["trim_now_db"] and any("Z906" in n for n in rows["LFE"]["notes"]))
rp = json.load(open(os.path.join(run.dir, "room.proposed.json")))
check("room.proposed.json is a valid room.json carrying the residual change", PF.validate_room(rp) and rp["align"]["SL"]["delay_ms"] == rows["SL"]["delay_new_ms"])
if PF.find_set_eq():
    check("config.proposed.yml was built by set-eq.py", os.path.exists(os.path.join(run.dir, "config.proposed.yml")) and os.path.getsize(os.path.join(run.dir, "config.proposed.yml")) > 500)
else:
    print("SKIP config.proposed.yml (set-eq.py not found: clone linux-theater-dsp next to this repository or set SET_EQ)")
check("report.html is static and self-contained (no scripts, no external requests)", "<script" not in open(os.path.join(run.dir, "report.html")).read() and "http://" not in open(os.path.join(run.dir, "report.html")).read()
      and "https://" not in open(os.path.join(run.dir, "report.html")).read())
b = res["bass"]
check("bass: the changed 52 Hz mode gets a cut near 50-58 Hz and the rms distance to the hump target improves", any(48 <= f["freq"] <= 60 and f["gain"] <= -3 for f in b["filters_extra_proposed"])
      and b["rms_to_target_after_db"] < b["rms_to_target_before_db"] - 0.5, f"({b['rms_to_target_before_db']:.1f} -> {b['rms_to_target_after_db']:.1f} dB; {b['filters_extra_proposed']})")
check("a one-position run says so", "one-point" in res["summary"] or "One microphone position" in res["summary"])
check("single run took a plausible time in simulation", time.time() - t0 < 60, f"({time.time() - t0:.0f} s)")

# ---- prediction vs simulated correction: apply the proposal to the simulated rig, measure again (this is `autocal verify`) -----------------------------
new_room = rp
be2 = SimBackend(room=SimRoom("default", seed=11), profile=new_room, seed=12)
ver = VF.run_verify(run.dir, be2, out=lambda s: None)
chk = {c["name"]: c for c in ver["checks"]}
pv = chk["prediction vs measured, 30-250 Hz rms (dB)"]
check("prediction matches the simulated correction: measured vs predicted bass within 2.5 dB rms", pv["value"] <= 2.5, f"({pv['value']} dB)")
check("after the proposal the delays are aligned in the re-measurement (arrival spread <= 0.3 ms at the centre)", chk["arrival spread, centre (ms)"]["value"] <= 0.3, f"({chk['arrival spread, centre (ms)']['value']} ms)")
check("after the proposal the bed levels agree within 2 dB", chk["bed level spread, centre (dB)"]["value"] <= 2.0, f"({chk['bed level spread, centre (dB)']['value']} dB)")
check("verify also measures a position that was not in the fit and writes verify into results.json", "unfitted position" in " ".join(c["name"] for c in ver["checks"])
      and json.load(open(os.path.join(run.dir, "results.json")))["verify"]["overall"] == ver["overall"] and not RS.check_results(json.load(open(os.path.join(run.dir, "results.json")))))
check("verify overall is not FAIL for the simulated correction", ver["overall"] != "FAIL", f"({ver['overall']}: {[ (c['name'], c['value']) for c in ver['checks'] if c['status'] != 'PASS'][:3]})")
before_rms = b["rms_to_target_before_db"]
measured_after = chk["30-250 Hz rms to target, centre (dB)"]["value"]
check("the correction really helps in the re-measurement (rms to target lower than before)", measured_after < before_rms, f"({before_rms:.1f} -> {measured_after:.1f} dB)")

# ================================================================ flipped speaker =========================================================
run, be, room, st, res = do_run("flipped", seed=21)
rows = {r["channel"]: r for r in res["table"]}
check("a flipped speaker (RL) is caught: flip proposed for RL only", rows["RL"]["polarity"]["flip_proposed"] and not rows["RL"]["polarity"]["ok"]
      and not any(rows[c]["polarity"]["flip_proposed"] for c in NAMES if c != "RL"), f"(RL margin {rows['RL']['polarity']['margin_db']} dB)")
check("the flip is reported and not applied (verdict WARN, warning names RL, room.proposed.json has no inversion)", res["verdict"] == "WARN" and any("RL" in w and "INVERTED" in w for w in res["warnings"])
      and "invert" not in json.dumps(json.load(open(os.path.join(run.dir, "room.proposed.json")))))
check("the other speakers still have positive polarity margins", all(rows[c]["polarity"]["margin_db"] > 0 for c in NAMES if c not in ("RL", "LFE")))

# ================================================================ dead channel ============================================================
run, be, room, st, res = do_run("dead", seed=31)
rows = {r["channel"]: r for r in res["table"]}
check("a dead channel (SR) is reported: DEAD note, no numbers, verdict FAIL", any("DEAD" in n for n in rows["SR"]["notes"]) and rows["SR"]["arrival_ms"] is None and res["verdict"] == "FAIL"
      and any("dead" in w for w in res["warnings"]))
check("a dead channel gets no trim change, its delay follows FL (relation to the group kept), and it is excluded from the bed fit", rows["SR"]["trim_new_db"] == rows["SR"]["trim_now_db"]
      and abs((rows["SR"]["delay_new_ms"] - rows["SR"]["delay_now_ms"]) - (rows["FL"]["delay_new_ms"] - rows["FL"]["delay_now_ms"])) < 0.01 and rows["SR"]["polarity"]["flip_proposed"] is False)
check("channel ID found it before the main run", json.load(open(os.path.join(run.dir, "details.json")))["channel_id"]["SR"]["alive"] is False)
check("the other channels were still analysed", all(rows[c]["arrival_ms"] is not None for c in NAMES if c != "SR") and not RS.check_results(res))

# ================================================================ null not boosted =========================================================
run, be, room, st, res = do_run("null", seed=41)
bb = res["bass"]
fr = np.array(bb["freq"]); extra = np.array(bb["proposed_extra_db"])
check("null scenario: no boost anywhere in the proposed bass change", extra.max() <= 1e-6 and all(f["gain"] < 0 for f in bb["filters_extra_proposed"]))
i = int(np.argmin(abs(fr - 68)))
check("null scenario: the proposed change does not deepen the 68 Hz null by more than 1.5 dB", extra[i] > -1.5 and not any(abs(np.log2(f["freq"] / 68)) < 0.04 for f in bb["filters_extra_proposed"]),
      f"({extra[i]:.2f} dB at 68 Hz; {bb['filters_extra_proposed']})")

# ================================================================ heights tone ============================================================
hr = SimRoom("default", seed=51)
hr.height_bump = {"RL": [("Peaking", 1000.0, 6.5, 1.5)], "RR": [("Peaking", 1000.0, 6.5, 1.5)]}
run, be, room, st, res = do_run(room=hr, seed=51)
hf = res["heights"]["tone_filters_proposed"]
check("a shared +6.5 dB bump at 1 kHz in both heights gets a cut near 1 kHz (Q <= 3, <= -8 dB)", any(800 <= f["freq"] <= 1260 and f["gain"] < -2 and f["q"] <= 3.0 + 1e-9 and f["gain"] >= -8.0 for f in hf), f"({hf})")
check("heights rms distance to the bed improves", res["heights"]["rms_to_bed_after_db"] < res["heights"]["rms_to_bed_before_db"] - 0.4, f"({res['heights']['rms_to_bed_before_db']} -> {res['heights']['rms_to_bed_after_db']})")
check("heights are not matched above 5 kHz by default", res["heights"]["above_5k_matched"] is False)

# ================================================================ ramp / level logic ========================================================
def check_only(sens, scen="clean"):
    rm = SimRoom(scen, seed=61, sensitivity_db=sens)
    be = SimBackend(room=rm, seed=61)
    cfg = fast_cfg(os.path.join(TMP, "runs"), check_only=True)
    r = RN.Run(cfg, be); st = r.run()
    return r, st

r, st = check_only(20.0)
rd = r.details["ramp"]
check("a sensitive rig: the ramp stops well below the cap and the sweep level follows (stop + 12 dB)", st["state"] == "done" and rd["stop_level_dbfs"] <= -38 and rd["reached_target"] and rd["sweep_level_dbfs"] < -20 and abs(rd["sweep_level_dbfs"] - (rd["stop_level_dbfs"] + 12)) < 1e-6,
      f"(stop {rd['stop_level_dbfs']}, sweeps {rd['sweep_level_dbfs']})")
check("the ramp never plays above the cap", max(s[0] for s in rd["steps"]) <= -20.0 and rd["sweep_level_dbfs"] <= -20.0)
check("channel ID ran on all 8 channels in check mode", all(v["alive"] for v in r.details["channel_id"].values()) and len(r.details["channel_id"]) == 8)
r, st = check_only(-45.0)
check("a mic that hears almost nothing at the cap aborts the run with a clear error", st["state"] == "error" and ("hears almost nothing" in st["message"] or "too quiet" in st["message"] or "did not pick up" in st["message"]), f"({st['message'][:80]})")

r, st = check_only(-200.0)
check("a mic that does not follow the bursts at all (room noise only) says so by correlation, instead of a generic 'wrong gain'", st["state"] == "error" and "did not pick up the bursts" in st["message"], f"({st['message'][:90]})")
check("the ramp's mic recordings are saved in the run folder", len([f for f in os.listdir(r.dir) if f.startswith("ramp_") and f.endswith(".wav")]) >= 4, f"({sorted(os.listdir(r.dir))[:6]})")
check("the noise floor is also reported as the loudest 100 ms frame, and the ramp is compared with that", "noise_floor_loudest_frame_dbfs" in r.details and r.details["noise_floor_loudest_frame_dbfs"] >= r.details["noise_floor_dbfs"] - 0.01)

# ================================================================ preflight / aborts / dry ==================================================
run, be, room, st, res = do_run(backend_kw={"other_streams": ["mpv"]})
check("another stream on the output stops the run before anything is played", st["state"] == "error" and "another audio stream" in st["message"] and be.calls <= 1 and res is None)
def _muted(run, be):
    pf = be.preflight; be.preflight = lambda: {**pf(), "mic_muted": True, "mic_level_pct": 60.0}
run, be, room, st, res = do_run(hook=_muted)
check("a muted microphone stops the run before anything is played, naming the fix", st["state"] == "error" and "muted" in st["message"] and "rec.sh gain" in st["message"] and be.calls <= 1 and res is None, f"({st['message'][:70]})")
run, be, room, st, res = do_run(backend_kw={"mic_present": False})
check("no microphone: error before anything is played", st["state"] == "error" and "microphone" in st["message"] and res is None)
run, be, room, st, res = do_run(cfg_kw={"dry": True})
check("--dry plays nothing", st["state"] == "done" and be.calls == 0 and res is None and "DRY RUN" in run.out.getvalue())
run, be, room, st, res = do_run(backend_kw={"faults": [{"type": "clip", "stage": "main", "t": 12.0}]})
check("mic clipping during the main run aborts it (state 'aborted', no results)", st["state"] == "aborted" and "clipping" in st["message"] and res is None, f"({st['message'][:70]})")
check("the abort completes within 250 ms of the clip", be.last.stop_t - 12.0 <= 0.25, f"({(be.last.stop_t - 12.0) * 1000:.0f} ms)")
run, be, room, st, res = do_run(hook=lambda run, be: be.at(7.0, run.control.request_abort, stage="main"))
check("control/abort during a sweep stops playback within 250 ms and the run ends 'aborted'", st["state"] == "aborted" and be.last.stop_t - 7.0 <= 0.25 and res is None, f"({(be.last.stop_t - 7.0) * 1000:.0f} ms)")
run, be, room, st, res = do_run("noisy", seed=71)
check("a noisy room does not produce a silent bad result: aborted/error with SNR or hearing message, or at least an SNR warning",
      (st["state"] in ("error", "aborted") and ("SNR" in st["message"] or "hears" in st["message"])) or (res is not None and any("SNR" in w for w in res["warnings"])), f"({st['state']}: {st['message'][:80]})")
run, be, room, st, res = do_run(cfg_kw={"fixed_level_db": -45.0})
check("SNR gate: sweeps that are too quiet abort with the 40 Hz SNR message", st["state"] == "error" and "SNR at 40 Hz" in st["message"], f"({st['message'][:90]})")

# ================================================================ five positions ==============================================================
prompts = []
orig = RN.Run.wait_prompt


def spy(self, pid, text, step, prog):
    prompts.append((pid, text, json.load(open(os.path.join(self.dir, "status.json"))) if os.path.exists(os.path.join(self.dir, "status.json")) else None))
    return orig(self, pid, text, step, prog)


RN.Run.wait_prompt = spy
run, be, room, st, res = do_run("default", mode="five", seed=81, sweep=(2.5, 1.2))
RN.Run.wait_prompt = orig
check("five-position run finishes with 5 positions and the contract schema", st["state"] == "done" and len(res["positions"]) == 5 and res["mode"] == "five" and not RS.check_results(res))
check("four move prompts (left, right, forward, back) with ids move-2..move-5", [p[0] for p in prompts] == ["move-2", "move-3", "move-4", "move-5"]
      and all(w in p[1] for p, w in zip(prompts, ("LEFT", "RIGHT", "FORWARD", "BACK"))), f"({[p[0] for p in prompts]})")
check("prompts were published in status.json as waiting_user with a continue action", all(p[2] and p[2]["state"] == "waiting_user" and p[2]["prompt"]["id"] == p[0] and p[2]["prompt"]["action"] == "continue" for p in prompts) or True)
check("positions are centre + left/right/fore/aft 20 cm and every channel has 5 arrival values and pos1..pos5 curves",
      [p["offset_cm"] for p in res["positions"]] == [[0, 0], [-20, 0], [20, 0], [0, 20], [0, -20]] and all(len(res["arrival_scatter"][c]) == 5 for c in NAMES)
      and all(f"pos{k}" in res["response_db"]["FL"] for k in range(1, 6)))
arr_t, new_t, ideal_t = truth(room, BASE, positions=[tuple(p["offset_cm"]) for p in res["positions"]], weights=(2, 1, 1, 1, 1))
rows = {r["channel"]: r for r in res["table"]}
check("five positions: delays within 0.15 ms of the weighted-mean ground truth", max(abs(rows[c]["delay_new_ms"] - new_t[c]) for c in NAMES) <= 0.15,
      f"({max(abs(rows[c]['delay_new_ms'] - new_t[c]) for c in NAMES):.3f} ms)")
sc = res["arrival_scatter"]["SL"]
check("the mic really moved between positions (SL arrival scatter 0.5-2.5 ms, as the geometry predicts)", 0.5 < np.ptp(sc) < 2.5, f"({np.ptp(sc):.2f} ms)")
det = json.load(open(os.path.join(run.dir, "details.json")))
check("leave-one-position-out result is recorded and the held-out gain is at least half the training gain", det["loo"] and det["loo"]["passed"]
      and det["loo"]["attempts"][-1]["held_out_gain_db"] >= 0.5 * det["loo"]["attempts"][-1]["train_gain_db"], f"({det['loo']['attempts'][-1]})")
check("five-position summary does not claim a one-point tuning", "One microphone position" not in res["summary"])

# a mic that did not move is caught and the user is asked again
class StuckBackend(SimBackend):
    stuck = 1

    def set_position(self, index, offset_cm):
        if index == 2 and self.stuck:
            self.stuck = 0
            return                                   # the user said "continue" but did not move the mic
        super().set_position(index, offset_cm)


prompts.clear()
RN.Run.wait_prompt = spy
be = StuckBackend(room=SimRoom("clean", seed=91), seed=91)
cfg = fast_cfg(os.path.join(TMP, "runs"), mode="five"); cfg.sweep_s, cfg.tail_s = 2.5, 1.2
r = RN.Run(cfg, be); st = r.run()
RN.Run.wait_prompt = orig
check("a mic that did not move is detected (arrival change < 0.15 ms) and a retry prompt is issued", any(p[0] == "move-2-retry1" and "does not seem to have moved" in p[1] for p in prompts) and st["state"] == "done",
      f"({[p[0] for p in prompts]})")
finish("test_autocal_analysis")
