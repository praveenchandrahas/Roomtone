#!/usr/bin/env python3
"""Tests for scripts/resource-log with stand-ins for everything it reads: busy processes named mpv and camilladsp, a fake CamillaDSP websocket,
a fake mpv IPC socket, fake journalctl / pw-top / intel_gpu_top. No audio hardware, no root. Standard library only."""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "resource-log")
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ((" - " + str(extra)[:600]) if (extra and not cond) else ""))
    if not cond:
        fails += 1


def exe(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise OSError("closed")
        b += c
    return b


def fake_camilladsp_ws(port_holder, stop):
    """Minimal server side of RFC 6455: handshake, then answer each masked text frame with the value for that command."""
    values = {"GetProcessingLoad": 12.5, "GetBufferLevel": 2048, "GetClippedSamples": 0, "GetState": "Running"}
    clipped = {"n": 0}
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(0.3)
    port_holder.append(srv.getsockname()[1])

    def serve(c):
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(1)
            key = [l for l in req.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0].split(":")[1].strip()
            acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode())
            while not stop.is_set():
                b1, b2 = recv_exact(c, 2)
                ln = b2 & 0x7F
                mask = recv_exact(c, 4)
                data = bytes(x ^ mask[i % 4] for i, x in enumerate(recv_exact(c, ln)))
                cmd = json.loads(data.decode())
                if cmd == "GetClippedSamples":
                    clipped["n"] += 3                      # a counter that grows: 3 per sample
                    v = clipped["n"]
                else:
                    v = values[cmd]
                body = json.dumps({cmd: {"result": "Ok", "value": v}}).encode()
                c.sendall(bytes([0x81, len(body)]) + body)
        except (OSError, ValueError):
            pass
        finally:
            c.close()

    while not stop.is_set():
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        threading.Thread(target=serve, args=(c,), daemon=True).start()
    srv.close()


def fake_mpv_ipc(path, stop):
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(4)
    srv.settimeout(0.3)
    count = {"n": 0}

    def serve(c):
        try:
            buf = b""
            while not stop.is_set():
                chunk = c.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line)
                    prop = req["command"][1]
                    count["n"] += 1
                    data = {"frame-drop-count": count["n"] // 5, "decoder-frame-drop-count": 2, "vo-delayed-frame-count": 1,
                            "mistimed-frame-count": 0, "avsync": -0.012}[prop]
                    c.sendall((json.dumps({"data": data, "request_id": req["request_id"], "error": "success"}) + "\n").encode())
        except (OSError, ValueError):
            pass
        finally:
            c.close()

    while not stop.is_set():
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        threading.Thread(target=serve, args=(c,), daemon=True).start()
    srv.close()


def run_script(env, out, seconds=5, extra=()):
    r = subprocess.run([sys.executable, SCRIPT, "--seconds", str(seconds), "--out", out, *extra], env=env, capture_output=True, text=True, timeout=120)
    return r, open(os.path.join(out, "summary.md")).read() if os.path.exists(os.path.join(out, "summary.md")) else ""


tmp = tempfile.mkdtemp(prefix="resource-log-test-")
stop = threading.Event()
procs = []
try:
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir)
    py = sys.executable
    os.symlink(py, os.path.join(bindir, "mpv"))
    os.symlink(py, os.path.join(bindir, "camilladsp"))
    exe(os.path.join(bindir, "pw-top"), "#!/bin/sh\necho 'S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q   ERR  FORMAT  NAME'\n"
        "echo 'R   41   1024  48000  12.0us  30.0us  0.01  0.02    2  F32P 8 48000 camilladsp'\n")
    exe(os.path.join(bindir, "journalctl"), """#!/bin/sh
now=$(date +%s)
# four buffer-empty lines and one clipping line while mpv runs, one error line; timestamps inside the run
for frac in 100 200 300 400; do echo "$((now-3)).$frac host camilladsp[9]: WARN buffer empty, outputting silence"; done
echo "$((now-2)).5 host camilladsp[9]: WARN clipping detected in channel 3"
echo "$((now-1)).2 host camilladsp[9]: ERROR something broke"
""")
    exe(os.path.join(bindir, "intel_gpu_top"), """#!/bin/sh
echo "["
while true; do
cat <<J
{"period":{"duration":1000.0,"unit":"ms"},"frequency":{"requested":1250.0,"actual":1198.4,"unit":"MHz"},"power":{"GPU":6.3,"Package":14.1,"unit":"W"},
 "engines":{"Render/3D/0":{"busy":41.5,"sema":0.0,"wait":0.0,"unit":"%"},"Video/0":{"busy":22.25,"sema":0.0,"wait":0.0,"unit":"%"}}},
J
sleep 1
done
""")
    busy = "while True: pass"
    procs.append(subprocess.Popen([os.path.join(bindir, "mpv"), "-c", busy]))
    procs.append(subprocess.Popen([os.path.join(bindir, "camilladsp"), "-c", "import time\nwhile True:\n    sum(range(200000)); time.sleep(0.05)"]))
    port_holder = []
    threading.Thread(target=fake_camilladsp_ws, args=(port_holder, stop), daemon=True).start()
    sock = os.path.join(tmp, "mpv.sock")
    threading.Thread(target=fake_mpv_ipc, args=(sock, stop), daemon=True).start()
    time.sleep(0.8)

    env = dict(os.environ, PATH=bindir + ":" + os.environ["PATH"], THEATER_CAMILLA_WS=f"127.0.0.1:{port_holder[0]}", THEATER_MPV_SOCK=sock,
               RESOURCE_LOG_DRM=os.path.join(tmp, "nodrm"))
    r, s = run_script(env, os.path.join(tmp, "full"), 6)
    check("it runs and writes a summary", r.returncode == 0 and "## Problems" in s, r.stderr[-400:])
    check("mpv CPU is seen (a busy loop is about one core)", any(l.startswith("| mpv |") and "mean 9" in l for l in s.splitlines()) or any(l.startswith("| mpv |") and "mean 10" in l for l in s.splitlines()), s)
    check("CamillaDSP is seen as a component", any(l.startswith("| camilladsp |") and "not seen" not in l for l in s.splitlines()))
    play = [l for l in s.splitlines() if "while mpv was running (" in l and l.startswith("- CamillaDSP log")]
    idle = [l for l in s.splitlines() if "while mpv was not running (" in l and l.startswith("- CamillaDSP log")]
    check("buffer-empty log lines while mpv ran are counted under 'running'", play and "buffer-empty/underrun lines 4" in play[0], s)
    check("clipping and error log lines are counted under 'running'", play and "clipping lines 1" in play[0] and "ERROR/WARN lines 1" in play[0], s)
    check("nothing is counted as idle while mpv ran the whole time", idle and "buffer-empty/underrun lines 0" in idle[0], s)
    check("the most frequent problem message is named", 'buffer empty, outputting silence" x4' in s, s)
    check("CamillaDSP processing load comes from the websocket", "processing load: mean 12.5" in s, s)
    check("clipped samples are the growth of the counter", "clipped samples added during the run: " in s and "not measured" not in s.split("clipped samples added during the run: ")[1].split("\n")[0], s)
    check("PipeWire xruns are read from pw-top", "xruns added (pw-top ERR column, best effort): 0" in s, s)
    check("mpv frame drops and sync come from the IPC socket", "dropped by the decoder 0" in s and "audio/video sync offset mean 12.0 ms" in s, s)
    check("the Intel GPU engines are reported", "Render/3D busy mean 41.5" in s and "Video (decode) engine busy mean 22.2" in s, s)
    check("the samples file has the problem columns", all(h in open(os.path.join(tmp, "full", "samples.csv")).readline() for h in ("cdsp_load_pct", "mpv_drops", "throttle_count", "psi_io_pct")))
    check("it does not claim to measure latency", "Not measured: end-to-end audio latency" in s)

    # a run where nothing is available: every source must say 'not measured', none may show a made-up zero
    for p in procs:
        p.kill()
    procs.clear()
    stop.set()
    time.sleep(0.6)
    bare = os.path.join(tmp, "bare")
    os.makedirs(bare)
    for tool in ("python3",):
        pass
    env2 = dict(os.environ, PATH=bare + ":/usr/bin:/bin", THEATER_CAMILLA_WS="127.0.0.1:1", THEATER_MPV_SOCK=os.path.join(tmp, "nosuch.sock"),
                RESOURCE_LOG_DRM=os.path.join(tmp, "nodrm"))
    for name in ("journalctl", "pw-top", "intel_gpu_top", "nvidia-smi", "lspci", "pw-metadata"):
        assert not os.path.exists(os.path.join(bare, name))
    r2, s2 = run_script(env2, os.path.join(tmp, "empty"), 3)
    low = s2.lower()
    check("with no sources it still finishes", r2.returncode == 0 and "## Problems" in s2, r2.stderr[-400:])
    check("no websocket: load says not measured", "processing load: not measured" in s2, s2)
    check("no mpv socket: frame drops say not measured", "mpv frame drops and a/v sync: not measured".lower() in low, s2)
    check("no GPU tool: GPU says not measured", "gpu: not measured" in low, s2)
    check("no mpv process: the table covers all samples and says so", "mpv was never seen running" in s2, s2)
finally:
    stop.set()
    for p in procs:
        p.kill()
print("ALL PASSED" if not fails else f"FAILED {fails}")
sys.exit(1 if fails else 0)
