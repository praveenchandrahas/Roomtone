# Measurement method

## Equipment and layout

- **Microphone:** Dayton iMM-6S with its calibration file (not in this repo: load your own in REW and in analysis).
  Placed at the listening seat, ear height, opposite the centre speaker.
- **Capture device:** a SPACETOUCH USB audio adapter (ALSA card 1), separate from the playback interface. Two devices
  means two clocks: all timing methods here remove the drift.
- **Level:** `./scripts/rec.sh gain -18dB` set the capture level once. Do not change it during a session.

## Recording with rec.sh

```bash
./scripts/rec.sh list                                  # find the capture device (not a .monitor)
export MIC=alsa_input.usb-SPACETOUCH_USB_Audio_000000000-00.analog-stereo
./scripts/rec.sh gain -18dB                            # once per session
./scripts/rec.sh NAME 45                               # records ./recordings/NAME.wav (48 kHz stereo, 24-bit)
./scripts/rec.sh check recordings/NAME.wav             # peak and clipping check
```

Start the clip or test signal about 3 s after the recording starts so the first seconds are a noise floor.

### Order for an A/B of presets

Record "direct" (no DSP) first, then each preset, then "direct" again at the end: the two direct recordings show whether
anything drifted (mic position, room noise, level). Keep playback level, mic position and capture gain identical.

### Clipping

If `check` says the peak is above about -6 dBFS, treat the recording as rough. Loud scenes clipped at the mic;
lowering the playback level by roughly 10 dB gives a clean recording and the **change** in dB between presets is still valid.

## What each test is for

| Test | Tool | Answers |
|------|------|---------|
| Frequency response at the seat | REW sweeps (`data/rew/`) | Where the room modes and nulls are; fit cut filters |
| Preset comparison | `rec.sh` recordings of real film clips, band-energy comparison against "direct" | Did the preset change what was meant to change, by how much |
| Arrival time and level per speaker | `make-delaytest.py` + `analyze-delaytest.py` | Delays and trims ([alignment.md](alignment.md)) |
| Imaging by ear | `make-bedtest.py` | Channel order, phantom centre, pans, click alignment, polarity pairs, dialogue |

### The bed test

`python3 scripts/make-bedtest.py bedtest-5.1.flac` writes a 163 s 5.1(side) FLAC and prints the start time of each part:
T1 channel order, T2 phantom versus real centre, T3 front pan, T4 around you, T5 click alignment, T6 polarity pairs,
T7 dialogue-like noise. Play it with `mpv-atmos` so it goes through the same chain as films.

### Proof of the alignment (still open)

Record the first 14 seconds twice, once with the alignment off and once on:

```bash
./scripts/set-eq.py --set=drone2                 # alignment off
mpv-atmos --pause --end=14 bedtest-5.1.flac      # second terminal; then ./scripts/rec.sh bed-off 20, press space about 3 s in
./scripts/set-eq.py --set=drone2 --align=bed     # alignment on
mpv-atmos --pause --end=14 bedtest-5.1.flac      # then ./scripts/rec.sh bed-on 20
```

The per-speaker level difference between the two recordings should match the trims: SL -4.4, FR -1.9, FC -0.8, SR -0.7 dB.
(With the router installed no manual routing step is needed.)

## Sweep tests with CamillaDSP in the path (2026-10-23)

REW sends its sweep straight to the sound card, so it cannot measure through CamillaDSP. `tools/sweeptest/` and
`tools/subtest/` play log sweeps on the eight channels (and pairs) through `play-through-camilla.sh`, record on the mic, and
deconvolve with sync chirps as the timing reference. `analyze-sweeptest.py` reports per-speaker arrival and tone, and the
heights-versus-bed fit; `--heights-on` checks a recording made with the heights processing active. `analyze-subtest.py`
reports the sub's range, compression and distortion, and a rattle hunt with stepped tones. Both tolerate a late-started or
early-stopped recording (they place the file by the sync chirps). Each folder has a README with the commands.

## Dialogue and reflection tests (2026-10-23)

`tools/dialoguetest/` (centre-speaker response, real versus phantom centre, C50, RT60, STI, background noise) and
`tools/reflecttest/` (early-reflection energy-time curves from the existing sweep test, with a panels-on versus panels-off
comparison and an image-source label per surface) are built and checked against simulated or existing recordings; see their
READMEs. The reflection comparison's repeat-run noise is about +-3 dB per time slice.

## Things that bit us

- REW sweeps were recorded with no timing reference on a separate capture device: do not read absolute arrival times from them.
- Mic and output share no clock: always use a drift-removing fit.
- A mic recording of a very loud scene is only a rough comparison (ceiling clipping).
- The source of a hot trailer can itself exceed full scale (a few samples on FR/SR in one clip): that is the file, not the chain.
- Repositioning the mic between runs changes single 1/6-octave bins by 3-7 dB (and the bed mean by 1-2 dB): compare band averages and reference channels, not single bins.
- The `.mdat` files of REW 5 are Java serialisations; `javaobj-py3` reads them. A 45-58 dB "response" under 90 Hz in the height sweeps was noise, not output.
