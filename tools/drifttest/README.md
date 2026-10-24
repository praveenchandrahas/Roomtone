# drifttest: does the second subwoofer stay in phase with the first?

`drift.py` measures how the delay between two playback paths changes over time. Use it on any pair of outputs behind different clocks
(Vantec USB vs HDMI to the soundbar, or the same device twice as a control).

1. `python3 drift.py make drift.wav` (default: one pair every 20 s for an hour, chirp 30-120 Hz, B 1.5 s after A).
2. Route the left channel into path A (the Z906 sub via the LFE feed) and the right channel into path B (the bar's feed), through the real chain.
3. Record the whole run with the Dayton mic at the seat (start recording before playback). The mic may be on a different clock: it cancels.
4. `python3 drift.py analyze recording.wav drift.json --csv delays.csv`

It prints the relative drift in ppm and what that adds up to over 3 hours, the jitter, any steps (a re-sync, a stream restart),
and LOCKED / NOT LOCKED against a 1 ms tolerance (about 25 degrees at 70 Hz).

Checked only on simulated recordings (tests/test_drift.py: known drift, a fast mic clock, a 12 ms step, a noisy ringing room). Not yet run on the rig.
The laptop's own microphone is not suitable: MEMS microphones roll off well above the 30-120 Hz band used here.
