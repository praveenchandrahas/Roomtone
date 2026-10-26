#!/usr/bin/env bash
# Installs the on-screen theatre panel (theater_status.lua) into the mpv config folders.
#   ./install-theater-status.sh            copy the script into ~/.config/mpv and ~/.config/mpv-object-audio (whichever exist)
#   ./install-theater-status.sh --bindings also add the key (c: status and controls) to each input.conf, once; it replaces the
#                                          F9/F10 lines an earlier version of this installer added
#   ./install-theater-status.sh remove     take the script out again (and the input.conf lines, if this installer added them)
# It never edits input.conf unless you pass --bindings or remove. The status page for the browser is separate: linux-theater-dsp/scripts/install-ctld.sh.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/theater_status.lua"
MARK="# theater_status (added by install-theater-status.sh)"
LINES='c script-binding theater_status/control'
[ -f "$SRC" ] || { echo "theater_status.lua must be next to this script."; exit 1; }

strip_block() {   # remove the marked block (the mark and the theater_status bindings under it) from input.conf $1
  python3 - "$1" "$MARK" <<'PY'
import sys
p, mark = sys.argv[1], sys.argv[2]
out = []; inblock = False
for l in open(p).read().split("\n"):
    if l.strip() == mark: inblock = True; continue
    if inblock and "script-binding theater_status/" in l: continue
    inblock = False; out.append(l)
open(p, "w").write("\n".join(out))
PY
}

found=0
for CONF in "$HOME/.config/mpv" "$HOME/.config/mpv-object-audio"; do
  [ -d "$CONF" ] || continue
  found=1
  if [ "${1:-}" = remove ]; then
    rm -f "$CONF/scripts/theater_status.lua"
    if [ -f "$CONF/input.conf" ] && grep -qF "$MARK" "$CONF/input.conf"; then strip_block "$CONF/input.conf"; fi
    echo "removed from $CONF"
    continue
  fi
  mkdir -p "$CONF/scripts"
  install -m 644 "$SRC" "$CONF/scripts/theater_status.lua"
  echo "installed: $CONF/scripts/theater_status.lua"
  if [ "${1:-}" = --bindings ]; then
    if grep -qF "$MARK" "$CONF/input.conf" 2>/dev/null; then strip_block "$CONF/input.conf"; echo "  replaced the earlier keys in $CONF/input.conf"; fi
    if grep -qE "^[[:space:]]*c[[:space:]]" "$CONF/input.conf" 2>/dev/null; then
      echo "  NOTE: c is already bound in $CONF/input.conf; the new binding comes later in the file and wins. Edit it there if you prefer another key."
    fi
    printf '\n%s\n%s\n' "$MARK" "$LINES" >> "$CONF/input.conf"
    echo "  key added to $CONF/input.conf"
  fi
done
[ "$found" = 1 ] || { echo "No mpv config folder found (~/.config/mpv or ~/.config/mpv-object-audio)."; exit 1; }
if [ "${1:-}" != remove ] && [ "${1:-}" != --bindings ]; then
  echo
  echo "Add this line to input.conf in the same folder(s), or re-run with --bindings to do it for you:"
  echo "  $LINES"
fi
