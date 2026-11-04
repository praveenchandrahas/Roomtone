# Install

Releases are marked **pre-release** on the [releases page](https://github.com/praveenchandrahas/Roomtone/releases).

## Package install

Download the `.deb` and `SHA256SUMS` from one release, check, then install:

```sh
sha256sum -c --ignore-missing SHA256SUMS
sudo apt install ./Roomtone_0.1.0_all.deb
roomtone-setup --dry-run   # then: roomtone-setup install
```

Depends on [linux-theater-dsp](https://github.com/praveenchandrahas/linux-theater-dsp) — install that one first.

The package puts files under `/usr` only. Its install and remove scripts do not enable or start services, do not write into any home folder, and do not touch CamillaDSP or PipeWire configuration; the `roomtone-setup` command above does the per-user part, and `--dry-run` shows what it would do first. Details, the other repositories' packages, and the one-command installer are in the main repository's [packaging notes](https://github.com/praveenchandrahas/Software-Defined-Home-Theatre/blob/main/docs/packaging.md).

## Just the calibration tool

To use only the calibration tool and web page without the system package:

```sh
pipx install git+https://github.com/praveenchandrahas/Roomtone
```

This gives the `autocal` and `autocal-web` commands (the project is not on PyPI).

## What was checked before this release

Written with an AI assistant; checked against simulated rooms and REW exports; used and checked by the author in his own room (one room, one author). No external or second-person validation yet. The packages are unsigned. CI installs and removes them in a clean Ubuntu 24.04 container; the packaging itself has not been run on the author's own rig.

## Adapting it to your system

The calibration file in `data/mic/` belongs to **one** microphone (the author's Dayton iMM-6S); replace it with your own, or run with `--mic-cal=none`. The REW exports in `data/rew/` are from one room, and paths and channel names in the kits assume the author's 5.1.2 layout and interface unless you provide a layout file — see [Speaker layouts](layouts.md).

**Safety.** The sweeps and tone tests play loud, sustained low frequencies: start low, keep the sub away from your ears; high-level tones can damage drivers.

Next: set up your [speaker layout](layouts.md) (five minutes), then see [Usage](usage.md) for running a measurement.
