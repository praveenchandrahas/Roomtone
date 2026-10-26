#!/usr/bin/env bash
# Installs the local status and control page (Roomtone: gui/) (theater-ctld, http://127.0.0.1:8765, this computer only).
#   ./install-ctld.sh          install and start it (safe to re-run; also how you update)
#   ./install-ctld.sh remove   stop and remove it again (audio is never affected either way)
# Needs the updated router first (`eq status --json`): run ./install-autoroute.sh before this one.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$HERE
BIN=$HOME/.local/bin
WEB=$HOME/.local/share/theater-ctld
UNITDIR=$HOME/.config/systemd/user
UNIT=theater-ctld.service

if [ "${1:-}" = remove ]; then
  systemctl --user disable --now "$UNIT" 2>/dev/null
  rm -f "$UNITDIR/$UNIT" "$BIN/theater-ctld"; rm -rf "$WEB"
  systemctl --user daemon-reload
  echo "Removed. (The token file ~/.local/state/theater/ctl-token is left; delete it if you like.)"
  exit 0
fi

[ -f "$HERE/theater-ctld" ] && [ -f "$REPO/index.html" ] && [ -f "$REPO/autocal.html" ] && [ -f "$REPO/$UNIT" ] || { echo "Run this from a full Roomtone checkout (gui/ folder)."; exit 1; }
command -v eq >/dev/null || [ -x "$BIN/eq" ] || { echo "'eq' not found: run linux-theater-dsp's ./scripts/install-autoroute.sh first."; exit 1; }
"$BIN/eq" status --json 2>/dev/null | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null || { echo "Your installed 'eq' has no 'status --json' yet: run linux-theater-dsp's ./scripts/install-autoroute.sh to update it, then re-run this."; exit 1; }
mkdir -p "$BIN" "$WEB" "$UNITDIR"
install -m 755 "$HERE/theater-ctld" "$BIN/theater-ctld"
install -m 644 "$REPO/index.html" "$WEB/index.html"
install -m 644 "$REPO/autocal.html" "$WEB/autocal.html"
# the autocal page reads this checkout's runs/ folder (where `autocal run` writes)
sed "s|^Environment=THEATER_AUTOCAL_RUNS=.*|Environment=THEATER_AUTOCAL_RUNS=$(cd "$HERE/.." && pwd)/runs|" "$REPO/$UNIT" > "$UNITDIR/$UNIT"
chmod 644 "$UNITDIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT" 2>&1 | sed 's/^/  /'
sleep 1
echo
echo "theater-ctld: $(systemctl --user is-active $UNIT)   page: http://127.0.0.1:8765   autocal: http://127.0.0.1:8765/autocal   (this computer only; nothing listens on the network)"
ss -ltn 2>/dev/null | grep -E ':8765\b' | sed 's/^/  listening: /'
echo "CamillaDSP live numbers need its websocket (-p 1234 -a 127.0.0.1 in the unit); the page says so if it is off."
