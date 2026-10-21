# data/

Small measurement files that the analysis and fits were built from. Large recordings (WAV/FLAC) are **not**
committed; see `.gitignore`.

| File | What it is |
|------|------------|
| `rew/L-sweep-2026-10-19.txt` | REW 5.31.3 sweep of the **left** speaker at the listening seat, variable smoothing, 96 points per octave, mic calibration applied. Columns: frequency (Hz), SPL (dB), phase (deg). Used as the "room" curve for all bass fits. |
| `rew/LFE-sweep-2026-10-19.txt` | Same for the **LFE** channel. Shows the same bass mountain as every Z906-fed channel (the Z906 subwoofer plus room modes). |
| `rew/L-filters-rew-export-2026-10-19.txt` | The filter set REW's auto-EQ fitted to flatten the left sweep (the first, flat-target attempt; filter 8 removed). Kept for history: it is the `rew` set in `scripts/set-eq.py`. |

Not included on purpose: the microphone calibration file (it belongs to one specific microphone; load your own
in REW) and any recordings of copyrighted film soundtracks.

Sweep conditions (from the file headers): 256k log sweep, 1 sweep at -12 dBFS, no timing reference, so absolute
arrival times from these files are not reliable (see `docs/measurement.md`). Absolute SPL was not level-calibrated:
read shapes, not absolute dB.
