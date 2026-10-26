#!/usr/bin/env bash
# (In the repository since 26 Oct 2026. The one edit to the original: the unit name in the last error message is theater-router, not theater-route.)
# Plays delaytest.wav THROUGH CamillaDSP (so the alignment delays and trims are in the path), then the USB interface.
# The router (theater-route) only handles mpv, so this links the test stream to CamillaDSP itself, by port index,
# exactly as the router does for mpv: file channel i (FL FR RL RR FC LFE SL SR) -> camilladsp-capture:input_{i+1}.
#
#   ./play-through-camilla.sh [delaytest.wav]
#
# Start the recording in another terminal FIRST (./rec.sh after1 45), then run this about 3 s later.
set -u
WAV=${1:-delaytest.wav}
NAME=delaytest-verify
fail() { echo "ERROR: $*" >&2; exit 1; }

for t in pw-play pw-link; do command -v "$t" >/dev/null || fail "$t not found (sudo apt install pipewire-bin)"; done
[ -f "$WAV" ] || fail "$WAV not found (make it with: python3 make-delaytest.py)"

# CamillaDSP must be up and the EQ switch on, or this would not test the alignment at all.
n_in=$(pw-link -i | grep -c '^camilladsp-capture:input_')
[ "$n_in" -eq 8 ] || fail "CamillaDSP capture has $n_in input ports (need 8). Is CamillaDSP running? Try: eq status"
if command -v eq >/dev/null; then
    eq status | grep -q 'CamillaDSP:.*active' || fail "eq status does not show CamillaDSP active"
    eq status | grep -q 'EQ switch:.*ON' || fail "EQ switch is off: run 'eq on' first"
fi
USB=$(pw-link -i | grep -m1 -o '^alsa_output\.usb-0d8c[^:]*' || true)
[ -n "$USB" ] || fail "USB interface not found"
pw-link -l | grep -A1 '^camilladsp-playback:output_1' | grep -q "$USB" || \
    fail "CamillaDSP output 1 is not linked to the USB interface (the router does this; try: systemctl --user restart theater-router)"

echo "Playing $WAV through CamillaDSP in 1 s ..."
pw-play -P "{ node.name=$NAME node.autoconnect=false }" --channel-map FL,FR,RL,RR,FC,LFE,SL,SR "$WAV" &
PID=$!
# wait for the 8 output ports of the new stream
for _ in $(seq 1 30); do
    mapfile -t OUT < <(pw-link -o | grep "^$NAME:output_")
    [ "${#OUT[@]}" -eq 8 ] && break
    kill -0 "$PID" 2>/dev/null || fail "pw-play exited early"
    sleep 0.1
done
[ "${#OUT[@]}" -eq 8 ] || { kill "$PID" 2>/dev/null; fail "expected 8 ports from the test stream, found ${#OUT[@]}"; }
for i in "${!OUT[@]}"; do
    pw-link "${OUT[$i]}" "camilladsp-capture:input_$((i + 1))" || { kill "$PID" 2>/dev/null; fail "could not link ${OUT[$i]}"; }
done
echo "Linked: ${OUT[*]##*:} -> CamillaDSP inputs 1..8. Playing (the whole file; Ctrl-C stops it)."
wait "$PID"
echo "Done."
