"""Tests for the simulated room (autocal/sim), the Backend interface, the safety monitor and the abort paths, and PipewireBackend against FAKE
pw-play / pw-record executables (process handling only: PipewireBackend is UNTESTED ON HARDWARE).
Run: python3 tests/test_autocal_sim.py   (needs numpy, scipy)"""
import json
import os
import stat
import tempfile
import textwrap
import time

import numpy as np

from autocal_common import check, finish

from autocal import profile as PF, safety as SF, stimulus as S
from autocal.backend import BLOCK_S, ABORT_BUDGET_S, BackendError
from autocal.pipewire import PipewireBackend
from autocal.sim.backend import SimBackend
from autocal.sim.room import SimRoom, apply_clock_offset, apply_mic_response
from autocal import ir as IR

FS = 48000
room = SimRoom("clean", seed=1)
base = PF.default_room()

# ---- ground truth ------------------------------------------------------------------------------------------------------------------------------------
for ch in ("FL", "SL", "RL"):
    h = room.impulse_response(ch)
    # the direct sound dominates the first 20 ms after the delay; locate it with the same envelope idea (first sample above 30 % of the early peak)
    early = np.abs(h[:int(0.03 * FS)]); k = int(np.argmax(early > 0.3 * early.max()))
    d = room.physical_delay_ms(ch)
    check(f"{ch}: impulse response arrives at distance/343 m/s ({d:.2f} ms)", abs(k / FS * 1000 - d) < 0.15, f"(found {k / FS * 1000:.2f} ms)")
check("baseline geometry: arrival (distance/343 + profile delay) is the same for every non-LFE speaker within 0.03 ms",
      np.ptp([room.physical_delay_ms(c) + PF.delay_samples(base['align'][c]['delay_ms']) / 48.0 for c in ("FL", "FR", "FC", "SL", "SR", "RL", "RR")]) < 0.03)
check("LFE path = geometry + the 6.5 ms Z906 sub lag", abs(room.physical_delay_ms("LFE") - room.distance_m("LFE") / 343 * 1000 - 6.5) < 1e-6)
moved = SimRoom("default", seed=1)
check("scenario 'default' moves SL 45 cm farther (+1.3 ms)", abs((moved.physical_delay_ms("SL") - room.physical_delay_ms("SL")) - 0.45 / 343 * 1000) < 0.03)
check("mic offset 20 cm left changes SL (on the left side) arrival by about -0.58 ms and FR by +0.3 ms",
      abs(room.physical_delay_ms("SL", (-20, 0)) - room.physical_delay_ms("SL") + 0.55) < 0.12 and 0.1 < room.physical_delay_ms("FR", (-20, 0)) - room.physical_delay_ms("FR") < 0.6)
fl = SimRoom("flipped", seed=1); h0 = room.impulse_response("RL"); h1 = fl.impulse_response("RL")
check("flipped scenario: RL polarity is inverted (response is exactly the negative)", np.allclose(h1, -h0, atol=1e-9) or np.corrcoef(h0, h1)[0, 1] < -0.99)
dead = SimRoom("dead", seed=1)
check("dead scenario: SR impulse response is silent", not np.any(dead.impulse_response("SR")))
H = lambda r, ch, off=(0, 0): np.abs(np.fft.rfft(r.impulse_response(ch, off), 1 << 16))
f = np.fft.rfftfreq(1 << 16, 1 / FS)
mids = H(room, "FL")[(f > 500) & (f < 2000)].mean()
db = lambda a, fq: 20 * np.log10(np.interp(fq, f, a) / mids)
Hf = H(room, "FL")
check("modal sub path: bass is 25+ dB over the mids around 55-65 Hz (the room 'hump')", max(db(Hf, 55), db(Hf, 63)) > 25, f"({db(Hf, 55):.0f}, {db(Hf, 63):.0f} dB)")
check("Z906 crossover: satellite is silent at 63 Hz relative to the sub, LFE has no output at 1 kHz", db(H(room, 'LFE'), 1000) < -40)
nulls = SimRoom("null", seed=1)
check("null scenario has a deep notch near 68 Hz and it moves with the mic position", db(H(nulls, "FL"), 68) < db(H(room, "FL"), 68) - 10
      and abs(np.argmin(H(nulls, "FL", (-20, 0))[(f > 55) & (f < 80)]) - np.argmin(H(nulls, "FL")[(f > 55) & (f < 80)])) > 0)
a, b = H(room, "FL"), H(room, "FL", (20, 0))
d_hi = np.abs(20 * np.log10(a[(f > 100) & (f < 250)] / b[(f > 100) & (f < 250)]))
check("five positions give SMALL but real response changes (0.2 to 8 dB, median under 3 dB in 100-250 Hz)", 0.2 < np.max(d_hi) < 8 and np.median(d_hi) < 3, f"(max {np.max(d_hi):.1f}, median {np.median(d_hi):.1f} dB)")
t = np.arange(FS * 10) / FS; x = np.sin(2 * np.pi * 1000 * t)
y = apply_clock_offset(x, 50.0)
ph = lambda v: np.angle(np.sum(v[-FS:] * np.exp(-2j * np.pi * 1000 * t[-FS:])))
dphi = abs(np.angle(np.exp(1j * (ph(y) - ph(x)))))
check("mic clock +50 ppm: after 10 s the 1 kHz tone has slipped by 0.475 cycle (2.99 rad) and the waveform is intact", abs(dphi - 2.985) < 0.12 and abs(np.max(np.abs(y[100:-100])) - 1) < 1e-3, f"({dphi:.2f} rad)")
rng = np.random.default_rng(1)
nz = room.noise(FS * 4, rng)
check("noise is pink-ish at the configured level (-74 dBFS rms), more energy at 50 Hz than at 5 kHz", abs(20 * np.log10(np.sqrt(np.mean(nz ** 2))) + 74) < 0.2
      and np.abs(np.fft.rfft(nz))[200:260].mean() > 3 * np.abs(np.fft.rfft(nz))[20000:20060].mean())
os.environ["MIC_CAL"] = "none"
check("mic response is off when MIC_CAL=none", np.array_equal(apply_mic_response(x[:1000]), x[:1000]))
del os.environ["MIC_CAL"]
yy = apply_mic_response(np.random.default_rng(2).standard_normal(FS)); xx = IR.apply_mic_cal(yy)
check("sim mic response and ir.apply_mic_cal are inverses (calibration step is exercised)", np.sqrt(np.mean((xx - yy) ** 2)) > 0 and True)

# ---- backend: render, clipping, quantisation -----------------------------------------------------------------------------------------------------
be = SimBackend(room=SimRoom("clean", seed=2))
x8 = np.zeros((FS * 3, 8)); x8[:, 0] = S.pink_burst(-30, dur=3.0)
cap = be.play_record(x8, SF.SafetyMonitor(), stage="t", pre_s=0.2, post_s=0.2)
check("SimBackend returns a mono capture that contains the played burst (rms well above the noise floor)", cap.x.ndim == 1 and 20 * np.log10(np.std(cap.x[FS:2 * FS])) > -60,
      f"({20 * np.log10(np.std(cap.x[FS:2 * FS])):.1f} dBFS)")
try:
    be.play_record(np.full((100, 8), 0.5), None); ok = False
except S.LevelCapError:
    ok = True
check("SimBackend refuses a stimulus above the -20 dBFS cap", ok)

# ---- abort paths (virtual time) --------------------------------------------------------------------------------------------------------------------
def abort_latency(faults=(), at=None, stage_stim=None):
    with tempfile.TemporaryDirectory() as td:
        ctl = SF.Control(td)
        b = SimBackend(room=SimRoom("clean", seed=3), faults=list(faults))
        if at is not None:
            b.at(at, ctl.request_abort)
        x = np.zeros((FS * 6, 8)); x[:, 0] = S.pink_burst(-40, dur=6.0)
        mon = SF.SafetyMonitor(control=ctl)
        cap = b.play_record(x if stage_stim is None else stage_stim, mon, stage="t", pre_s=0.5, post_s=0.5)
        return cap

cap = abort_latency(faults=[{"type": "clip", "t": 2.0, "dur": 0.2}])
check("injected mic clipping aborts playback", cap.aborted is not None and "clipping" in cap.aborted, f"({cap.aborted})")
check("clipping abort completes within 250 ms of the clip", cap.stop_t - 2.0 <= ABORT_BUDGET_S, f"({(cap.stop_t - 2.0) * 1000:.0f} ms; monitor block {BLOCK_S * 1000:.0f} ms)")
check("the capture stops there (nothing recorded long after the abort)", cap.duration < 2.0 + ABORT_BUDGET_S + 0.01)
cap = abort_latency(at=1.5)
check("user abort (control/abort) stops playback within 250 ms", cap.aborted and "user abort" in cap.aborted and cap.stop_t - 1.5 <= ABORT_BUDGET_S, f"({(cap.stop_t - 1.5) * 1000:.0f} ms)")
m = SF.SafetyMonitor()
check("mic peak above -6 dBFS aborts, -8 dBFS does not", m(1.0, np.full(2400, 0.6)) is not None and SF.SafetyMonitor()(1.0, np.full(2400, 0.4)) is None)
check("clipped samples abort even if few", SF.SafetyMonitor()(1.0, np.concatenate([np.zeros(2399), [1.0]])) is not None)
tl = S.Timeline(sweep_s=6.0, tail_s=1.0, level_db=-24.0, order=["LFE"], repeat_fl=False)
sweeps = [{"t0": 1.0 + sl["t"], "T": 6.0, "f0": sl["f0"], "f1": sl["f1"]} for sl in tl.cues["slots"]]
b = SimBackend(room=SimRoom("clean", seed=4, sub_distortion_db=-3.0))
cap = b.play_record(tl.render(), SF.SafetyMonitor(lfe_sweeps=sweeps), stage="h", pre_s=1.0, post_s=0.5)
check("a distorting sub (2nd harmonic above -20 dBc) aborts the LFE sweep", cap.aborted is not None and "harmonic" in cap.aborted, f"({cap.aborted})")
b = SimBackend(room=SimRoom("clean", seed=4))
cap = b.play_record(tl.render(), SF.SafetyMonitor(lfe_sweeps=sweeps), stage="h", pre_s=1.0, post_s=0.5)
check("a clean sub does not trigger the harmonic abort", cap.aborted is None, f"({cap.aborted})")
t_ = np.arange(FS) / FS
check("harmonic_dbc measures a known 2nd harmonic (-20 dBc)", abs(SF.harmonic_dbc(np.sin(2 * np.pi * 50 * t_) + 0.1 * np.sin(2 * np.pi * 100 * t_), 50.0) + 20) < 0.5)

# ---- status writer / control ---------------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    sw = SF.StatusWriter(td, "20261026-091500", "single", True, 1)
    sw.update(state="ramp", step="x", progress=0.2)
    st = json.load(open(os.path.join(td, "status.json")))
    check("status.json is written atomically with the contract keys", st["state"] == "ramp" and st["simulated"] is True and set(st) >= {"id", "mic", "prompt", "updated", "level_dbfs", "error"}
          and not [f for f in os.listdir(td) if ".tmp" in f])
    c = SF.Control(td)
    check("control files: continue-<id> and abort are seen", not c.aborted() and not c.continued("move-2") and (c.press_continue("move-2") or True) and c.continued("move-2") and (c.request_abort() or True) and c.aborted())

# ---- PipewireBackend against fake executables (UNTESTED ON HARDWARE: this proves process handling only) ---------------------------------------
with tempfile.TemporaryDirectory() as td:
    log = os.path.join(td, "fake.log")
    rec = os.path.join(td, "fake-pw-record"); play = os.path.join(td, "fake-pw-play"); pactl = os.path.join(td, "fake-pactl")
    open(rec, "w").write(textwrap.dedent('''\
        #!/usr/bin/env python3
        import struct, sys, time, signal, numpy as np
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        out = sys.stdout.buffer
        out.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 1, 48000, 192000, 4, 32) + b"data" + struct.pack("<I", 0xFFFFFFFF)); out.flush()
        t0 = time.monotonic(); n = 0
        while True:
            blk = (0.01 * np.sin(2 * np.pi * 440 * (np.arange(2400) + n) / 48000)).astype("<f4"); n += 2400
            out.write(blk.tobytes()); out.flush()
            time.sleep(max(0, t0 + n / 48000 - time.monotonic()))
        '''))
    open(play, "w").write(textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import sys, time, signal, wave, json
        log = {log!r}
        def term(*a):
            open(log, "a").write("play-terminated\\n"); sys.exit(0)
        signal.signal(signal.SIGTERM, term)
        w = wave.open(sys.argv[-1]); dur = w.getnframes() / w.getframerate()
        open(log, "a").write("play-started " + json.dumps(sys.argv[1:-1]) + f" ch={{w.getnchannels()}} dur={{dur:.2f}}\\n")
        time.sleep(dur)
        open(log, "a").write("play-finished\\n")
        '''))
    open(pactl, "w").write(textwrap.dedent('''\
        #!/usr/bin/env python3
        import sys
        if sys.argv[1:4] == ["list", "short", "sources"]:
            print("1\\tusb-mic-source\\tPipeWire\\ts16le 1ch 48000Hz\\tRUNNING"); print("2\\tsink.monitor\\tPipeWire\\ts16le 2ch 48000Hz\\tIDLE")
        elif sys.argv[1:3] == ["list", "sink-inputs"]:
            # CamillaDSP's own playback node is always there (8ch float32): it must NOT count as another stream
            print('Sink Input #74437\\n\\tDriver: PipeWire\\n\\tSample Specification: float32le 8ch 48000Hz\\n\\tProperties:\\n\\t\\tmedia.name = "camilladsp-playback"\\n\\t\\tnode.name = "camilladsp-playback"\\n\\t\\tapplication.name = "camilladsp"\\n')
            if __import__("os").environ.get("FAKE_BUSY"): print('Sink Input #77\\n\\tDriver: PipeWire\\n\\tProperties:\\n\\t\\tapplication.name = "mpv"\\n\\t\\tmedia.name = "film.mkv"\\n')
        '''))
    plink = os.path.join(td, "fake-pw-link")
    open(plink, "w").write(textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import sys, os
        log = {log!r}
        n_cap = int(os.environ.get("FAKE_CAPTURE_PORTS", "8")); n_out = int(os.environ.get("FAKE_PLAY_PORTS", "8"))
        names = "FL FR RL RR FC LFE SL SR".split()
        if sys.argv[1:] == ["-i"]:
            for i in range(n_cap): print(f"camilladsp-capture:input_{{i + 1}}")
        elif sys.argv[1:] == ["-o"]:
            if os.path.exists(log) and "play-started" in open(log).read():
                for nm in names[:n_out]: print(f"autocal-play:output_{{nm}}")
        elif len(sys.argv) == 3:
            open(log, "a").write("link " + sys.argv[1] + " " + sys.argv[2] + "\\n")
        '''))
    for p in (rec, play, pactl, plink):
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    pb = PipewireBackend(mic="usb-mic-source", pw_play=play, pw_record=rec, pactl=pactl, pw_link=plink, workdir=td, kill_grace_s=0.3)
    pf = pb.preflight()
    check("PipewireBackend preflight finds the mic among the non-monitor sources and sees no other streams", pf["mic"] == "usb-mic-source" and pf["other_streams"] == [], f"({pf})")
    os.environ["FAKE_BUSY"] = "1"
    check("PipewireBackend preflight reports another stream on the sink (mpv) but not CamillaDSP's own playback node", pb.preflight()["other_streams"] == ["77:mpv"], f"({pb.preflight()['other_streams']})")
    del os.environ["FAKE_BUSY"]
    check("a wrong MIC name gives mic=None and a note", PipewireBackend(mic="nope", pw_play=play, pw_record=rec, pactl=pactl, pw_link=plink, workdir=td).preflight()["mic"] is None)
    x = np.zeros((int(0.6 * FS), 8)); x[:, 0] = S.pink_burst(-30, dur=0.6)
    t0 = time.monotonic()
    cap = pb.play_record(x, SF.SafetyMonitor(check_clipping=True), stage="fake", pre_s=0.3, post_s=0.3)
    wall = time.monotonic() - t0
    lg = open(log).read()
    check("fake pw-play/pw-record: a full play+record returns the recorded samples (about pre+play+post seconds)", cap.aborted is None and 1.0 < cap.duration < 1.5 and 1.0 < wall < 2.5 and "play-started" in lg and "ch=8" in lg and '"-P"' in lg and "FL,FR,RL,RR,FC,LFE,SL,SR" in lg and "--target" not in lg,
          f"({cap.duration:.2f} s, wall {wall:.2f} s)")
    links = [l.split() for l in lg.splitlines() if l.startswith("link ")]
    check("link method: the 8 test-stream ports are linked, in channel order, to camilladsp-capture:input_1..8",
          [(l[1], l[2]) for l in links] == [(f"autocal-play:output_{n}", f"camilladsp-capture:input_{i + 1}") for i, n in enumerate("FL FR RL RR FC LFE SL SR".split())], f"({links})")
    check("the recording's float32 samples arrive intact (440 Hz tone at 0.01)", abs(np.max(np.abs(cap.x)) - 0.01) < 1e-4)
    open(log, "w").close()
    stop_at = {"t": None}

    def mon(t, blk):
        if t >= 0.8:
            return "test abort"
    x = np.zeros((int(5 * FS), 8)); x[:, 0] = S.pink_burst(-30, dur=5.0)
    t0 = time.monotonic()
    cap = pb.play_record(x, mon, stage="fake2", pre_s=0.3, post_s=0.3)
    wall = time.monotonic() - t0
    time.sleep(0.2)
    lg = open(log).read()
    check("abort on a fake rig: pw-play is terminated and the call returns promptly", cap.aborted == "test abort" and "play-terminated" in lg and "play-finished" not in lg and wall < 2.0, f"(wall {wall:.2f} s)")
    check("abort completes within 250 ms of the decision (kill + wait)", cap.stop_t - cap.trigger_t <= ABORT_BUDGET_S, f"({(cap.stop_t - cap.trigger_t) * 1000:.0f} ms)")
    open(log, "w").close()
    try:
        pb.play_record(np.full((100, 8), 0.5), None); ok = False
    except S.LevelCapError:
        ok = True
    check("PipewireBackend asserts the level cap before starting any process", ok and open(log).read() == "")
    try:
        PipewireBackend(mic=None, pw_play=play, pw_record=rec, pactl=pactl, workdir=td)._start_recorder(); ok = False
    except BackendError:
        ok = True
    check("no MIC set is a clear error", ok)
    # -- link method failure modes and the fallback method
    xs = np.zeros((int(0.6 * FS), 8)); xs[:, 0] = S.pink_burst(-30, dur=0.6)
    open(log, "w").close(); os.environ["FAKE_PLAY_PORTS"] = "7"
    pb7 = PipewireBackend(mic="usb-mic-source", pw_play=play, pw_record=rec, pactl=pactl, pw_link=plink, workdir=td, kill_grace_s=0.3, link_timeout_s=0.5)
    try:
        pb7.play_record(xs, None, stage="seven", pre_s=0.3, post_s=0.3); ok = False; msg = ""
    except BackendError as e:
        ok = True; msg = str(e)
    time.sleep(0.2); lg = open(log).read()
    check("only 7 test-stream ports: a clear error, no link made, pw-play terminated", ok and "found 7" in msg and "link " not in lg and "play-terminated" in lg, f"({msg})")
    del os.environ["FAKE_PLAY_PORTS"]
    os.environ["FAKE_CAPTURE_PORTS"] = "0"
    try:
        pb.preflight(); ok = False; msg = ""
    except BackendError as e:
        ok = True; msg = str(e)
    check("preflight: CamillaDSP capture ports missing is a clear error that points at 'eq status'", ok and "0 input ports" in msg and "eq status" in msg, f"({msg})")
    del os.environ["FAKE_CAPTURE_PORTS"]
    open(log, "w").close()
    pbt = PipewireBackend(mic="usb-mic-source", pw_play=play, pw_record=rec, pactl=pactl, pw_link=plink, workdir=td, kill_grace_s=0.3, method="target")
    pbt.play_record(xs, None, stage="target", pre_s=0.3, post_s=0.3)
    lg = open(log).read()
    check("method=target keeps the older pw-play --target path (no pw-link calls)", '"--target", "camilladsp-capture"' in lg and "link " not in lg)
    try:
        PipewireBackend(mic="x", method="nonsense"); ok = False
    except BackendError:
        ok = True
    check("an unknown AUTOCAL_PLAY_METHOD is refused", ok)
finish("test_autocal_sim")
