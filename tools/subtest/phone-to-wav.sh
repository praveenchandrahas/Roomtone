#!/usr/bin/env bash
# Converts a phone recording (m4a/mp4/mov/caf/wav, any rate) to the 48 kHz 24-bit mono WAV that analyze-subtest.py reads.
#   ./phone-to-wav.sh Recording.m4a recordings/sub-phone.wav
set -eu
[ $# -eq 2 ] || { sed -n '2,3p' "$0"; exit 1; }
[ -e "$2" ] && { echo "$2 already exists; pick another name."; exit 1; }
mkdir -p "$(dirname "$2")"
ffmpeg -loglevel error -i "$1" -vn -ac 1 -ar 48000 -c:a pcm_s24le "$2"
python3 -I - "$2" <<'PY'
import sys, wave, numpy as np
w = wave.open(sys.argv[1]); b = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8).reshape(-1, 3)
x = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
x = np.where(x >= 1 << 23, x - (1 << 24), x) / 2**23
pk = np.abs(x).max()
print(f"{w.getnframes()/48000:.0f} s, peak {20*np.log10(pk):.1f} dBFS, samples above -1 dBFS: {int(np.sum(np.abs(x) > 0.89))}")
print("OK" if pk < 0.5 else ("CLIPPED: phone input overloaded; move it back" if pk > 0.89 else "marginal: aim for peaks below -6 dBFS"))
PY
