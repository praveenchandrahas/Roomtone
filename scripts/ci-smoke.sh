#!/usr/bin/env bash
# Smoke test of the INSTALLED roomtone package (and the installed linux-theater-dsp it depends on). Throw-away HOME; no audio hardware,
# no PipeWire, no CamillaDSP. It runs a SIMULATED autocal run, starts the status page and checks that it listens on 127.0.0.1 only.
#   scripts/ci-smoke.sh
set -euo pipefail
H=$(mktemp -d); SRV=
cleanup() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$H"; }
trap cleanup EXIT
export HOME=$H XDG_CONFIG_HOME=$H/.config XDG_DATA_HOME=$H/.local/share ROOMTONE_LAYOUT=$H/none.yml
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
for c in autocal theater-ctld roomtone-setup eq; do command -v "$c" >/dev/null || fail "$c is not on PATH"; done
case "$(command -v autocal)" in /usr/bin/autocal) ;; *) fail "autocal resolves to $(command -v autocal)" ;; esac

echo "== simulated autocal run (nothing is measured and the live config is never touched)"
mkdir -p "$H/live"; echo "ORIGINAL CONFIG" > "$H/live/config.yml"
export AUTOCAL_CONFIG=$H/live/config.yml AUTOCAL_ROOM=$H/live/room.json AUTOCAL_BACKUP_DIR=$H/bk
autocal run --simulate --sweep-s 3 --tail-s 1.5 --auto-continue >"$H/run.txt" 2>&1 || { tail -20 "$H/run.txt"; fail "autocal run --simulate"; }
RUN=$(ls -d "$H"/.local/share/roomtone/runs/*/ 2>/dev/null | tail -1)
[ -n "$RUN" ] || { cat "$H/run.txt"; fail "no run folder under ~/.local/share/roomtone/runs"; }
for f in results.json run.json status.json report.html; do [ -f "$RUN/$f" ] || fail "run lacks $f"; done
python3 - "$RUN/results.json" <<'PY' || fail "results.json"
import json, sys
r = json.load(open(sys.argv[1]))
assert r.get("simulated") is True, "run is not marked simulated"
print("simulated run ok: verdict", r.get("verdict"), "layout", (r.get("layout") or {}).get("name", "built-in"))
PY
[ "$(cat "$H/live/config.yml")" = "ORIGINAL CONFIG" ] || fail "the live config was changed"
[ ! -e "$H/bk" ] || fail "a backup dir appeared: something was applied"
autocal report "$(basename "$RUN")" >/dev/null || fail "autocal report"

echo "== status page on 127.0.0.1"
printf '#!/bin/sh\nif [ "$1" = status ]; then cat %s/status.json; exit 0; fi\nexit 0\n' "$H" > "$H/eq-stub"; chmod +x "$H/eq-stub"
cat > "$H/status.json" <<'J'
{"schema": 1, "eq": {"switch": "on", "router": "active", "camilla": "active", "camilla_summary": "x", "state_file": "f"},
 "dsp": {"profile": "film", "pmode": "auto", "flavours": {"stereo": "balanced", "bed": "balanced"}, "engine": {"state": "on", "service": "active", "ports_up": false, "linked": false}},
 "pipewire": true, "apps": [], "usb_up": true, "mpv": {"playing": false, "channels": [], "source": null, "route": null, "verdict": null}}
J
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
THEATER_EQ=$H/eq-stub THEATER_CTL_PORT=$PORT THEATER_STATE_DIR=$H/state THEATER_CAMILLA_WS=127.0.0.1:1 \
  THEATER_AUTOCAL_RUNS=$H/.local/share/roomtone/runs THEATER_AUTOCAL_REPO=/usr/lib/roomtone \
  theater-ctld >"$H/ctld.log" 2>&1 &
SRV=$!
for _ in $(seq 1 50); do python3 -c "import socket,sys; socket.create_connection(('127.0.0.1',$PORT),0.2)" 2>/dev/null && break; sleep 0.2; done
python3 - "$PORT" <<'PY' || { cat "$H/ctld.log"; fail "status page"; }
import http.client, json, sys
p = int(sys.argv[1])
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", p, timeout=5); c.request("GET", path, headers={"Host": f"127.0.0.1:{p}"}); r = c.getresponse(); return r.status, r.read()
st, body = get("/")
assert st == 200 and b"<html" in body.lower(), ("index", st)
st, body = get("/v1/status"); assert st == 200 and json.loads(body).get("schema") == 1, ("status", st, body[:100])
st, body = get("/v1/autocal"); assert st == 200 and json.loads(body).get("found"), ("autocal", st, body[:200])
st, body = get("/layout"); assert st == 200, ("layout", st)
c = http.client.HTTPConnection("127.0.0.1", p, timeout=5); c.request("GET", "/v1/status", headers={"Host": "evil.example"}); assert c.getresponse().status in (400, 403, 421), "foreign Host header accepted"
print("page, status, autocal runs, layout: ok; foreign Host header refused")
PY
python3 - "$SRV" <<'PY' || fail "the page listens on something other than loopback"
import os, sys
pid = sys.argv[1]
inodes = set()
for fd in os.listdir(f"/proc/{pid}/fd"):
    try: t = os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError: continue
    if t.startswith("socket:["): inodes.add(t[8:-1])
bad, seen = [], 0
for fn, loop in (("/proc/net/tcp", {"0100007F"}), ("/proc/net/tcp6", {"00000000000000000000000001000000", "0000000000000000FFFF00000100007F"})):
    try: lines = open(fn).read().splitlines()[1:]
    except OSError: continue
    for l in lines:
        f = l.split()
        if f[3] == "0A" and f[9] in inodes:              # LISTEN sockets of this process only
            seen += 1
            addr, port = f[1].split(":")
            if addr not in loop: bad.append((fn, addr, int(port, 16)))
if bad or not seen: print("listeners of the page process:", seen, "non-loopback:", bad); sys.exit(1)
print(f"the page process has {seen} listening socket(s), all on 127.0.0.1 or ::1")
PY
kill "$SRV"; wait "$SRV" 2>/dev/null || true; SRV=

echo "== setup command (dry run only)"
roomtone-setup --dry-run install --overlay | tee "$H/setup.txt"
grep -q 'would run: systemctl --user enable --now theater-ctld.service' "$H/setup.txt" || fail "dry-run text"
roomtone-setup --dry-run uninstall >/dev/null
[ ! -e "$H/.config/systemd" ] && [ ! -e "$H/.config/mpv" ] || fail "dry run created files in the home folder"
echo "SMOKE OK (roomtone)"
