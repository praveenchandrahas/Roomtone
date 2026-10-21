#!/usr/bin/env bash
# Usage: ./tone-test.sh <channel 1-8> <freq Hz> <cd|direct>
#   channel: 1=FL 2=FR 3=RL(height) 4=RR(height) 5=FC 6=LFE 7=SL 8=SR
#   cd     = route the tone through CamillaDSP (bass filters active)
#   direct = send the tone straight to the USB sink (no filters)
# Stop with: pkill speaker-test
set -u
[ $# -eq 3 ] || { echo "usage: $0 <channel 1-8> <freq Hz> <cd|direct>"; exit 1; }
CH=$1; F=$2; MODE=$3
USB=alsa_output.usb-0d8c_USB_Sound_Device-00.analog-surround-71
SRC='ALSA plug-in [speaker-test]'
PORTS=(FL FR RL RR FC LFE SL SR)

pkill speaker-test 2>/dev/null
sleep 0.4
PULSE_SINK=$USB setsid speaker-test -D pulse -c 8 -t sine -f "$F" -s "$CH" >/dev/null 2>&1 &
sleep 1

if [ "$MODE" = "cd" ]; then
  for i in 0 1 2 3 4 5 6 7; do
    p=${PORTS[$i]}
    pw-link -d "$SRC:output_$p" "$USB:playback_$p" 2>/dev/null
    pw-link "$SRC:output_$p" "camilladsp-capture:input_$((i+1))"
  done
fi
echo "Playing ${F} Hz on channel ${CH} (${PORTS[$((CH-1))]}), mode: ${MODE}. Stop: pkill speaker-test"
