"""theater-ctld against a fake `eq` and a fake CamillaDSP websocket.   Run: python3 tests/test_ctld.py"""
import base64, hashlib, http.client, importlib.machinery, importlib.util, json, os, socket, stat, struct, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
os.environ["THEATER_STATE_DIR"] = f"{tmp}/state"
os.environ["THEATER_WEB_DIR"] = os.path.join(HERE, "..", "gui")
LOG = f"{tmp}/eq.log"
STATUS = f"{tmp}/status.json"
FAKE = f"{tmp}/eq"
open(FAKE, "w").write(f"""#!/bin/sh
if [ "$1" = status ]; then cat {STATUS}; exit 0; fi
echo "$@" >> {LOG}
[ -n "$FAKE_EQ_SLEEP" ] && sleep $FAKE_EQ_SLEEP
exit 0
""")
os.chmod(FAKE, 0o755)
os.environ["THEATER_EQ"] = FAKE
os.environ["FAKE_EQ_SLEEP"] = ""
os.environ["THEATER_CAMILLA_WS"] = "127.0.0.1:1"        # nothing listens there

loader = importlib.machinery.SourceFileLoader("ctld", os.path.join(HERE, "..", "gui", "theater-ctld"))
C = importlib.util.module_from_spec(importlib.util.spec_from_loader("ctld", loader))
loader.exec_module(C)
ok = True


def check(msg, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + msg, extra)
    ok = ok and cond


def status(playing=False, switch="on", pmode="auto", engine="on", apps=()):
    d = {"schema": 1, "eq": {"switch": switch, "router": "active", "camilla": "active", "camilla_summary": "x", "state_file": "f"},
         "dsp": {"profile": "film", "pmode": pmode, "flavours": {"stereo": "balanced", "bed": "balanced"},
                 "engine": {"state": engine, "service": "active", "ports_up": False, "linked": False}},
         "pipewire": True, "apps": list(apps), "usb_up": True,
         "mpv": {"playing": playing, "channels": [], "source": None, "route": None, "verdict": None}}
    json.dump(d, open(STATUS, "w"))
    C.SNAP.t = 0.0


def calls():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


C.TOKEN = C.load_token()
check("token file is private (0600) and long", stat.S_IMODE(os.stat(C.TOKEN_FILE).st_mode) == 0o600 and len(C.TOKEN) >= 32)
check("the token is stable across restarts", C.load_token() == C.TOKEN)
srv = C.Server(("127.0.0.1", 0), C.Handler)
PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(method, path, body=None, host=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    h = {"Host": host or f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body) if not isinstance(body, str) else body
        h["Content-Type"] = "application/json"
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    if data is not None:
        c.putheader("Content-Length", str(len(data.encode())))
    c.endheaders(data.encode() if data is not None else None)
    r = c.getresponse()
    raw = r.read()
    try:
        j = json.loads(raw)
    except ValueError:
        j = None
    return r.status, j, raw, r


def act(body, token=True, headers=None, **kw):
    h = {"X-Theater-Token": C.TOKEN} if token else {}
    h.update(headers or {})
    return req("POST", "/v1/action", body, headers=h, **kw)


status()
code, _, raw, r = req("GET", "/")
check("page is served with the token inside, and a CSP header", code == 200 and C.TOKEN.encode() in raw and b"__TOKEN__" not in raw and r.getheader("Content-Security-Policy"))
check("localhost Host header is accepted too", req("GET", "/v1/status", host=f"localhost:{PORT}")[0] == 200)
check("a wrong Host header is refused (DNS rebinding)", req("GET", "/v1/status", host="evil.example:80")[0] == 421)
check("a cross-origin Origin is refused on GET", req("GET", "/v1/status", headers={"Origin": "https://evil.example"})[0] == 403)
check("a cross-origin Origin is refused on POST even with the token", act({"action": "eq", "value": "off"}, headers={"Origin": "https://evil.example"})[0] == 403)
check("the same-origin Origin is fine", req("GET", "/v1/status", headers={"Origin": f"http://127.0.0.1:{PORT}"})[0] == 200)
code, j, _, r = req("GET", "/v1/status")
check("status carries eq status plus a live block that says why it is missing", code == 200 and j["eq"]["switch"] == "on" and j["live"]["ok"] is False and "websocket" in j["live"]["reason"], str(j["live"]))
check("no CORS headers are ever sent", r.getheader("Access-Control-Allow-Origin") is None)
check("POST without a token is 401", act({"action": "eq", "value": "off"}, token=False)[0] == 401)
check("POST with a wrong token is 401", req("POST", "/v1/action", {"action": "eq", "value": "off"}, headers={"X-Theater-Token": "x" * 40})[0] == 401)
check("unknown action 400", act({"action": "rm"})[0] == 400)
check("bad enum 400", act({"action": "mode", "value": "party"})[0] == 400)
check("shell metacharacters never reach argv", act({"action": "flavour", "kind": "stereo", "value": "wide; reboot"})[0] == 400 and act({"action": "mode", "value": "film;id"})[0] == 400)
check("a non-object body is 400", act("[1,2]")[0] == 400 and act("not json")[0] == 400)
check("nothing was run by any of those", calls() == [], str(calls()))

status(switch="on")
code, j, *_ = act({"action": "eq", "value": "on"})
check("setting what is already set is a no-op (nothing run)", code == 200 and j.get("noop") and calls() == [])
code, j, *_ = act({"action": "mode", "value": "film"})
check("idle: a mode change runs `eq mode film` without confirmation", code == 200 and j["ok"] and calls() == ["mode film"], str(calls()))
status(playing=True)
open(LOG, "w").close()
code, j, *_ = act({"action": "mode", "value": "stereo"})
check("playing: a mode change needs confirm (409) and runs nothing", code == 409 and j["error"] == "confirm" and "silence" in j["message"] and calls() == [])
code, j, *_ = act({"action": "mode", "value": "stereo", "confirm": True})
check("playing: with confirm it runs `eq mode stereo`", code == 200 and calls() == ["mode stereo"], str(calls()))
open(LOG, "w").close()
code, j, *_ = act({"action": "eq", "value": "off"})
check("playing: `eq off` needs confirm because it is louder", code == 409 and "louder" in j["message"])
check("confirm must be exactly true", act({"action": "eq", "value": "off", "confirm": "yes"})[0] == 409)
status(playing=True, apps=[{"name": "Google Chrome", "patched": 2, "total": 2}])
code, j, *_ = act({"action": "engine", "value": "off"})
check("a stereo app counts as playing too", code == 409)
status()
open(LOG, "w").close()
for body, want in (({"action": "flavour", "kind": "bed", "value": "wide"}, "flavour bed wide"), ({"action": "engine", "value": "off"}, "engine off"),
                   ({"action": "eq", "value": "off"}, "off")):
    act(body)
check("flavour, engine and eq map to the exact eq commands", calls() == ["flavour bed wide", "engine off", "off"], str(calls()))
check("a flavour of the wrong kind is refused", act({"action": "flavour", "kind": "stereo", "value": "subtle"})[0] == 400)

# one action at a time
status(); os.environ["FAKE_EQ_SLEEP"] = "2"
res = {}
t = threading.Thread(target=lambda: res.update(first=act({"action": "mode", "value": "bed"})[0]))
t.start(); time.sleep(0.5)
second = act({"action": "mode", "value": "stereo"})[0]
t.join()
os.environ["FAKE_EQ_SLEEP"] = ""
check("a second action while one is running is refused (429)", res["first"] == 200 and second == 429, f"({res}, {second})")

# SSE
c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
c.putrequest("GET", "/v1/events", skip_host=True); c.putheader("Host", f"127.0.0.1:{PORT}"); c.endheaders()
r = c.getresponse(); line = r.fp.readline().decode()
check("the event stream sends the status as the first event", r.status == 200 and line.startswith("data: ") and json.loads(line[6:])["schema"] == 1)
c.close()

# constants stay in step with the router
ws = os.environ.get("SDHT_WORKSPACE") or os.path.dirname(os.path.dirname(HERE))
route = os.path.join(ws, "linux-theater-dsp", "scripts", "theater-route")
if os.path.exists(route):
    rl = importlib.machinery.SourceFileLoader("trc", route)
    TR = importlib.util.module_from_spec(importlib.util.spec_from_loader("trc", rl)); rl.exec_module(TR)
    check("FLAVOURS and PMODES match theater-route", C.FLAVOURS == TR.FLAVOURS and C.PMODES == TR.PMODES)
else:
    print("SKIP FLAVOURS/PMODES check (linux-theater-dsp not next to this repository)")

# websocket client against a fake CamillaDSP
REPLIES = {"GetState": "Running", "GetCaptureRate": 48000, "GetBufferLevel": 1024, "GetClippedSamples": 0, "GetProcessingLoad": 7.5,
           "GetCaptureSignalPeak": [-20.1, -30.0], "GetPlaybackSignalPeak": [-18.0, -25.5]}
asked = []


def fake_ws(listener):
    conn, _ = listener.accept()
    req_ = b""
    while b"\r\n\r\n" not in req_:
        req_ += conn.recv(1)
    key = [l for l in req_.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0].split(":")[1].strip()
    acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode())
    try:
        while True:
            op, data = C.ws_read_frame(conn)
            if op == 8:
                break
            name = json.loads(data.decode())
            asked.append(name)
            body = json.dumps({name: {"result": "Ok", "value": REPLIES[name]}} if name in REPLIES else {name: {"result": "Error"}}).encode()
            conn.sendall(bytes([0x81, len(body)]) + body if len(body) < 126 else bytes([0x81, 126]) + struct.pack(">H", len(body)) + body)
    except OSError:
        pass
    conn.close()


ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(1); wsport = ls.getsockname()[1]
threading.Thread(target=fake_ws, args=(ls,), daemon=True).start()
C.WS_PORT = wsport
live = C.live_block()
check("websocket: live block reads the numbers", live["ok"] and live["state"] == "Running" and live["load_percent"] == 7.5 and live["capture_peak_db"] == [-20.1, -30.0], str(live))
check("websocket: only read-only commands were sent", set(asked) <= set(C.WS_READ_ONLY), str(asked))
try:
    C.ws_query(["SetConfig"]); refused = False
except ValueError:
    refused = True
check("websocket: a write command is refused in code before anything is sent", refused)
C.WS_PORT = 1
check("websocket: unreachable gives ok false with the unit hint, never an exception", C.live_block()["ok"] is False)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
