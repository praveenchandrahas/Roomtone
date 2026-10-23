# Reflection test: are the first-reflection panels working?

Question: do the panels at FL, FC, FR and the two ceiling heights actually remove the early reflections that reach the seat?
The answer needs a **before and after**, because a single measurement cannot say what the room would do without the panels.
It reuses the sweep test (`tools/sweeptest/`): each speaker plays alone, so no new test file is needed.

## 1. Look at what is there now (any existing sweep recording)

    python3 -I analyze-reflections.py recordings/sweep2.wav --plot=etc.png

For each speaker: the energy-time curve (ETC) of its impulse response in the 1-8 kHz band, relative to the direct sound; every
reflection in the first 30 ms with delay, level and extra path length; and the ITU-R BS.1116 check (every reflection in the first
15 ms at least 10 dB below the direct sound, 1-8 kHz; reported as PASS at -15 dB or lower, OK to -10 dB, FAIL above).

## 2. Prove the panels do something (A/B, about 15 minutes)

Mic and mic gain exactly where they are for the sweep tests. EQ ON. Both runs the same day, the room otherwise unchanged.

1. Cover or remove the panels (a bare sheet or a bed sheet pinned over the panel is enough for 1-8 kHz; a hard board is better).
2. `./rec.sh nopanel 155` + `./play-through-camilla.sh sweeptest-hw.flac`  -> `recordings/nopanel.wav`
3. Put the panels back, run again  -> `recordings/panel.wav`
4. `python3 -I analyze-reflections.py recordings/panel.wav --base=recordings/nopanel.wav --plot=etc.png`

The report adds, per speaker, the change in early energy per time slice (1-3, 3-5, 5-8, 8-11, 11-15 ms) and per 1/3-octave band
from 250 Hz up. A working panel lowers the slice where its reflection arrives by several dB above about 500 Hz-1 kHz. **Repeat-run
noise of this comparison was measured at +-3 dB** (sweep run 1 against run 2, same state, speakers FL FR FC: single slices moved up
to 3.3 dB, the summed 1-15 ms energy up to 0.6 dB), so only changes of 4 dB or more in a slice, or 1.5 dB or more in the sum, are real.
Do each speaker's panel separately (one panel covered at a time) if you want to attribute a change to one panel.

## 3. Name the surface behind each reflection (optional)

Copy `room-example.json`, fill in your tape-measure survey (metres, origin in a floor corner, mic at the ear position), and add
`--geometry=room.json`. Each measured peak within 0.4 ms of a predicted first-order image-source delay is labelled with its surface.
The ceiling is not level (2.96 m and 3.22 m measured) and the surfaces are not all parallel, so treat labels as candidates.
A mirror taped to the wall and moved until you see the speaker from the seat finds the reflection point physically.

## What the existing sweep (2026-10-23, panels in place, no baseline) shows

| Speaker | Worst reflection 1-15 ms | Verdict | Note |
|---|---|---|---|
| FC | -14 dB at 3.4 / 4.0 ms | OK | no reflection above -14 dB after 1 ms |
| FR | -14 dB at 12 ms | OK | clean early, a little at 12-17 ms |
| FL | -8 dB at 5.1 ms (extra path 1.75 m) | FAIL narrowly | one distinct reflection, others near -13 dB at 9-14 ms |
| Height L | -1 dB at 3.6 ms (1.2 m), -8 dB at 5.8 ms | FAIL | a reflection almost as strong as the direct sound |
| Height R | -1 dB at 3.8 ms (1.3 m), -5 dB at 10 ms | FAIL | same pattern |

This says where the energy is, not whether the panels failed: without a baseline the same peaks could be the untreated part of
the room. The heights' pair of peaks at 3.6-3.8 ms in both is worth a mirror check on the floor and the back wall (the seat is
against it).

Validation: the analyser was exercised on real sweeps (run 1 against run 2 as a null test) and its geometry labels checked
with made-up coordinates; it has not yet been run on a real A/B.
