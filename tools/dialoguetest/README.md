# Dialogue test: how well does speech come through?

Measures the centre speaker (FC) and the room for speech, with the mic at the seat. One 150 s playback, one recording.
Mic gain as for the sweep runs (do not touch it). EQ ON (`eq status`). **Leave the room as it is when you watch**: projector,
fan and AC on, nobody talking: 20 s of the file is silence on purpose (it measures the background noise).

    python3 make-dialoguetest.py            # writes dialoguetest-hw.flac and dialoguetest-cues.json
    # terminal 1
    ./rec.sh dlg1 165
    # terminal 2, about 8 s later, at your normal listening volume
    ./play-through-camilla.sh dialoguetest-hw.flac
    ./rec.sh check recordings/dlg1.wav
    python3 -I analyze-dialoguetest.py recordings/dlg1.wav

Optional: during the 12 s pink-noise segment (starts at about 105 s into the playback) read the dB(A) level at the seat with a
phone SPL app, then add `--cal-dba=<reading>` to get the dialogue level and the noise floor in dB(A).

## What it measures

| Section | What | Rule of thumb used |
|---|---|---|
| 1 | FC response 200 Hz-5 kHz: flatness, boom (100-200 Hz) and presence (2-4 kHz) relative to 500-1000 Hz; FC against the mean of FL and FR | flatness 2 dB rms good, boom and presence within 3 dB |
| 2 | Real centre against phantom centre (FL+FR together): tone difference and comb-filter ripple | the phantom should not be more combed than FC |
| 3 | From FC's impulse response: C50, RT60, and STI (noise-free, with the measured noise, and direct from a speech-modulated noise) | C50 above +2 dB good; STI 0.60 good, 0.75 excellent (IEC 60268-16 bands) |
| 4 | Background noise and dialogue-to-noise per octave; dBA with the calibration reading | 15+ dB in every band: noise no longer limits intelligibility |

The test signal plays at -27 dBFS RMS, the level of film dialogue, so the noise margin is the one you get when watching at your
usual volume. STI is "STIPA-style": the IEC modulation method with an approximate male speech spectrum and no auditory-masking
correction, so use it to compare setups and judge good/poor, not as a certified figure. Everything is one seat: differences under
about 2 dB per band or 0.03 STI are within repeatability.

## Self-test

`python3 make-dialoguetest.py && python3 simulate-dialogue.py` builds a fake recording of a room with a known impulse response
(RT60 0.35 s) and noise, then `python3 -I analyze-dialoguetest.py sim-dialogue.wav` must find it: the 2026-10-23 run gave
true STI 0.76, impulse-response STI 0.76 (noise-free and with noise), direct STI 0.74, RT60 0.33-0.36 s at 500 Hz-4 kHz.
Not yet run on the real room.
