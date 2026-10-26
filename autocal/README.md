# autocal: automated room calibration for the SDHT rig

Measures every speaker through the CURRENT CamillaDSP profile, then proposes a residual correction (new = now + residual): per-channel delay, trim, polarity, a consensus-cut bass EQ towards the owner's 20 dB hump (drone2), and a heights tone/level match. It never applies anything by itself. Only numpy, scipy and the standard library.

    python3 -m autocal check [--simulate]                 preflight, noise floor, level ramp, channel ID only
    python3 -m autocal run --simulate [--mode single|five] [--runs-dir DIR]
    python3 -m autocal run [--mode single|five]           real rig (PipeWire), needs the mic
    python3 -m autocal report|verify RUN
    python3 -m autocal apply RUN [--trial-seconds 60]     backup, install, 60 s trial that reverts unless:
    python3 -m autocal keep                               ...you confirm
    python3 -m autocal rollback                           restore the latest backup
    python3 -m autocal.web                                localhost GUI (separate part)

Tests: `bash tests/run.sh` (analysis test takes about 2 minutes). Test env overrides: AUTOCAL_CONFIG, AUTOCAL_ROOM, AUTOCAL_BACKUP_DIR, AUTOCAL_RESTART_CMD, SET_EQ, AUTOCAL_RUNS_DIR.

## What is verified, and how

**Only in simulation** (a modelled room with a Z906-like satellite/sub path, noise, clock drift, faults): ir/deconvolution golden tests against the existing analysers, stimulus levels and the -20 dBFS cap, ground-truth recovery (delays within 0.15 ms, trims within 1 dB), flipped-speaker detection, null not boosted, dead channel, prediction vs re-measurement, abort paths (clip, abort file within 250 ms, fake pw-play), apply/keep/rollback and trial revert on temp files, schema validity.

**Only the owner can validate at the rig:**
- real SNR, mic clipping margins, and every threshold (SNR gate 25 dB at 40 Hz, harmonic limit -20 dBc, polarity margin, null masking, 3 dB target rms): these are reasoned guesses, not calibrated
- `pw-record` stdout format (PipewireBackend is UNTESTED ON HARDWARE). Playback now uses the method of `scripts/play-through-camilla.sh` (`pw-play` with `node.autoconnect=false`, then `pw-link` each port to `camilladsp-capture:input_N`), which is what the earlier measurements used; the older `--target camilladsp-capture` path remains as `AUTOCAL_PLAY_METHOD=target`. The autocal code around it is still unrun on the rig
- trial/rollback across a live CamillaDSP restart and relink (the real-rig path in install.py is untested)
- mic calibration use, and above all whether the result sounds right
- whether the 3 dB rms-to-target is reachable: in the sim, drone2's taste cuts and nulls leave about 3.8 dB after correction

## Behaviour to know

- Arrival times are relative to FL (no absolute timing reference). LFE timing uses 40-120 Hz and is rough; LFE and dead channels follow FL's change. The sub knob is untouched.
- Trims are <= 0 against the quietest raw bed speaker. Changes under 1 dB / 0.15 ms are ignored. Boosts only with --allow-boost.
- Level: ramp from -50 dBFS up to a hard cap of -20 dBFS, stopping about 30 dB over the noise floor; sweeps run at stop + 12 dB (never above the cap, and reduced if the mic peak would exceed -10 dBFS). Heights play from 100 Hz.
- Safety: mic peak above -6 dBFS, clipped samples, or 2nd harmonic above -20 dBc abort; the abort file is polled every 50 ms.
- Deviation from the plan: not one pw-play per sweep. One continuous file per position is played, so the sync chirps can place every sweep and fit clock drift.
- Stubs: automatic repeat of a sweep when SNR < 30 dB (only a warning/abort). Channel ID catches dead or wrong-mic channels; swapped speakers are only visible by comparing runs.
- Persistence: `apply` writes the live room.json (`~/.config/camilladsp/room.json`); set-eq.py reads it implicitly (`--no-room` ignores it), so the router's automatic profile switches keep the applied values. This needs the set-eq.py of linux-theater-dsp from 2026-10-26 or later.
- A GUI-started simulated run waits for control/continue unless --auto-continue is given.

## Five-position mode

Five mic positions (centre plus four offsets); the program prompts "move the mic" between them, detects a mic that did not move (arrival change < 0.15 ms) and asks again. Delays/trims come from the position means. Bass EQ is a consensus cut: below 100 Hz a position-weighted power average (centre weight 2), above 100 Hz the 25th percentile, cross-faded 85-115 Hz; nulls at any position are masked; cuts only (<= 8 filters, <= -15 dB, Q <= 5/6). A leave-one-position-out check steps down to fewer/softer filters if the held-out position gets worse. It aims at a smoother seat area, not a perfect single point, so predicted improvement at the centre is smaller than in single mode.
