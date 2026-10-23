#!/usr/bin/env bash
# What does the soundbar tell the laptop it can accept? Run with the bar connected and ON (HDMI from the laptop to the bar's HDMI IN).
#   ./probe-hdmi.sh
# Look for:  sadN_coding_type  LPCM / AC-3 / DTS ...  with  sadN_channels  (6 or 8 = multichannel LPCM; 2 = stereo only)
# Samsung soundbars are often stereo-only for LPCM and take 5.1 as a Dolby Digital bitstream instead; then use the .ac3 files.
echo "=== ELD per HDMI port (the bar's own list of formats) ==="
found=0
for f in /proc/asound/card*/eld#*; do
  [ -e "$f" ] || continue
  if grep -q "monitor_present[[:space:]]*1" "$f" 2>/dev/null && grep -q "eld_valid[[:space:]]*1" "$f" 2>/dev/null; then
    found=1; echo "--- $f"; cat "$f"
  fi
done
[ $found = 0 ] && echo "No HDMI port reports a valid monitor. Check the cable, the bar's input, and that it is the HDMI IN port."
echo
echo "=== audio sinks ==="
(wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p') || true
echo
echo "=== HDMI ALSA devices (use one as HDMI_DEV for play-bar.sh) ==="
aplay -L 2>/dev/null | grep -i -A1 "^hdmi" | grep -v '^--'
echo
echo "=== PipeWire/Pulse view: formats and channels of the HDMI sinks ==="
pactl list sinks 2>/dev/null | grep -E "Name:|Description:|Channel Map|Formats:|iec958|ac3|eac3|dts|truehd" | sed 's/^[[:space:]]*//'
