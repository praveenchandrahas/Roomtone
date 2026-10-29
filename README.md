# Roomtone

![Roomtone: measure, analyse, calibrate. Open tools for home-theatre acoustics](docs/img/roomtone-banner.png)

> **Part of the [Software Defined Home Theatre](https://github.com/praveenchandrahas/Software-Defined-Home-Theatre) project.** The main repository holds the documentation, build log, results and the installer checks; this is one of its parts. Sibling repositories: [linux-theater-dsp](https://github.com/praveenchandrahas/linux-theater-dsp) (router and CamillaDSP configs) · [mpv-object-audio](https://github.com/praveenchandrahas/mpv-object-audio) (player and Atmos wrapper) · [seekthumbs](https://github.com/praveenchandrahas/seekthumbs) (seek-bar thumbnails) · [mpv-shelf](https://github.com/praveenchandrahas/mpv-shelf) (film browser for mpv).
> *Roomtone* (a film term for a location's own ambient sound) is this repository's name since 2026-10-26; it was `theater-measurement`, and GitHub redirects the old address.

Test signals, recording procedures and analysers used to measure and tune a DIY Linux home theatre with a measurement microphone (a Dayton iMM-6S in the original).
Everything here was written for one room and one seat; the kits are general enough to reuse, but read each kit's README for its assumptions.

| Kit | Question it answers |
|---|---|
| `tools/sweeptest/` | speaker-by-speaker sweeps with CamillaDSP in the path: level, arrival time, bass management of the heights |
| `tools/subtest/` | how low the sub goes and where it rattles (tones, sweeps, a rattle detector) |
| `tools/dialoguetest/` | how well speech comes through (a STIPA-style estimate, not a certified STI) |
| `tools/reflecttest/` | are the first-reflection panels working (early-reflection analysis, before and after) |
| `tools/bartest/` | the same tests as a 5.1 set for a soundbar, plus a pan ring, to compare against the rig |
| `tools/analyze-delaytest.py`, `scripts/make-delaytest.py`, `scripts/make-bedtest.py` | the delay-alignment test signals and analyser |
| `scripts/rec.sh`, `scripts/tone-test.sh` | recording (mic gain) and a tone helper |
| `tools/drifttest/` | does a second subwoofer (another clock) stay in phase with the first? Measures the delay between two playback paths over time (ppm drift, jitter, steps) |
| `tools/miccal.py` | removes the measurement microphone's response from every recording the analysers read (see below) |
| `autocal/` | **automated room calibration** ("our own Audyssey"): measures every speaker through the current CamillaDSP profile and proposes delay, trim, polarity, bass-EQ and heights corrections, with a local web page and graphs. Never applies anything by itself. Developed against a simulated room; on the real rig so far it has produced analysed single-position recordings and stopped, by design, at its noise gates; nothing has been applied. See `autocal/README.md` |
| `gui/` | the local **status and control page** (`theater-ctld`, http://127.0.0.1:8765, this computer only): what is playing, profile, engine, CamillaDSP load, and switches for EQ, mode, flavours and engine. Moved here from linux-theater-dsp; it drives the router's `eq` command and never applies room corrections. See `gui/` and the section below |
| `overlay/` | the same status and controls as an **mpv overlay**: press **C** in the player. Moved here from mpv-object-audio |
| `data/` | small REW exports and the microphone calibration file (`data/README.md`); large recordings are not committed |

The figure-drawing scripts that turn these results into the project's graphs are in the [sdht](https://github.com/praveenchandrahas/Software-Defined-Home-Theatre) repository (`tools/make-*.py`), because they also use the router's filter
definitions. The measurement protocol is `docs/measurement.md`; the results, their uncertainty and the build log are in sdht.

## Microphone calibration

All the analysers read recordings through one function that applies the calibration file in `data/mic/` (values subtracted, as REW does; zero-phase, so timing is untouched). The analyser prints
`mic calibration: applied ...` or `NOT applied`. Use `--mic-cal=FILE` or `MIC_CAL=FILE` for another file and `--mic-cal=none` to switch it off. Results published before 2026-10-26 were computed **without** it
(it changes absolute curves by up to about 2 dB and cancels in speaker-to-speaker comparisons).

## Architecture

![Roomtone measurement and calibration architecture: measurement data path, automated calibration prototype, results and iteration, safety](docs/img/roomtone-architecture.png)

*Conceptual diagram: it shows how the parts relate, not every file. The room, seat, channel map, interface and microphone calibration are the author's; check each assumption for your system.*
The three blocks map to this repository like this: the **measurement data path** is `tools/` and `scripts/` (signal generators, `rec.sh`, `tools/miccal.py` and the analysers); the **automated calibration prototype** is `autocal/`, which measures through the current CamillaDSP profile and only *proposes* corrections;
**results and iteration** are the graphs and REW exports (graph scripts live in sdht). The status page and the overlay (`gui/`, `overlay/`) sit beside this path: they show and switch the router, they do not measure and they do not apply corrections.

## Status and control (gui/ and overlay/)

- **Page:** `gui/install-ctld.sh` installs `theater-ctld` as a user service on `127.0.0.1:8765` (needs the router's `eq status --json` from linux-theater-dsp first; `remove` undoes it). Shows now-playing, profile and engine, CamillaDSP live numbers (needs its websocket: `-p 1234 -a 127.0.0.1`), and offers EQ, profile mode, flavours and engine as buttons. A live **sound field** shows the level of each of the 8 outputs (FL FR FC LFE SL SR and the two ceiling speakers) in a plan view of the room plus meters, about 20 times a second (`/v1/levels`, read-only `GetPlaybackSignalPeak/Rms`). It is the signal after EQ, not room sound pressure and not object positions; the "loudest" and glow centre are level-weighted, not a rendered object position. Localhost only, a token on every action, fixed action table; details in `gui/theater-ctld`'s header. Tests: `tests/test_ctld.py`.

<p align="center"><img src="docs/img/control-dark.png" alt="The Roomtone control page: a plan view of the 5.1.2 room with live per-speaker levels, eight meters, now playing, and the controls" width="880"></p>

*The control page (dark theme; it follows your system theme, so there is a light one too: [`docs/img/control-light.png`](docs/img/control-light.png)). These screenshots were taken against a simulated CamillaDSP that generates moving levels, so the numbers and the "now playing" text are made up for the picture, not a recording of a film on the rig.*

- **Autocal page:** `http://127.0.0.1:8765/autocal` (link in the control page's header). Start a measurement, watch it (step, progress, position, mic meters, room noise floor, the run's console output), answer "move the microphone" with **Continue**, **Stop the run**, read the verdict, warnings and per-channel table of any past run, and **apply** a finished run's proposal. It reads `runs/` from this checkout (`gui/install-ctld.sh` sets `THEATER_AUTOCAL_RUNS` / `THEATER_AUTOCAL_REPO` in the service; re-run it to update).
  - **It runs the same `autocal` commands you would type**, as a subprocess from a fixed table, no shell, no free-form options: `check`, `run --mode single|five`, and two simulated practice runs (`--simulate`, no sound). The only variable parts are a run id that matched `YYYYMMDD-HHMMSS` and the runs folder this service computed.
  - **Guardrails.** One run at a time (also not while a run was started in a terminal, and not while a trial of an applied profile is pending). Starting anything that plays sound asks first and says what will be played. The -20 dBFS level cap and the live safety monitor are inside autocal and the page has no way to change them. **Stop** writes `control/abort` (what the terminal's Ctrl-C ends up doing) and, for a run the page started, sends SIGINT after 3 s and SIGTERM after 8 s if the run is still alive.
  - **Apply** first shows what will change (per-channel delay and trim, extra bass filters, height tone: the run's starting room against `room.proposed.json`), the verdict and warnings, and says it starts a timed trial (a backup is made first); you then press **Keep it** or **Roll back now**, or the trial reverts by itself. A **FAIL** verdict needs a second, explicit "apply anyway (force)" confirmation, the same as `autocal apply --force`; autocal itself refuses without it. **Simulated runs are never applied** to the real system (autocal refuses too).
  - The service unit has `KillMode=process` so that restarting the page does not kill a running measurement or the watchdog that reverts a trial.
  - Same security model as the rest of the page: 127.0.0.1 only, Host/Origin checks, the token on every POST. Tests: `tests/test_ctld.py` (a fake `autocal` records the exact argv).

<p align="center"><img src="docs/img/autocal-page-dark.png" alt="The autocal page during a simulated run waiting for the microphone to be moved: run status, the Continue prompt, console output, and the start buttons disabled while it runs" width="880"></p>
<p align="center"><img src="docs/img/autocal-apply-dialog.png" alt="The apply confirmation: verdict, what changes, warnings, and the timed trial" width="880"></p>

*Screenshots: the first is a real `autocal run --simulate` driven from the page (no sound played); the second uses a generated result from `autocal/web/mock.py`, so its numbers are made up.*
- **Overlay:** `overlay/install-theater-status.sh --bindings` installs the mpv script and binds **C**. One panel: status first, then a row per setting showing its current value; Enter steps a row, risky changes ask first. Tests: `tests/test_theater_status.py` (mocked mpv, needs `pip install lupa`).
- **Mic noise check:** `python3 -m autocal mic --spectrum` records 15 s (nothing is played) and prints the noise floor per octave band with its spread over time, and the strongest narrow tone, to tell a cycling compressor or fan from electrical hum before a calibration run.

## Load and dropouts during a film (`scripts/resource-log`)

Run it in a terminal, start the film, press Ctrl+C at the end (add `--gpu-sudo` on an Intel GPU, which needs root for its GPU counters and asks for your password once):

```bash
scripts/resource-log --label atmos --gpu-sudo
```

It writes `~/resource-log/<time>-atmos/summary.md` (read this first), `samples.csv` (one row per second), `environment.txt`, `pw-top.log` and `camilladsp-journal.txt`. It only reads: `/proc`, `/sys`, CamillaDSP's read-only websocket (port 1234, as the status page uses), mpv's IPC socket, `journalctl --user`, and `pw-top`, `nvidia-smi` or `intel_gpu_top` when present.

The summary starts with the problems: CamillaDSP "buffer empty" log lines while mpv was running (and the same count for the time it was not, because CamillaDSP logs them about every 21 ms when idle), clipped samples, processing load and buffer level; PipeWire xruns; mpv dropped, delayed and mistimed frames and the audio/video sync offset; CPU temperature, clock and thermal-throttle events; CPU, I/O and memory stalls (PSI); the network rate. Then the CPU and RAM of each component (mpv with the Omniphony engine and harletty-bridge inside it, CamillaDSP, PipeWire, WirePlumber, the router, the upmix engine, this page, seekthumbs) and the whole-computer CPU, RAM and GPU. A source that is not available is reported as "not measured", never as zero. Run it once with the film stopped for the idle baseline, and for a fair load number stop other work first (a seekthumbs build reads the same disk, network and CPU).

**First runs on the rig (2026-10-28):** three runs of about a minute each on one small local file (Atmos, idle, DTS:X). It worked end to end. Known rough edges seen: GPU data was empty in one `--gpu-sudo` run and present in another (run `sudo -v` right before; plain `intel_gpu_top` without root aborts on that machine); CamillaDSP buffer level and mpv A/V sync read a constant 0 in every run, which has not been checked against a deliberate load; and the warning count includes about one "WebSocket protocol error" a second from the status page's own connection, which is harmless.

What it does not do: measure end-to-end audio latency (`environment.txt` lists the configured PipeWire and CamillaDSP settings, which are settings, not a measurement), or show per-process GPU use. Tested here against stand-in processes, a fake CamillaDSP websocket and mpv socket, and fake `journalctl`, `pw-top` and `intel_gpu_top` (`tests/test_resource_log.py`), and then run on the rig on 2026-10-28 (see above); the real `intel_gpu_top` and CamillaDSP log wording worked there, with the rough edges listed.

## Adapting it to your system

The calibration file in `data/mic/` belongs to **one** microphone (the author's Dayton iMM-6S); replace it with your own (or run with `--mic-cal=none`). The REW exports in `data/rew/` are from one room, and paths and
channel names in the kits assume the author's 5.1.2 layout and interface. Change them for your setup.

**Safety.** The sweeps and tone tests play loud, sustained low frequencies: start low, keep the sub away from your ears; high-level tones can damage drivers.

Note: the analysers were written with an AI assistant and checked against simulated rooms and REW exports, not independently validated.

## Licence

[Apache License 2.0](LICENSE) (see also [NOTICE](NOTICE)). Dolby, Dolby Atmos, DTS and DTS:X are trademarks of their owners; this project is not affiliated with or endorsed by them. It contains no media files and no decoder code.

Part of the [software-defined home theatre](https://github.com/praveenchandrahas/Software-Defined-Home-Theatre) project.
