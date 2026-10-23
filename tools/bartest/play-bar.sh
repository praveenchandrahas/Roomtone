#!/usr/bin/env bash
# Plays a bar-*-5.1 file to the soundbar. UNTESTED on the real hardware: the first run is also a test of this script.
#   ./play-bar.sh pcm   bar-sweeptest-5.1.flac     6-channel LPCM (only if probe-hdmi.sh showed multichannel LPCM)
#   ./play-bar.sh ac3   bar-sweeptest-5.1.ac3      Dolby Digital bitstream passthrough
#   ./play-bar.sh eac3  bar-sweeptest-5.1.eac3     Dolby Digital Plus bitstream passthrough
# For pcm set SINK to the PipeWire sink name (wpctl status); for ac3/eac3 set HDMI_DEV to an ALSA device from probe-hdmi.sh,
# e.g.  export HDMI_DEV='hdmi:CARD=PCH,DEV=0'   (passthrough needs a direct ALSA device, not the PipeWire mix).
# Record with the usual:  ./rec.sh NAME SECONDS   (start it about 8 s before this).
set -eu
MODE=${1:-}; FILE=${2:-}
[ -n "$MODE" ] && [ -f "$FILE" ] || { sed -n '2,9p' "$0"; exit 1; }
command -v mpv >/dev/null || { echo "mpv not found"; exit 1; }
case "$MODE" in
  pcm)  [ -n "${SINK:-}" ] || { echo "set SINK=<pipewire sink name> (see: wpctl status)"; exit 1; }
        exec mpv --no-video --no-config --ao=pipewire --audio-device="pipewire/$SINK" --audio-channels=5.1 --audio-samplerate=48000 --volume=100 "$FILE" ;;
  ac3|eac3) [ -n "${HDMI_DEV:-}" ] || { echo "set HDMI_DEV=<alsa hdmi device> (see: ./probe-hdmi.sh)"; exit 1; }
        exec mpv --no-video --no-config --ao=alsa --audio-device="alsa/$HDMI_DEV" --audio-spdif=ac3,eac3 --volume=100 "$FILE" ;;
  *) echo "mode must be pcm, ac3 or eac3"; exit 1 ;;
esac
