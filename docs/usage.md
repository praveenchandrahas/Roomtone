# Usage

## What it does

| Step | What Roomtone does | Where |
|---|---|---|
| Measure | Plays level-ramped sweeps through the **current** CamillaDSP profile, one speaker at a time, at one or five microphone positions. Finds each burst by matched filter, so a noisy room does not hide it. Records the room's noise floor before and after | `autocal/` |
| Check | Preflight, noise floor, level ramp and channel identification (catches dead channels and a wrong microphone input) before any real sweep. Hard output cap of -20 dBFS; aborts on microphone clipping or sub distortion (second harmonic above -20 dBc) | `autocal/safety.py` |
| Analyse | Per-speaker arrival time, level, polarity, bass response and the heights' tone against the bed, with the microphone's calibration file applied | `autocal/`, `tools/` |
| Propose | New delays and trims, polarity flips, a consensus parametric cut below 100 Hz towards a target (the author's deliberate bass hump, `drone2`), and a tone and level match for the height speakers. Boosts only with `--allow-boost`. Changes under 0.15 ms or 1 dB are ignored | `autocal/` |
| Apply, safely | Backs up the live config, installs the change, starts a **timed trial** that reverts by itself unless you press **Keep it**; **Roll back** restores the backup | `autocal apply`, `gui/` |
| Show | A local control page (127.0.0.1 only): a plan view of the room with live per-speaker levels, what is playing, CamillaDSP load, and the autocal page with live progress, "move the microphone" prompts and graphs. The same panel is in mpv (press **C**) | `gui/`, `overlay/` |
| Diagnose | Separate kits for the sub (how low it goes, where it rattles), dialogue (a STIPA-style estimate), early reflections, drift between two playback clocks, and a load and dropout logger for films | `tools/`, `scripts/resource-log` |

## Running autocal

`autocal` measures every speaker through the current CamillaDSP profile and proposes a residual correction. It never applies anything by itself.

```
scripts/autocal check|mic --spectrum|run ...          the same commands with the mic made ready first (MIC set, unmuted, capture level 60 %); use this on the real rig
python3 -m autocal check [--simulate]                 preflight, noise floor, level ramp, channel ID only
python3 -m autocal run --simulate [--mode single|five] [--runs-dir DIR]
python3 -m autocal run [--mode single|five]           real rig (PipeWire), needs the mic
python3 -m autocal report|verify RUN
python3 -m autocal apply RUN [--trial-seconds 60]     backup, install, 60 s trial that reverts unless:
python3 -m autocal keep                               ...you confirm
python3 -m autocal rollback                           restore the latest backup
python3 -m autocal.web                                localhost GUI (separate part)
```

A `--mode five` run uses five microphone positions (centre plus four offsets); the program prompts "move the mic" between them, and the delays, trims and bass EQ come from the position means. `--seat` sets up a multi-seat run instead (name the seats and how much each counts) — see `autocal/README.md` for the full options.

A **FAIL** verdict can still be applied with `autocal apply --force` (or the page's "Apply anyway" confirmation after a warning) — autocal refuses without it. `python3 -m autocal mic --spectrum` records 15 s of nothing playing and prints the noise floor per octave band, to tell a cycling compressor or fan from electrical hum before a calibration run.

## Status and control page (`gui/`)

`gui/install-ctld.sh` installs `theater-ctld` as a user service on `127.0.0.1:8765` (needs the router's `eq status --json` from linux-theater-dsp first; `remove` undoes it).

- **Control page** (`/`): now-playing, profile and engine, CamillaDSP live numbers (needs its websocket, `-p 1234 -a 127.0.0.1`), buttons for EQ, profile mode, flavours and engine, and a live sound-field view of each speaker's level in a plan view of the room. It is the signal after EQ, not room sound pressure.
- **Layout page** (`/layout`): choose a preset or edit speakers, map each to an output port, identify a speaker with a quiet tone, preview the plan view, save with a backup.
- **Autocal page** (`/autocal`): start a measurement, watch it live (step, progress, position, mic meters, noise floor, console output), answer "move the microphone" prompts, read the verdict and per-channel table of any past run, and apply a finished run's proposal. It runs the same `autocal` commands you would type, as a subprocess from a fixed table — no shell, no free-form options.

All pages are localhost-only, with a token on every action and a fixed action table (no shell, no free-form options).

## mpv overlay

`overlay/install-theater-status.sh --bindings` installs the mpv script and binds the **C** key. It shows one panel: status first, then a row per setting showing its current value; Enter steps a row, and risky changes ask first.

## Setting up your own speaker layout

See [Speaker layouts](layouts.md) for the full reference. In short:

1. `python3 autocal/layout.py --detect` lists your interface's output ports (plays nothing) and proposes a preset by port count.
2. `python3 autocal/layout.py --detect --device <your sink name> --write` writes that proposal, or use the Layout page at `http://127.0.0.1:8765/layout`.
3. `python3 autocal/layout.py --validate` checks the file in plain words.
4. Rebuild the DSP config with `set-eq.py` (or `eq on`), then restart the router and `theater-ctld`.

Every preset except the author's own 5.1.2 starts with zero delays, zero trims and no tone filters; run autocal in your room to get proposals.

## Diagnostic kits

| Kit | Question it answers |
|---|---|
| `tools/sweeptest/` | speaker-by-speaker sweeps with CamillaDSP in the path: level, arrival time, bass management of the heights |
| `tools/subtest/` | how low the sub goes and where it rattles (tones, sweeps, a rattle detector) |
| `tools/dialoguetest/` | how well speech comes through (a STIPA-style estimate, not a certified STI) |
| `tools/reflecttest/` | are the first-reflection panels working (early-reflection analysis, before and after) |
| `tools/bartest/` | the same tests as a 5.1 set for a soundbar, plus a pan ring, to compare against the rig |
| `tools/drifttest/` | does a second subwoofer (another clock) stay in phase with the first? Measures the delay between two playback paths over time (ppm drift, jitter, steps) |
| `tools/miccal.py` | removes the measurement microphone's response from every recording the analysers read |

Each kit's own `README.md` under `tools/<kit>/` has the exact commands and what the output means. The full measurement procedure (equipment, recording levels, what each test is for) is in [Measurement method](measurement.md).

## Load and dropouts during a film

Run `scripts/resource-log` in a terminal, start the film, press Ctrl+C at the end (add `--gpu-sudo` on an Intel GPU, which needs root for its GPU counters and asks for your password once):

```bash
scripts/resource-log --label atmos --gpu-sudo
```

It writes `~/resource-log/<time>-atmos/summary.md` (read this first), plus `samples.csv`, `environment.txt`, `pw-top.log` and `camilladsp-journal.txt`. It only reads `/proc`, `/sys`, CamillaDSP's read-only websocket, mpv's IPC socket, `journalctl --user`, and `pw-top`, `nvidia-smi` or `intel_gpu_top` when present. A source that is not available is reported as "not measured", never as zero.

## Microphone calibration

All the analysers read recordings through one function that applies the calibration file in `data/mic/` (values subtracted, as REW does; zero-phase, so timing is untouched). The analyser prints `mic calibration: applied ...` or `NOT applied`. Use `--mic-cal=FILE` or `MIC_CAL=FILE` for another file and `--mic-cal=none` to switch it off.
