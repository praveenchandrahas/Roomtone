#!/usr/bin/env bash
# Records the measurement mic (iMM-6S via your USB DAC) to a WAV.
#
#   ./rec.sh list                 show capture devices (find your DAC's name here)
#   ./rec.sh gain [LEVEL]         set capture level (default 100%; e.g. -18dB), unmuted. Do this once per session
#   ./rec.sh check FILE.wav       print peak level and number of clipped samples (needs python3 + numpy)
#   ./rec.sh NAME [SECONDS]       record NAME.wav for SECONDS (default 45), stereo 48 kHz (24-bit container)
#
# Set the device once per terminal:   export MIC=<name from 'list'>
# Files go to ./recordings/
set -u
OUTDIR=${OUTDIR:-./recordings}
ARG=${1:-}

list_sources() { pactl list short sources 2>/dev/null | grep -v '\.monitor' ; }

case "$ARG" in
  ""|-h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  list)
    echo "Capture devices (name is the second column):"
    list_sources
    echo
    echo "Then:  export MIC=<that name>"
    exit 0 ;;
  check)
    [ -f "${2:-}" ] || { echo "usage: ./rec.sh check FILE.wav"; exit 1; }
    python3 -I - "$2" <<'PY'
import sys, wave, numpy as np
w = wave.open(sys.argv[1]); b = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8).reshape(-1, 3)
x = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
x = np.where(x >= 1 << 23, x - (1 << 24), x)[0::w.getnchannels()] / 2**23
pk = np.abs(x).max()
print(f"peak {20*np.log10(pk):.1f} dBFS, samples above -1 dBFS: {int(np.sum(np.abs(x) > 0.89))}, above -0.1 dBFS: {int(np.sum(np.abs(x) > 0.988))}")
print("OK" if pk < 0.5 else ("TOO HOT: lower the level" if pk > 0.89 else "marginal: aim for peaks below -6 dBFS"))
PY
    exit 0 ;;
  gain)
    [ -n "${MIC:-}" ] || { echo "Set MIC first (see: ./rec.sh list)"; exit 1; }
    pactl set-source-mute "$MIC" 0 && pactl set-source-volume "$MIC" "${2:-100%}"
    echo "Capture level for $MIC:"; pactl get-source-volume "$MIC"
    echo "Do not change it again during the session."
    exit 0 ;;
esac

[ -n "${MIC:-}" ] || { echo "Set MIC first (see: ./rec.sh list)"; exit 1; }
list_sources | awk '{print $2}' | grep -qxF "$MIC" || { echo "No capture device named '$MIC'. Run ./rec.sh list"; exit 1; }
command -v pw-record >/dev/null || { echo "pw-record not found (pipewire-bin)."; exit 1; }

NAME=$ARG; SECS=${2:-45}
mkdir -p "$OUTDIR"
FILE="$OUTDIR/$NAME.wav"
[ -e "$FILE" ] && { echo "$FILE already exists; pick another name (or delete it)."; exit 1; }

echo "Recording $FILE for ${SECS}s. START THE MOVIE CLIP ABOUT 3 s AFTER THIS STARTS."
pw-record --target "$MIC" --rate 48000 --channels 2 --format s24 "$FILE" &
PID=$!
sleep 1
kill -0 "$PID" 2>/dev/null || { echo "Recorder failed to start. Check MIC name and ./rec.sh list."; exit 1; }
for ((i = SECS; i > 0; i--)); do printf '\r%3ds left ' "$i"; sleep 1; done
echo
kill -INT "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
ls -l "$FILE"
