# data/

Small measurement files that the analysis and fits were built from. Large recordings (WAV/FLAC) are **not**
committed; see `.gitignore`.

| File | What it is |
|------|------------|
| `rew/L-sweep-2026-10-19.txt` | REW 5.31.3 sweep of the **left** speaker at the listening seat, variable smoothing, 96 points per octave, mic calibration applied. Columns: frequency (Hz), SPL (dB), phase (deg). Used as the "room" curve for all bass fits. |
| `rew/LFE-sweep-2026-10-19.txt` | Same for the **LFE** channel. Shows the same bass mountain as every Z906-fed channel (the Z906 subwoofer plus room modes). |
| `rew/L-filters-rew-export-2026-10-19.txt` | The filter set REW's auto-EQ fitted to flatten the left sweep (the first, flat-target attempt; filter 8 removed). Kept for history: it is the `rew` set in `scripts/set-eq.py`. |

Not included on purpose: recordings of copyrighted film soundtracks.

| File | What it is |
|------|------------|
| `mic/dayton-imm-6s-calibration.txt` | Calibration file of the owner's Dayton iMM-6S (header `*1000Hz -40.5`, 20 Hz to 20 kHz, dB corrections). **Valid only for that one microphone**; use your own with your own mic. The REW exports above had it applied. The recording analysers in `tools/` (sweep, sub, dialogue, reflection) and the figures drawn from the raw recordings (03, 06 range, 07, 08, 12 to 19) do **not** apply it. Its size: within about +-1.5 dB from 25 Hz to 6 kHz, up to +2.1 dB at 7.3 to 7.5 kHz, about -2.1 dB at 17 kHz. That matters little for comparisons between speakers (the same correction applies to all) but shifts absolute curves by that much. |

Sweep conditions (from the file headers): 256k log sweep, 1 sweep at -12 dBFS, no timing reference, so absolute
arrival times from these files are not reliable (see `docs/measurement.md`). Absolute SPL was not level-calibrated:
read shapes, not absolute dB.
