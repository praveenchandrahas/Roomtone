"""autocal web GUI server: stdlib http.server, localhost only, allow-listed actions.

    python3 -m autocal.web [--runs-dir DIR] [--port 8765] [--bind ADDR] [--mock]

Security model (see plans/gui-plan.md): binds 127.0.0.1 unless --bind is given; Host and Origin are
checked on every request (DNS rebinding); POST bodies must be application/json and are capped; run ids are
validated with a strict regex; static files are served from one flat directory by exact name; subprocesses
are started from fixed argv lists (never a shell) and only values that passed validation are appended.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}")
PROMPT_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
MODES = ("single", "five")
MAX_BODY = 4096
MAX_JSON_READ = 32 * 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_NAME_RE = re.compile(r"[A-Za-z0-9_-]+\.(?:html|js|css|svg|ico|txt)")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
ACTIVE_STATES = ("preflight", "ramp", "channel_id", "sweeping", "waiting_user", "analysing")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def valid_id(s):
    return isinstance(s, str) and ID_RE.fullmatch(s) is not None


def read_json_file(path, retries=3):
    """Read a JSON file the core rewrites atomically; tolerate a transient miss."""
    for i in range(retries):
        try:
            if os.path.getsize(path) > MAX_JSON_READ:
                raise ApiError(500, "file too large")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            if i == retries - 1:
                raise ApiError(500, "unreadable json: " + os.path.basename(path))
            time.sleep(0.03)
    return None


class Controller:
    """Holds configuration, the child run process, and the single in-flight action."""

    def __init__(self, runs_dir, mock=False, python=None, port=8765, bind="127.0.0.1", extra_hosts=()):
        self.runs_dir = Path(runs_dir).resolve()
        self.mock = mock
        self.python = python or sys.executable
        self.port = port
        self.bind = bind
        self.allowed_hosts = set(LOOPBACK) | set(extra_hosts)
        self.child = None
        self.lock = threading.Lock()
        self.action = {"running": False}
        self.cwd = str(Path(__file__).resolve().parent.parent.parent)   # directory that contains the autocal package

    # ---- allow-listed argv builders (the only place subprocess argv is made) ----
    def argv_run(self, mode, simulate):
        if self.mock:
            return [self.python, "-m", "autocal.web.mock", "live", str(self.runs_dir), "--mode", mode]
        argv = [self.python, "-m", "autocal", "run", "--runs-dir", str(self.runs_dir), "--mode", mode]
        if simulate:
            argv.append("--simulate")
        return argv

    def argv_action(self, name, run_id=None):
        base = [self.python, "-m", "autocal.web.mock", "cli"] if self.mock else [self.python, "-m", "autocal"]
        if name == "apply":
            return base + ["apply", run_id]
        if name in ("keep", "rollback"):
            return base + [name]
        raise ApiError(404, "unknown action")

    def env(self):
        e = dict(os.environ)
        e["AUTOCAL_RUNS_DIR"] = str(self.runs_dir)
        e["PYTHONPATH"] = self.cwd + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
        return e

    # ---- runs ----
    def run_dir(self, run_id):
        if not valid_id(run_id):
            raise ApiError(400, "invalid run id")
        d = self.runs_dir / run_id
        if not d.is_dir() or d.is_symlink():
            raise ApiError(404, "no such run")
        return d

    def list_runs(self):
        out = []
        if not self.runs_dir.is_dir():
            return out
        for p in sorted(self.runs_dir.iterdir(), reverse=True):
            if not valid_id(p.name) or not p.is_dir() or p.is_symlink():
                continue
            item = {"id": p.name, "mode": None, "simulated": None, "state": None, "has_results": (p / "results.json").is_file(), "verdict": None, "created": None}
            try:
                rj = read_json_file(p / "run.json") or {}
                st = read_json_file(p / "status.json") or {}
                item.update(mode=rj.get("mode"), simulated=rj.get("simulated"), created=rj.get("created"), state=st.get("state"))
                if item["has_results"]:
                    # verdict only: avoid parsing multi-MB results for the listing when a small status is enough
                    item["verdict"] = (read_json_file(p / "results.json") or {}).get("verdict")
            except ApiError:
                item["state"] = "error"
            out.append(item)
        return out

    def status(self, run_id):
        d = self.run_dir(run_id)
        f = d / "status.json"
        st = read_json_file(f)
        if st is None:
            st = {"id": run_id, "state": "idle", "message": "No status yet"}
        else:
            try:
                age = time.time() - f.stat().st_mtime
            except OSError:
                age = 0.0
            st["age_s"] = round(age, 2)
            st["stale"] = bool(st.get("state") in ACTIVE_STATES and age > 10.0)
        st["has_results"] = (d / "results.json").is_file()
        return st

    def results(self, run_id):
        d = self.run_dir(run_id)
        r = read_json_file(d / "results.json")
        if r is None:
            raise ApiError(404, "no results yet")
        return r

    def start_run(self, mode, simulate):
        with self.lock:
            if self.child is not None and self.child.poll() is None:
                raise ApiError(409, "a run is already in progress")
            if self.action.get("running"):
                raise ApiError(409, "an action is running")
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            before = {p.name for p in self.runs_dir.iterdir()}
            log = open(self.runs_dir / ".web-run.log", "ab")
            try:
                self.child = subprocess.Popen(self.argv_run(mode, simulate), cwd=self.cwd, env=self.env(), stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=subprocess.STDOUT, shell=False, close_fds=True)
            finally:
                log.close()
        new_id = None
        for _ in range(60):
            time.sleep(0.1)
            fresh = [p.name for p in self.runs_dir.iterdir() if p.name not in before and valid_id(p.name)]
            if fresh:
                new_id = sorted(fresh)[-1]
                break
            if self.child.poll() is not None:
                break
        rc = self.child.poll()
        if new_id is None and rc not in (None, 0):
            raise ApiError(500, "the run process exited with code %s; see %s" % (rc, self.runs_dir / ".web-run.log"))
        return {"started": True, "id": new_id, "pid": self.child.pid}

    def control(self, run_id, name):
        d = self.run_dir(run_id)
        ctl = d / "control"
        ctl.mkdir(exist_ok=True)
        target = ctl / name
        tmp = ctl / (".tmp-" + name)
        with open(tmp, "w"):
            pass
        os.replace(tmp, target)
        return target

    def do_continue(self, run_id, prompt_id):
        if not isinstance(prompt_id, str) or not PROMPT_RE.fullmatch(prompt_id):
            raise ApiError(400, "invalid prompt_id")
        st = read_json_file(self.run_dir(run_id) / "status.json") or {}
        cur = (st.get("prompt") or {}).get("id")
        if cur != prompt_id:
            raise ApiError(409, "that prompt is not the current one")
        self.control(run_id, "continue-" + prompt_id)
        return {"ok": True, "written": "control/continue-" + prompt_id}

    def do_abort(self, run_id):
        self.control(run_id, "abort")
        return {"ok": True, "written": "control/abort"}

    # ---- apply / keep / rollback ----
    def run_action(self, name, run_id=None, wait=20.0):
        argv = self.argv_action(name, run_id)
        with self.lock:
            if self.action.get("running"):
                raise ApiError(409, "another action is running")
            if self.child is not None and self.child.poll() is None:
                raise ApiError(409, "a measurement is running")
            proc = subprocess.Popen(argv, cwd=self.cwd, env=self.env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, shell=False, close_fds=True)
            self.action = {"running": True, "name": name, "id": run_id, "argv_display": " ".join(["autocal"] + argv[3:] if not self.mock else ["autocal"] + argv[4:]),
                           "started": time.time(), "output": "", "returncode": None}
        state = self.action

        def reader():
            with proc.stdout:
                out = proc.stdout.read()
            proc.wait()
            state["output"] = out
            state["returncode"] = proc.returncode
            state["finished"] = time.time()
            state["running"] = False
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(wait)
        return self.action_view()

    def action_view(self):
        a = dict(self.action)
        a.pop("started", None)
        a.pop("finished", None)
        return a

    def do_apply(self, run_id, body):
        if body.get("confirm") != "APPLY":
            raise ApiError(400, 'confirmation required: send {"confirm":"APPLY"}')
        d = self.run_dir(run_id)
        if not (d / "results.json").is_file():
            raise ApiError(409, "this run has no results to apply")
        return self.run_action("apply", run_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "autocal-web"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    ctl = None  # set by make_server

    def log_message(self, fmt, *args):
        if os.environ.get("AUTOCAL_WEB_QUIET") != "1":
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json", {"Cache-Control": "no-store"})

    def _error(self, code, msg):
        self._json(code, {"error": msg})

    def host_ok(self):
        ctl = self.ctl
        host = self.headers.get("Host", "")
        if not host or len(host) > 100:
            return False
        if host.startswith("["):
            end = host.find("]")
            if end < 0:
                return False
            name, rest = host[:end + 1], host[end + 1:]
        else:
            name, _, port = host.partition(":")
            rest = (":" + port) if port else ""
        if name.lower() not in ctl.allowed_hosts:
            return False
        if rest:
            if not rest.startswith(":") or rest[1:] != str(ctl.port):
                return False
        else:
            return ctl.port == 80
        return True

    def origin_ok(self, required):
        origin = self.headers.get("Origin")
        if origin is None:
            return not required
        ctl = self.ctl
        m = re.fullmatch(r"http://(\[::1\]|[A-Za-z0-9.\-]+):([0-9]{1,5})", origin)
        if not m or m.group(1).lower() not in ctl.allowed_hosts or int(m.group(2)) != ctl.port:
            return False
        return True

    def _gate(self, post):
        if not self.host_ok():
            self._error(403, "bad Host header")
            return False
        if not self.origin_ok(required=False):
            self._error(403, "bad Origin")
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        if post and sfs is not None and sfs not in ("same-origin", "none"):
            self._error(403, "cross-site request refused")
            return False
        return True

    def _path(self):
        raw = self.path
        if len(raw) > 300 or "\x00" in raw:
            raise ApiError(400, "bad path")
        return raw.split("?", 1)[0].split("#", 1)[0]

    # ---- verbs ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            if not self._gate(False):
                return
            path = self._path()
            if path.startswith("/api/"):
                return self._get_api(path)
            return self._static(path)
        except ApiError as e:
            self._error(e.code, e.msg)
        except Exception as e:   # never leak a traceback to the client
            sys.stderr.write("internal error: %r\n" % (e,))
            self._error(500, "internal error")

    def do_POST(self):
        try:
            if not self._gate(True):
                self.close_connection = True
                return
            path = self._path()
            if not path.startswith("/api/"):
                raise ApiError(404, "not found")
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self.close_connection = True
                raise ApiError(415, "Content-Type must be application/json")
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                raise ApiError(411, "Content-Length required")
            cl = self.headers.get("Content-Length")
            if cl is None or not cl.isdigit():
                self.close_connection = True
                raise ApiError(411, "Content-Length required")
            n = int(cl)
            if n > MAX_BODY:
                self.close_connection = True
                raise ApiError(413, "body too large (max %d bytes)" % MAX_BODY)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "invalid JSON")
            if not isinstance(body, dict):
                raise ApiError(400, "JSON body must be an object")
            return self._post_api(path, body)
        except ApiError as e:
            self.close_connection = True   # the body may be unread
            self._error(e.code, e.msg)
        except Exception as e:
            sys.stderr.write("internal error: %r\n" % (e,))
            self.close_connection = True
            self._error(500, "internal error")

    def _method_not_allowed(self):
        self._error(405, "method not allowed")
        self.close_connection = True

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    # ---- API ----
    def _get_api(self, path):
        ctl = self.ctl
        parts = path.strip("/").split("/")   # api, ...
        if path == "/api/runs":
            return self._json(200, {"runs": ctl.list_runs()})
        if path == "/api/info":
            return self._json(200, {"mock": ctl.mock, "runs_dir": str(ctl.runs_dir), "bind": ctl.bind, "port": ctl.port,
                                    "busy": bool(ctl.child is not None and ctl.child.poll() is None)})
        if path == "/api/action":
            return self._json(200, ctl.action_view())
        if len(parts) == 4 and parts[1] == "runs" and parts[3] in ("status", "results"):
            rid = parts[2]
            if parts[3] == "status":
                return self._json(200, ctl.status(rid))
            return self._json(200, ctl.results(rid))
        raise ApiError(404, "not found")

    def _post_api(self, path, body):
        ctl = self.ctl
        parts = path.strip("/").split("/")
        if path == "/api/runs":
            if set(body) - {"mode", "simulate"}:
                raise ApiError(400, "unknown field")
            mode = body.get("mode")
            sim = body.get("simulate", True)
            if mode not in MODES:
                raise ApiError(400, "mode must be single or five")
            if not isinstance(sim, bool):
                raise ApiError(400, "simulate must be true or false")
            return self._json(202, ctl.start_run(mode, sim))
        if path == "/api/keep":
            if body:
                raise ApiError(400, "unexpected fields")
            return self._json(200, ctl.run_action("keep"))
        if path == "/api/rollback":
            if body:
                raise ApiError(400, "unexpected fields")
            return self._json(200, ctl.run_action("rollback"))
        if len(parts) == 4 and parts[1] == "runs":
            rid, verb = parts[2], parts[3]
            if verb == "continue":
                if set(body) - {"prompt_id"}:
                    raise ApiError(400, "unknown field")
                return self._json(200, ctl.do_continue(rid, body.get("prompt_id")))
            if verb == "abort":
                if body:
                    raise ApiError(400, "unexpected fields")
                ctl.run_dir(rid)
                return self._json(200, ctl.do_abort(rid))
            if verb == "apply":
                if set(body) - {"confirm"}:
                    raise ApiError(400, "unknown field")
                return self._json(200, ctl.do_apply(rid, body))
        raise ApiError(404, "not found")

    # ---- static ----
    def _static(self, path):
        if path == "/":
            path = "/index.html"
        name = path[1:]
        if not STATIC_NAME_RE.fullmatch(name):
            raise ApiError(404, "not found")
        f = (STATIC_DIR / name)
        try:
            real = f.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            raise ApiError(404, "not found")
        if real.parent != STATIC_DIR or not real.is_file():
            raise ApiError(404, "not found")
        data = real.read_bytes()
        self._send(200, data, MIME.get(real.suffix, "application/octet-stream"), {"Cache-Control": "no-cache"})


def make_server(runs_dir, port=8765, bind="127.0.0.1", mock=False, python=None):
    extra = ()
    if bind not in ("127.0.0.1", "localhost", "::1"):
        extra = (bind,)
    ctl = Controller(runs_dir, mock=mock, python=python, port=port, bind=bind, extra_hosts=extra)
    handler = type("BoundHandler", (Handler,), {"ctl": ctl})
    ThreadingHTTPServer.daemon_threads = True
    if ":" in bind:
        ThreadingHTTPServer.address_family = __import__("socket").AF_INET6
    srv = ThreadingHTTPServer((bind, port), handler)
    ctl.port = srv.server_address[1]
    srv.ctl = ctl
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m autocal.web", description="autocal web GUI (localhost only)")
    ap.add_argument("--runs-dir", default=None, help="directory holding runs/<id>/ (default ./runs, or a temp dir with --mock)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default=None, help="address to bind (default 127.0.0.1). Anything else exposes the GUI to the network; there is no login.")
    ap.add_argument("--mock", action="store_true", help="development: seed fake runs and use the mock run/apply back end instead of autocal")
    a = ap.parse_args(argv)
    bind = a.bind or "127.0.0.1"
    if a.bind and a.bind not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("WARNING: binding to %s. The GUI has no login and can apply a room correction. Only do this on a network you trust.\n" % a.bind)
    runs = a.runs_dir or (tempfile.mkdtemp(prefix="autocal-mock-") if a.mock else "runs")
    if a.mock:
        from . import mock as mk
        os.makedirs(runs, exist_ok=True)
        if not any(valid_id(p) for p in os.listdir(runs)):
            mk.make_run(runs, "20261025-210000", "single", seed=2, scenario="pass")
            mk.make_run(runs, "20261026-091500", "five", seed=7, scenario="warn")
            mk.make_run(runs, "20261026-101000", "five", seed=11, scenario="fail")
    srv = make_server(runs, a.port, bind, a.mock)
    print("autocal web GUI on http://%s:%d/  (runs: %s%s)" % (bind if ":" not in bind else "[%s]" % bind, srv.server_address[1], srv.ctl.runs_dir, ", MOCK" if a.mock else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
