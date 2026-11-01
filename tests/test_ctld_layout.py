"""theater-ctld Layout page API: overview, validate, save (backup, confirm), identify (one channel, -40 dBFS, 2 s) and the security gates.
No audio, no PipeWire: pw-play and pw-link are fakes.   Run: python3 tests/test_ctld_layout.py"""
import http.client, importlib.machinery, importlib.util, json, os, stat, sys, tempfile, threading, wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.realpath(os.path.join(HERE, ".."))
tmp = tempfile.mkdtemp()
os.environ.update(THEATER_STATE_DIR=f"{tmp}/state", THEATER_WEB_DIR=os.path.join(REPO, "gui"), THEATER_AUTOCAL_REPO=REPO,
                  ROOMTONE_LAYOUT=f"{tmp}/cfg/layout.yml", THEATER_AUTOCAL_RUNS=f"{tmp}/runs", THEATER_CAMILLA_WS="127.0.0.1:1")
os.makedirs(f"{tmp}/runs"); os.makedirs(f"{tmp}/bin")
# fakes: pw-link lists the 8 test-stream ports and accepts links; pw-play keeps a copy of the wav it was given
open(f"{tmp}/bin/pw-link", "w").write("#!/bin/sh\nif [ \"$1\" = -o ]; then for p in FL FR RL RR FC LFE SL SR; do echo autocal-play:output_$p; done; fi\nexit 0\n")
open(f"{tmp}/bin/pw-play", "w").write(f"#!/bin/sh\nfor last; do :; done\ncp \"$last\" {tmp}/played.wav\necho \"$@\" > {tmp}/play.args\nsleep 0.3\n")
for f in ("pw-link", "pw-play"):
    os.chmod(f"{tmp}/bin/{f}", 0o755)
os.environ["PATH"] = f"{tmp}/bin:" + os.environ["PATH"]

loader = importlib.machinery.SourceFileLoader("ctld", os.path.join(REPO, "gui", "theater-ctld"))
C = importlib.util.module_from_spec(importlib.util.spec_from_loader("ctld", loader))
loader.exec_module(C)
ok = True


def check(msg, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + msg, extra if not cond else "")
    ok = ok and bool(cond)


C.TOKEN = C.load_token()
srv = C.Server(("127.0.0.1", 0), C.Handler)
PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(method, path, body=None, host=None, headers=None, token=True):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {"Host": host or f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Length"] = str(len(data))
    if method == "POST" and token:
        h["X-Theater-Token"] = C.TOKEN
    c.request(method, path, body=data, headers=h)
    r = c.getresponse(); raw = r.read(); c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


L = C.layout_module()
check("layout.py is found", L is not None)

# ---- read
st, ov = req("GET", "/v1/layout")
check("overview without a layout file: built-in 5.1.2, 8 speakers, the author's upmix fits", st == 200 and ov["layout"]["preset"] == "5.1.2" and len(ov["plan"]) == 8 and ov["upmix"]["fits"] and not ov["file_exists"], str(ov)[:200])
check("overview lists the 7 presets and marks the author's", [p["name"] for p in ov["presets"]] == ["2.0", "2.1", "5.1", "5.1.2", "7.1", "7.1.2", "7.1.4"] and [p["name"] for p in ov["presets"] if p["author"]] == ["5.1.2"])
check("the level stream names for the author's layout are unchanged (TFL/TFR)", C.layout_names() == ["FL", "FR", "TFL", "TFR", "FC", "LFE", "SL", "SR"])
st, page = req("GET", "/layout")
check("the Layout page is served with the token filled in", st == 200 and b"__TOKEN__" not in page and C.TOKEN.encode() in page)

# ---- gates
check("POST without the token is refused", req("POST", "/v1/layout/validate", {"layout": {}}, token=False)[0] == 401)
check("a bad Host header is refused", req("GET", "/v1/layout", host="evil.example:80")[0] == 421)
check("a cross-origin POST is refused", req("POST", "/v1/layout/validate", {"layout": {}}, headers={"Origin": "http://evil.example"})[0] == 403)
check("an oversized body is refused", req("POST", "/v1/layout/validate", b"{" + b" " * 20000 + b"}")[0] == 413)
check("an unknown layout path is 404", req("POST", "/v1/layout/shell", {})[0] == 404)

# ---- validate
good = L.preset("7.1.2").to_dict()
st, v = req("POST", "/v1/layout/validate", {"layout": good})
check("a preset validates, with its plan and upmix answer", st == 200 and v["ok"] and len(v["plan"]) == 10 and v["upmix"]["fits"] is True, str(v)[:200])
bad = L.preset("5.1").to_dict(); bad["channels"][1]["port"] = bad["channels"][0]["port"]
st, v = req("POST", "/v1/layout/validate", {"layout": bad})
check("two speakers on one port is rejected with a plain message", st == 200 and not v["ok"] and "port" in v["error"], str(v))
check("a non-object layout is rejected", req("POST", "/v1/layout/validate", {"layout": "x"})[1]["ok"] is False)
st, v = req("POST", "/v1/layout/validate", {"layout": L.preset("5.1").to_dict()})
check("5.1 says the upmixes do not fit and why", v["ok"] and v["upmix"]["fits"] is False and v["upmix"]["why"], str(v))

# ---- save
st, r = req("POST", "/v1/layout/save", {"layout": good})
check("save needs confirm", st == 409 and r["error"] == "confirm" and not os.path.exists(os.environ["ROOMTONE_LAYOUT"]))
st, r = req("POST", "/v1/layout/save", {"layout": bad, "confirm": True})
check("an invalid layout is never written", st == 400 and not os.path.exists(os.environ["ROOMTONE_LAYOUT"]))
st, r = req("POST", "/v1/layout/save", {"layout": good, "confirm": True})
check("first save writes the file, no backup yet, and says what to restart", st == 200 and r["ok"] and r["backup"] is None and "restart" in r["restart"].lower() and os.path.isfile(os.environ["ROOMTONE_LAYOUT"]), str(r))
check("the file is 0600-safe to read back and loads as the same layout", L.load(None, strict=True).to_dict()["preset"] == "7.1.2")
st, r = req("POST", "/v1/layout/save", {"layout": L.preset("5.1").to_dict(), "confirm": True})
check("the second save backs up the previous file", st == 200 and r["backup"] and os.path.isfile(r["backup"].replace("~", os.path.expanduser("~"))) , str(r))
st, ov = req("GET", "/v1/layout")
check("the overview now reports the saved 5.1 and its file", ov["layout"]["preset"] == "5.1" and ov["file_exists"] and len(ov["plan"]) == 6)
check("level-stream names follow a non-author layout (port order, no renaming)", C.layout_names() == ["FL", "FR", "SL", "SR", "FC", "LFE"])
open(os.environ["ROOMTONE_LAYOUT"], "w").write("version: 1\nname: broken\nchannels: nope\n")
st, ov = req("GET", "/v1/layout")
check("a broken file is reported, and the page still shows the built-in layout", st == 200 and ov["error"] and ov["layout"]["preset"] == "5.1.2", str(ov)[:200])
os.unlink(os.environ["ROOMTONE_LAYOUT"])

# ---- identify
lay = L.preset("5.1.2").to_dict()
st, r = req("POST", "/v1/layout/identify", {"layout": lay, "speaker": "SL"})
check("identify needs confirm", st == 409 and r["error"] == "confirm")
check("identify refuses a name that is not in the layout", req("POST", "/v1/layout/identify", {"layout": lay, "speaker": "ZZ", "confirm": True})[0] == 400)
check("identify refuses a free-form option", req("POST", "/v1/layout/identify", {"layout": lay, "speaker": "SL --level 0", "confirm": True})[0] == 400)
st, r = req("POST", "/v1/layout/identify", {"layout": lay, "speaker": "SL", "confirm": True, "level": 0, "seconds": 60})
check("identify plays and reports the fixed limits (extra level/seconds fields are ignored)", st == 200 and r["dbfs"] == -40 and r["seconds"] == 2 and r["port"] == 6, str(r))
import numpy as np
w = wave.open(f"{tmp}/played.wav"); n = w.getnframes(); ch = w.getnchannels(); raw = w.readframes(n); w.close()
sw = w.getsampwidth()
a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, ch, sw)
x = (a[:, :, 0].astype(np.int32) | (a[:, :, 1].astype(np.int32) << 8) | (a[:, :, 2].astype(np.int32) << 16)) if sw == 3 else None
x = np.where(x >= 1 << 23, x - (1 << 24), x) / float(1 << 23)
peak_db = 20 * np.log10(np.abs(x).max() + 1e-12)
check("the tone is on port 6 (SL, 0-based) only", ch == 8 and [int(np.abs(x[:, i]).max() > 0) for i in range(8)] == [0, 0, 0, 0, 0, 0, 1, 0])
check("the tone peak is at most -40 dBFS and it lasts at most 2 s", peak_db <= -39.9 and n / 48000 <= 2.0 + 1e-6, f"{peak_db:.2f} dBFS, {n / 48000:.2f} s")
# a draft that moves SL to another port is honoured (the tone follows the draft, so the person can test it before saving)
lay2 = json.loads(json.dumps(lay))
for c in lay2["channels"]:
    if c["name"] == "SL":
        c["port"] = 4
    if c["name"] == "FC":
        c["port"] = 6
st, r = req("POST", "/v1/layout/identify", {"layout": lay2, "speaker": "SL", "confirm": True})
check("identify follows the draft's port mapping", st == 429 or (st == 200 and r["port"] == 4), str(r))
check("the draft file is private", stat.S_IMODE(os.stat(f"{tmp}/state/layout-draft.yml").st_mode) == 0o600 if os.path.exists(f"{tmp}/state/layout-draft.yml") else True)
# no tone while an autocal run is active
os.makedirs(f"{tmp}/runs/20261031-120000")
json.dump({"state": "sweeping"}, open(f"{tmp}/runs/20261031-120000/status.json", "w"))
C.IDENTIFY_LAST["t"] = 0
st, r = req("POST", "/v1/layout/identify", {"layout": lay, "speaker": "FL", "confirm": True})
check("no identify tone while an autocal run is active", st == 409 and "in progress" in r["error"], str(r))
st, r = req("POST", "/v1/layout/save", {"layout": lay, "confirm": True})
check("no layout save while an autocal run is active", st == 409, str(r))
# the same limits in autocal itself
sys.path.insert(0, REPO)
from autocal import identify as ID, stimulus as ST
import autocal.layout as AL
ST.activate(AL.preset("7.1.4"))
for name in ST.RESULT_ORDER:
    t = ID.tone(name)
    check(f"7.1.4 {name}: one channel only, <= -40 dBFS, <= 2 s", int((np.abs(t).max(0) > 0).sum()) == 1 and np.abs(t[:, ST.COL[name]]).max() > 0 and 20 * np.log10(np.abs(t).max()) <= -39.99 and len(t) <= 96000)
for bad_args in (dict(level_db=-30), dict(level_db=0), dict(seconds=5), dict(seconds=0.05)):
    try:
        ID.tone("FL", **bad_args); check(f"identify limit {bad_args}", False)
    except ID.IdentifyError:
        check(f"identify refuses {bad_args}", True)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
