# Changelog

## [0.1.0] - 2026-11-01

First packaged pre-release. Experimental. The code on main at the time of tagging is packaged as it is, including the configurable speaker layouts (docs/layouts.md), which were finished the same day and have been run in simulation only for layouts other than the author's 5.1.2.

- Debian package `roomtone` (Architecture: all): autocal, the status and control page (theater-ctld), the mpv status overlay and the measurement scripts, installed under /usr/lib/roomtone with commands in /usr/bin. It depends on the `linux-theater-dsp` package.
- `roomtone-setup` enables the status page for one user and can install the mpv overlay, with `--dry-run`, `uninstall` and a check for hand-installed copies in ~/.local/bin that would hide the packaged commands.
- `pipx install git+https://github.com/praveenchandrahas/Roomtone` installs the `autocal` and `autocal-web` commands (pyproject.toml added). The status page and the services need the Debian package.
- Packaging scripts never enable or start a service, never write into a home folder and never touch CamillaDSP or PipeWire configuration.
