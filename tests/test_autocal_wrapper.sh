#!/usr/bin/env bash
# scripts/autocal with a fake pactl and a fake python: what it sets, and when it leaves the mic alone. No hardware.
set -u
cd "$(dirname "$0")/.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir "$T/bin"
cat > "$T/bin/pactl" <<'P'
#!/usr/bin/env bash
echo "pactl $*" >> "$FAKE_LOG"
case "$1 $2 $3" in
  "list short sources") printf '1\talsa_input.usb-SPACETOUCH_USB_Audio_000000000-00.analog-stereo\tPipeWire\n2\talsa_output.x.monitor\tPipeWire\n' ;;
  "get-source-volume "*) echo "Volume: front-left: 39321 / 60%" ;;
esac
exit 0
P
chmod +x "$T/bin/pactl"
cat > "$T/bin/fakepy" <<'P'
#!/usr/bin/env bash
echo "python $* MIC=${MIC:-unset}" >> "$FAKE_LOG"
P
chmod +x "$T/bin/fakepy"
export FAKE_LOG="$T/log" PATH="$T/bin:$PATH" AUTOCAL_PYTHON=fakepy
fail=0
ok() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }

: > "$FAKE_LOG"; unset MIC; scripts/autocal check >/dev/null 2>&1
grep -q "set-source-volume alsa_input.usb-SPACETOUCH.* 60%" "$FAKE_LOG" && grep -q "set-source-mute .* 0" "$FAKE_LOG" && grep -q "python -m autocal check MIC=alsa_input.usb-SPACETOUCH" "$FAKE_LOG" && ok "check: unmutes, sets 60 % on the default mic, runs autocal with MIC set" || { bad "check sets the mic"; cat "$FAKE_LOG"; }

: > "$FAKE_LOG"; scripts/autocal run --simulate >/dev/null 2>&1
! grep -q set-source "$FAKE_LOG" && grep -q "python -m autocal run --simulate" "$FAKE_LOG" && ok "run --simulate leaves the mic alone" || bad "simulate touches the mic"

: > "$FAKE_LOG"; scripts/autocal apply RUN1 >/dev/null 2>&1
! grep -q "pactl" "$FAKE_LOG" && ok "apply does not touch the mic" || bad "apply touches the mic"

: > "$FAKE_LOG"; AUTOCAL_MIC_GAIN=-18dB scripts/autocal mic --spectrum >/dev/null 2>&1
grep -q "set-source-volume .* -18dB" "$FAKE_LOG" && ok "AUTOCAL_MIC_GAIN overrides the level" || bad "gain override"

: > "$FAKE_LOG"; AUTOCAL_MIC_GAIN=keep scripts/autocal check >/dev/null 2>&1
! grep -q set-source-volume "$FAKE_LOG" && grep -q "set-source-mute" "$FAKE_LOG" && ok "keep leaves the level, only unmutes" || bad "keep"

: > "$FAKE_LOG"; MIC=nonexistent scripts/autocal check >"$T/out" 2>&1; rc=$?
[ $rc -ne 0 ] && ! grep -q "python" "$FAKE_LOG" && grep -q "No capture device named 'nonexistent'" "$T/out" && ok "an unknown mic stops before anything runs and lists the devices" || bad "unknown mic"
[ $fail = 0 ] && echo "ALL PASSED 0" || { echo "FAILED"; exit 1; }
