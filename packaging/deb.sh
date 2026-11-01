# shellcheck shell=bash disable=SC2034  # sourced by build-deb.sh, which uses these variables
# Package definition for kit/build-deb.sh (sourced; see Software-Defined-Home-Theatre/packaging/kit).
PKG=roomtone
HOMEPAGE=https://github.com/praveenchandrahas/Roomtone
SUMMARY="room measurement, autocal and status page for a home theatre"
DESCRIPTION="The measurement part of the Software Defined Home Theatre project: autocal
(sweep measurement, impulse-response analysis, a proposed CamillaDSP room
profile with a timed trial and rollback), the status and control page
(theater-ctld, listens on 127.0.0.1 only), the mpv status overlay and the
measurement helper scripts.
.
Written for one rig and one room. Applying a result changes the CamillaDSP
profile only when you run autocal apply (or press the button on the page) and
confirm. This package does not enable or start any service and does not write
into any home folder; run roomtone-setup for that."
DEPENDS="python3, python3-yaml, python3-numpy, python3-scipy, linux-theater-dsp (>= 0.1.0), pipewire-bin, pulseaudio-utils, systemd"
RECOMMENDS="python3-matplotlib, mpv"
SUGGESTS="python3-lupa"
PYDIRS="/usr/lib/roomtone"

stage() {
  local L=/usr/lib/roomtone
  instdir autocal "$L/autocal"
  instdir tools "$L/tools"
  for f in autocal rec.sh tone-test.sh play-through-camilla.sh make-bedtest.py make-delaytest.py resource-log; do inst 755 "scripts/$f" "$L/scripts/$f"; done
  pyshebang "$L/scripts/make-bedtest.py" "$L/scripts/make-delaytest.py" "$L/scripts/resource-log"
  inst 755 gui/theater-ctld "$L/gui/theater-ctld"; pyshebang "$L/gui/theater-ctld"
  for f in index.html autocal.html layout.html; do inst 644 "gui/$f" "$L/web/$f"; done
  inst 644 overlay/theater_status.lua "$L/overlay/theater_status.lua"
  inst 755 overlay/install-theater-status.sh "$L/overlay/install-theater-status.sh"
  # tools/*.py are libraries or helpers run by hand; make sure none is flagged as a stray executable script
  link ../lib/roomtone/gui/theater-ctld /usr/bin/theater-ctld
  script /usr/bin/autocal <<'X'
#!/bin/sh
# Packaged launcher: runs and results go to ~/.local/share/roomtone/runs unless AUTOCAL_RUNS_DIR is set; applying uses the packaged set-eq.
: "${AUTOCAL_RUNS_DIR:=${XDG_DATA_HOME:-$HOME/.local/share}/roomtone/runs}"
: "${SET_EQ:=/usr/lib/linux-theater-dsp/set-eq.py}"
export AUTOCAL_RUNS_DIR SET_EQ PYTHONDONTWRITEBYTECODE=1
exec /usr/lib/roomtone/scripts/autocal "$@"
X
  inst 755 packaging/roomtone-setup /usr/bin/roomtone-setup
  inst 644 packaging/theater-ctld.service /usr/lib/systemd/user/theater-ctld.service
  local D=/usr/share/doc/roomtone
  for f in docs/measurement.md docs/layouts.md autocal/README.md; do inst 644 "$f" "$D/$(basename "$f")"; done
  inst 644 data/mic/dayton-imm-6s-calibration.txt "$D/examples/dayton-imm-6s-calibration.txt"
  inst 644 data/README.md "$D/examples/data-README.md"
  manpage autocal "measure the room and propose a CamillaDSP room profile (run, report, apply, keep, rollback)"
  manpage theater-ctld "status and control page for the home theatre, served on 127.0.0.1 only"
  manpage roomtone-setup "enable or disable the Roomtone status page and mpv overlay for one user" "Subcommands: install, uninstall, check. Options: --dry-run, --overlay."
}
