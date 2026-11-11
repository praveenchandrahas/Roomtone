# Changelog

## Unreleased

- **Control page: the sound field is now a 3D view** (requested by the owner, 2026-11-10). A schematic room drawn with plain canvas perspective projection, with no library (the page's content-security policy allows only this server's scripts). Bed speakers sit at ear height, the ceiling speakers on the ceiling, the sub on the floor; floor glow and beams from the listener's head scale with each output's level, with peak-hold rings and the centre-of-energy marker placed in 3D. Drag to turn the room; the 3D, Top and Side buttons jump to a view; double-click resets. Positions come from the layout's plan coordinates with fixed heights by speaker kind, so they are schematic, not measured. Same levels as before (`/v1/levels`, after EQ). Checked in headless Chromium with simulated levels and `tests/run.sh`; not yet watched against a live CamillaDSP on the rig.
- **Control page: a theme button** (Auto, Light, Dark; click to cycle). Auto follows the computer as before; the choice is remembered in that browser only (localStorage), and the 3D view's colours follow it. The Autocal and Layout pages still follow the computer only.
- `docs/img/control-dark.png` and `control-light.png` redrawn; `tools/make-control-screenshots.py` regenerates them from simulated status and levels (no hardware).

## [0.1.0] - 2026-11-01

First packaged pre-release. Experimental. The code on main at the time of tagging is packaged as it is, including the configurable speaker layouts (docs/layouts.md), which were finished the same day and have been run in simulation only for layouts other than the author's 5.1.2.

- Debian package `roomtone` (Architecture: all): autocal, the status and control page (theater-ctld), the mpv status overlay and the measurement scripts, installed under /usr/lib/roomtone with commands in /usr/bin. It depends on the `linux-theater-dsp` package.
- `roomtone-setup` enables the status page for one user and can install the mpv overlay, with `--dry-run`, `uninstall` and a check for hand-installed copies in ~/.local/bin that would hide the packaged commands.
- `pipx install git+https://github.com/praveenchandrahas/Roomtone` installs the `autocal` and `autocal-web` commands (pyproject.toml added). The status page and the services need the Debian package.
- Packaging scripts never enable or start a service, never write into a home folder and never touch CamillaDSP or PipeWire configuration.
