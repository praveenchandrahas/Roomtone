# Roomtone

**Roomtone is open tooling for measuring a Linux home theatre and calibrating it in a closed loop: it plays sweeps through your live audio chain, records them with a measurement microphone, works out what is wrong with each speaker, proposes corrections, and (only when you say so) applies them to CamillaDSP with a timed trial and a rollback.** The closest commercial analogue is the *workflow* of an Audyssey- or Dirac-style auto-calibrator, on a PC, in the open. It is not a clone of their filters.

*Roomtone* (a film term for a location's own ambient sound) is this repository's name since 2026-10-26; it was `theater-measurement`, and GitHub redirects the old address.

> Part of the [Software Defined Home Theatre](https://github.com/praveenchandrahas/Software-Defined-Home-Theatre) project. The main repository holds the documentation, build log, results and the installer checks; this is one of its parts. Sibling repositories: [linux-theater-dsp](https://github.com/praveenchandrahas/linux-theater-dsp) (router and CamillaDSP configs), [mpv-object-audio](https://github.com/praveenchandrahas/mpv-object-audio) (player and Atmos wrapper), [seekthumbs](https://github.com/praveenchandrahas/seekthumbs) (seek-bar thumbnails), [mpv-shelf](https://github.com/praveenchandrahas/mpv-shelf) (film browser for mpv).

## Status

This is a pre-release, experimental, personal hobby project: written with an AI assistant, checked against simulated rooms and REW exports, and used and checked by the author in his own room (one room, one author). No external or second-person validation yet, and the packages are unsigned. See the [README's "What Roomtone is not (yet)"](https://github.com/praveenchandrahas/Roomtone#what-roomtone-is-not-yet) section for the full honesty notes before relying on it.

## Where to go next

- **[Install](install.md)** — getting the package (or the `autocal`/`autocal-web` commands) onto your machine.
- **[Usage](usage.md)** — running a measurement, using the status page and the mpv overlay, and the other diagnostic kits.
- **[Speaker layouts](layouts.md)** — describing your own speaker layout instead of the author's 5.1.2 rig.
- **[Measurement method](measurement.md)** — the equipment, recording procedure and test signals behind the numbers in the README.
