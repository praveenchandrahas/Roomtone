#!/usr/bin/env python3
"""Builds the soundbar kit: the rig's sweep, dialogue and sub tests, plus a pan-ring test, as 5.1 files (no heights) for a
soundbar such as the Samsung HW-Q930F.

  python3 make-bartest.py [--outdir=.]

For each test it writes (NAME = sweeptest, dialoguetest, subtest, pantest):
  bar-NAME-5.1.flac   6-channel PCM, file order FL FR FC LFE SL SR (the order HDMI/ffmpeg call 5.1)
  bar-NAME-5.1.ac3    Dolby Digital 5.1, 640 kbit/s   (for bitstream passthrough: most soundbars take multichannel only this way)
  bar-NAME-5.1.eac3   Dolby Digital Plus 5.1, 768 kbit/s
  bar-NAME-cues.json  the same cue timeline the rig's analysers read, with the height slots removed and "file_channels"
                      giving the rig's hardware index of each file channel (FL 0, FR 1, FC 4, LFE 5, SL 6, SR 7)
The sweeps, dialogue and sub tests are the rig's own generators, remapped, so the analysers (analyze-sweeptest.py,
analyze-dialoguetest.py, analyze-subtest.py) read the recordings unchanged. analyze-bar-pan.py reads the pan ring.
It also decodes each lossy file back and checks that every slot lands on the channel it should (self-test).
Note: AC-3 / E-AC-3 band-limit the LFE channel to about 120 Hz and the rest to about 20 kHz; use the .flac when you can.
"""
import json, os, subprocess, sys, tempfile, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(TOOLS, "dialoguetest"))
from dialoguetest_common import pink_noise                       # noqa: E402

FS = 48000
FILE_CH = [0, 1, 4, 5, 6, 7]                                      # hardware index of each 5.1 file channel: FL FR FC LFE SL SR
NAMES = {0: "FL", 1: "FR", 4: "FC", 5: "LFE", 6: "SL", 7: "SR"}
HEIGHTS = {2, 3}
# the rig's generators (they write into the current directory)
MAKERS = {"sweeptest": ("sweeptest/make-sweeptest.py", [], "sweeptest-hw.wav", "sweeptest-cues.json"),
          "dialoguetest": ("dialoguetest/make-dialoguetest.py", [], "dialoguetest-hw.wav", "dialoguetest-cues.json"),
          "subtest": ("subtest/make-subtest.py", ["--quiet=-20", "--loud=-10"], "subtest-hw.wav", "subtest-cues.json")}
# pan ring: azimuth (deg, 0 = front, + = right) of each speaker, in ring order
SPK = [(0, 4), (30, 1), (110, 7), (250, 6), (330, 0)]            # FC FR SR SL FL (hardware index)
RING = list(range(0, 360, 30))


def read_wav(path):
    w = wave.open(path, "rb"); n, ch = w.getnframes(), w.getnchannels(); raw = w.readframes(n); w.close()
    return np.frombuffer(raw, "<i2").reshape(n, ch).astype(np.float64) / 32768.0


def write_wav(path, x):
    rng = np.random.default_rng(1)
    pcm = np.clip(np.round(x * 32767 + rng.random(x.shape) - rng.random(x.shape)), -32768, 32767).astype("<i2")
    w = wave.open(path, "wb"); w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes()); w.close()


def chirp(ms=30, f0=400.0, f1=8000.0, amp=0.2):
    n = int(FS * ms / 1000); k = np.log(f1 / f0); T = n / FS; t = np.arange(n) / FS
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))
    return amp * w * np.sin(2 * np.pi * f0 * T / k * (np.exp(t / T * k) - 1))


def remap(x8):
    return x8[:, FILE_CH]


def slot_ok(chs):
    return not (set(chs) & HEIGHTS) and (len(chs) == 1 or set(chs) == {0, 1})


def strip_heights(x6, C):
    """Drop the slots that need a height speaker; silence their audio. Works for the sweep / dialogue slot lists."""
    C = json.loads(json.dumps(C))
    if "slots" in C:
        keep, dropped = [], []
        for s in C["slots"]:
            (keep if slot_ok(s["ch"]) and not (len(s["ch"]) > 1 and 5 in s["ch"]) else dropped).append(s)
        for s in dropped:
            i0 = int(s["t"] * FS); x6[i0:i0 + int(C["slot_s"] * FS), :] = 0
        C["slots"] = keep
    return x6, C


def pan_gains(az):
    for (a0, c0), (a1, c1) in zip(SPK, SPK[1:] + [(360, SPK[0][1])]):
        if a0 <= az <= a1:
            u = (az - a0) / (a1 - a0); return {c0: np.cos(u * np.pi / 2), c1: np.sin(u * np.pi / 2)}
    raise ValueError(az)


def make_pan():
    PAD, GAP, DUR, LEVEL = 13.0, 2.8, 2.0, -24.0
    refs = [{"t": PAD + i * GAP, "name": NAMES[c], "ch": c, "az": a} for i, (a, c) in enumerate(SPK)]
    ring = [{"t": PAD + (len(SPK) + i) * GAP, "az": a, "gains": {str(k): round(float(v), 4) for k, v in pan_gains(a).items()}} for i, a in enumerate(RING)]
    end = PAD + (len(SPK) + len(RING)) * GAP + 1.0
    sync = [10.0, 10.5, 11.0, end, end + 0.5, end + 1.0]; total = end + 1.0 + 5.5
    x = np.zeros((int(total * FS), 6)); c = chirp()
    for tt in sync:
        i = int(round(tt * FS)); x[i:i + len(c), 0] += c
    for k, e in enumerate(refs + ring):
        n = pink_noise(DUR, seed=11 + k, level_db=LEVEL)           # a fresh noise for every burst, same level
        i = int(round(e["t"] * FS))
        g = {e["ch"]: 1.0} if "ch" in e else {int(h): v for h, v in e["gains"].items()}
        for hw, v in g.items():
            x[i:i + len(n), FILE_CH.index(hw)] += n * v
    C = {"fs": FS, "sync": sync, "total": total, "dur": DUR, "level": LEVEL, "refs": refs, "ring": ring, "names": {str(k): v for k, v in NAMES.items()}}
    return x, C


def encode(wav, base):
    out = {}
    for ext, codec, br in (("ac3", "ac3", "640k"), ("eac3", "eac3", "768k")):
        p = f"{base}.{ext}"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", codec, "-b:a", br, "-ar", "48000", p], check=True)
        out[ext] = p
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "flac", base + ".flac"], check=True)
    return out


def slots_of(C):
    """(t, dur, [file channel indices]) windows to verify after the lossy round trip."""
    out = []
    for s in C.get("slots", []):
        out.append((s["t"] + 1.0, 4.0, [FILE_CH.index(c) for c in s["ch"]]))
    for s in C.get("stipa", []):
        out.append((s["t"] + 2.0, 8.0, [FILE_CH.index(c) for c in s["ch"]]))
    if "pink" in C:
        out.append((C["pink"]["t"] + 1.0, 8.0, [FILE_CH.index(c) for c in C["pink"]["ch"]]))
    for s in C.get("refs", []):
        out.append((s["t"] + 0.4, 1.2, [FILE_CH.index(s["ch"])]))
    return out


def selftest(path, C, label):
    y = read_wav(path); bad = 0; worst = 99.0
    if not slots_of(C):
        print(f"    self-test {label}: nothing to check (LFE-only sweeps and tones)"); return True
    for t, d, chs in slots_of(C):
        seg = y[int(t * FS):int((t + d) * FS)]
        e = 10 * np.log10(np.mean(seg ** 2, 0) + 1e-20)
        on = min(e[chs]); off = max([e[i] for i in range(6) if i not in chs] + [-200])
        if on - off < 30:
            bad += 1
        worst = min(worst, on - off)
    print(f"    self-test {label}: weakest slot is {worst:.0f} dB above its silent channels, {bad} slot(s) below 30 dB")
    return bad == 0


def main():
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    outdir = os.path.abspath(opt.get("outdir", ".")); os.makedirs(outdir, exist_ok=True)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for name, (maker, args, wav8, cj) in MAKERS.items():
            subprocess.run([sys.executable, os.path.join(TOOLS, maker)] + args, cwd=tmp, check=True, capture_output=True)
            x6 = remap(read_wav(os.path.join(tmp, wav8))); C = json.load(open(os.path.join(tmp, cj)))
            x6, C = strip_heights(x6, C); C["file_channels"] = FILE_CH; C["layout"] = "5.1 (FL FR FC LFE SL SR)"
            base = os.path.join(outdir, f"bar-{name}-5.1"); write_wav(base + ".wav", x6)
            enc = encode(base + ".wav", base); json.dump(C, open(os.path.join(outdir, f"bar-{name}-cues.json"), "w"), indent=1)
            print(f"{name}: {x6.shape[0] / FS:.0f} s, {len(C.get('slots', []))} sweep slots kept" if "slots" in C else f"{name}: {x6.shape[0] / FS:.0f} s")
            for lab, p in (("wav", base + ".wav"), ("ac3", enc["ac3"]), ("eac3", enc["eac3"])):
                sub = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", p, "-c:a", "pcm_s16le", os.path.join(tmp, "dec.wav")], check=True)
                ok &= selftest(os.path.join(tmp, "dec.wav"), C, lab)
        x, C = make_pan(); base = os.path.join(outdir, "bar-pantest-5.1"); write_wav(base + ".wav", x)
        enc = encode(base + ".wav", base); json.dump(C, open(os.path.join(outdir, "bar-pantest-cues.json"), "w"), indent=1)
        print(f"pantest: {x.shape[0] / FS:.0f} s, 5 single-speaker references then {len(RING)} pan positions")
        for lab, p in (("wav", base + ".wav"), ("ac3", enc["ac3"]), ("eac3", enc["eac3"])):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", p, "-c:a", "pcm_s16le", os.path.join(tmp, "dec.wav")], check=True)
            ok &= selftest(os.path.join(tmp, "dec.wav"), C, lab)
    print("ALL SELF-TESTS PASS" if ok else "SELF-TEST FAILED: see above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
