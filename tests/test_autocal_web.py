#!/usr/bin/env python3
"""Tests for the autocal web GUI server (autocal/web). Stdlib only, no hardware, no network beyond 127.0.0.1.

Run: python3 tests/test_autocal_web.py   (uses the mock run generator and the mock `autocal` stand-in)
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock as umock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AUTOCAL_WEB_QUIET"] = "1"

from autocal.web import mock as mk          # noqa: E402
from autocal.web import server as srvmod    # noqa: E402

RID = "20261026-091500"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="autocal-web-test-")
        cls.runs = os.path.join(cls.tmp, "runs")
        os.makedirs(cls.runs)
        mk.make_run(cls.runs, RID, "five", seed=7, scenario="warn")
        mk.make_run(cls.runs, "20261025-210000", "single", seed=2, scenario="pass")
        cls.srv = srvmod.make_server(cls.runs, port=0, mock=True)
        cls.port = cls.srv.server_address[1]
        cls.ctl = cls.srv.ctl
        # fast mock runs
        cls.ctl.argv_run = lambda mode, simulate: [cls.ctl.python, "-m", "autocal.web.mock", "live", cls.runs, "--mode", mode, "--speed", "0.05"]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        child = cls.ctl.child
        if child is not None and child.poll() is None:
            child.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- helpers ----
    def req(self, method, path, body=None, headers=None, raw_body=None, host=True):
        h = {}
        if host:
            h["Host"] = "127.0.0.1:%d" % self.port
        if body is not None:
            raw_body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if raw_body is not None:
                c.putheader("Content-Length", str(len(raw_body)))
            c.endheaders(raw_body)
            r = c.getresponse()
            data = r.read()
            try:
                j = json.loads(data)
            except ValueError:
                j = None
            return r.status, j, data, r
        finally:
            c.close()

    def get(self, path, **kw):
        s, j, d, r = self.req("GET", path, **kw)
        return s, j

    def post(self, path, body=None, **kw):
        s, j, d, r = self.req("POST", path, body=({} if body is None else body), **kw)
        return s, j

    def wait_state(self, rid, states, timeout=30):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            s, j = self.get("/api/runs/%s/status" % rid)
            if s == 200:
                last = j
                if j["state"] in states:
                    return j
            time.sleep(0.05)
        self.fail("run %s never reached %s (last %s)" % (rid, states, last and last.get("state")))

    def wait_child(self):
        c = self.ctl.child
        if c is not None:
            try:
                c.wait(timeout=30)
            except Exception:
                c.kill()


class TestMockContract(unittest.TestCase):
    def test_results_shape(self):
        for mode in ("single", "five"):
            for scen in ("pass", "warn", "fail"):
                r = mk.build_results(RID, mode, 3, scen)
                n = len(r["freq"])
                self.assertEqual(r["freq"][0], 20.0)
                self.assertAlmostEqual(r["freq"][-1], 20000.0, places=0)
                self.assertEqual(r["channels"], ["FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"])
                self.assertEqual(len(r["target_db"]), n)
                npos = 5 if mode == "five" else 1
                self.assertEqual(len(r["positions"]), npos)
                for ch in r["channels"]:
                    self.assertEqual(len(r["response_db"][ch]["avg"]), n)
                    self.assertEqual(len(r["predicted_after_db"][ch]), n)
                    self.assertTrue(all(("pos%d" % i) in r["response_db"][ch] for i in range(1, npos + 1)))
                    self.assertLessEqual(len(r["ir"][ch]["h"]), 2000)
                    self.assertEqual(len(r["ir"][ch]["t_ms"]), len(r["ir"][ch]["h"]))
                    self.assertEqual(len(r["arrival_scatter"][ch]), npos)
                self.assertEqual(len(r["table"]), 8)
                self.assertIn(r["verdict"], ("PASS", "WARN", "FAIL"))
                b = r["bass"]
                self.assertEqual(len(b["freq"]), len(b["current_db"]))
                self.assertEqual(len(b["freq"]), len(b["proposed_extra_db"]))
                self.assertLess(b["rms_to_target_after_db"], b["rms_to_target_before_db"])
                json.dumps(r)

    def test_status_dict_fields(self):
        st = mk.status_dict(RID, "five", "sweeping", 2, 5, "FL sweep", 0.4, "m", level=-32.0)
        for k in ("id", "state", "mode", "position", "positions_total", "step", "progress", "message", "mic", "level_dbfs", "prompt", "simulated", "error", "updated"):
            self.assertIn(k, st)
        for k in ("peak_dbfs", "rms_dbfs", "noise_floor_dbfs"):
            self.assertIn(k, st["mic"])


class TestReadApi(Base):
    def test_list_runs(self):
        s, j = self.get("/api/runs")
        self.assertEqual(s, 200)
        ids = [r["id"] for r in j["runs"]]
        self.assertIn(RID, ids)
        row = [r for r in j["runs"] if r["id"] == RID][0]
        self.assertTrue(row["has_results"])
        self.assertEqual(row["verdict"], "WARN")

    def test_status_and_results(self):
        s, j = self.get("/api/runs/%s/status" % RID)
        self.assertEqual((s, j["state"], j["has_results"]), (200, "done", True))
        s, j = self.get("/api/runs/%s/results" % RID)
        self.assertEqual(s, 200)
        self.assertEqual(j["id"], RID)
        self.assertEqual(len(j["table"]), 8)

    def test_no_results_yet(self):
        rid = "20261026-120000"
        os.makedirs(os.path.join(self.runs, rid))
        s, j = self.get("/api/runs/%s/results" % rid)
        self.assertEqual(s, 404)
        s, j = self.get("/api/runs/%s/status" % rid)
        self.assertEqual((s, j["state"]), (200, "idle"))

    def test_stale_flag(self):
        rid = "20261026-120100"
        d = os.path.join(self.runs, rid)
        os.makedirs(d)
        mk.write_json_atomic(os.path.join(d, "status.json"), mk.status_dict(rid, "single", "sweeping", 1, 1, "FL sweep", 0.5, "x"))
        old = time.time() - 60
        os.utime(os.path.join(d, "status.json"), (old, old))
        s, j = self.get("/api/runs/%s/status" % rid)
        self.assertTrue(j["stale"])

    def test_corrupt_status_is_clean_error(self):
        rid = "20261026-120200"
        d = os.path.join(self.runs, rid)
        os.makedirs(d)
        with open(os.path.join(d, "status.json"), "w") as f:
            f.write("{not json")
        s, j = self.get("/api/runs/%s/status" % rid)
        self.assertEqual(s, 500)
        self.assertIn("error", j)
        self.assertNotIn("Traceback", json.dumps(j))

    def test_symlinked_run_dir_ignored(self):
        rid = "20261026-120300"
        os.symlink(os.path.join(self.runs, RID), os.path.join(self.runs, rid))
        s, j = self.get("/api/runs/%s/status" % rid)
        self.assertEqual(s, 404)
        self.assertNotIn(rid, [r["id"] for r in self.get("/api/runs")[1]["runs"]])

    def test_unknown_api_paths(self):
        for p in ("/api", "/api/", "/api/nothing", "/api/runs/%s" % RID, "/api/runs/%s/status/x" % RID, "/api/runs/%s/other" % RID):
            s, j = self.get(p)
            self.assertEqual(s, 404, p)


class TestIdValidation(Base):
    def test_bad_ids(self):
        bad = ["..", "2026", "20261026-09150", "20261026-0915000", "20261026_091500", "abcdefgh-ijklmn", "20261026-09150a",
               "%2e%2e", "20261026-091500%0A", "20261026-091500%00", "..%2f..%2fetc", "20261026-091500/..", "%20" + RID]
        for b in bad:
            for suffix in ("status", "results"):
                s, j = self.get("/api/runs/%s/%s" % (b, suffix))
                self.assertIn(s, (400, 404), (b, s))
                self.assertNotEqual(s, 200)
            s, j = self.post("/api/runs/%s/abort" % b, {})
            self.assertIn(s, (400, 404), b)
        s, j = self.get("/api/runs/20261026-091500/status")
        self.assertEqual(s, 200)
        # well-formed but unknown
        s, j = self.get("/api/runs/19990101-000000/status")
        self.assertEqual(s, 404)

    def test_valid_id_function(self):
        self.assertTrue(srvmod.valid_id(RID))
        self.assertFalse(srvmod.valid_id(RID + "\n"))
        self.assertFalse(srvmod.valid_id(None))
        self.assertFalse(srvmod.valid_id("2026102-6091500"))

    def test_prompt_id_validation(self):
        for pid in ("../x", "a b", "", "x" * 41, "move-2;rm", 5, None, ["move-2"]):
            s, j = self.post("/api/runs/%s/continue" % RID, {"prompt_id": pid})
            self.assertEqual(s, 400, repr(pid))
        self.assertFalse(os.path.exists(os.path.join(self.runs, RID, "control", "continue-..")))


class TestStatic(Base):
    def test_index_and_assets(self):
        for p, ct in (("/", "text/html"), ("/index.html", "text/html"), ("/app.js", "text/javascript"), ("/charts.js", "text/javascript"), ("/style.css", "text/css")):
            s, j, d, r = self.req("GET", p)
            self.assertEqual(s, 200, p)
            self.assertTrue(r.getheader("Content-Type").startswith(ct), p)
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        s, j, d, r = self.req("GET", "/")
        self.assertIn(b"autocal", d)

    def test_no_external_references(self):
        for f in (ROOT / "autocal" / "web" / "static").iterdir():
            t = f.read_text()
            for bad in ("http://", "https://", "//cdn", "<link rel=\"stylesheet\" href=\"//"):
                if f.name == "charts.js" and bad == "http://":
                    continue  # the SVG namespace URI
                self.assertNotIn(bad, t, (f.name, bad))

    def test_traversal_rejected(self):
        secret_markers = (b"root:", b"class Handler", b"def valid_id", b"ID_RE")
        paths = ["/../server.py", "/..%2fserver.py", "/%2e%2e/server.py", "/%2e%2e%2fserver.py", "/static/../server.py", "/static/index.html",
                 "//etc/passwd", "/../../../../etc/passwd", "/..\\server.py", "/%5c..%5cserver.py", "/index.html%00.js", "/index.html/", "/./index.html/../server.py",
                 "/server.py", "/__init__.py", "/mock.py", "/.", "/..", "/%2e", "/etc/passwd", "/app.js/../../server.py", "/%252e%252e/server.py",
                 "/....//server.py", "/..;/server.py", "/index.html?../../server.py#x/../", "/\\\\server.py", "/static%2f..%2fserver.py"]
        for p in paths:
            try:
                s, j, d, r = self.req("GET", p)
            except (http.client.HTTPException, OSError):
                continue
            if s == 200:
                # only the genuine index may come back for query-string tricks
                self.assertTrue(p.startswith("/index.html?"), p)
            for m in secret_markers:
                self.assertNotIn(m, d, p)

    def test_symlink_in_static_cannot_escape(self):
        link = ROOT / "autocal" / "web" / "static" / "escape-test.txt"
        try:
            os.symlink("/etc/passwd", link)
        except OSError:
            self.skipTest("cannot symlink")
        try:
            s, j, d, r = self.req("GET", "/escape-test.txt")
            self.assertEqual(s, 404)
            self.assertNotIn(b"root:", d)
        finally:
            os.unlink(link)

    def test_get_only_methods(self):
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            s, j, d, r = self.req(m, "/api/runs")
            self.assertEqual(s, 405, m)
        s, j, d, r = self.req("POST", "/index.html", body={})
        self.assertEqual(s, 404)


class TestHostOrigin(Base):
    def test_host_rejected(self):
        for host in ("evil.example", "evil.example:%d" % self.port, "127.0.0.1:1", "127.0.0.1", "localhost:%d.evil.com" % self.port, "0.0.0.0:%d" % self.port,
                     "127.0.0.1:%d, evil.com" % self.port, "[::2]:%d" % self.port, "192.168.1.5:%d" % self.port, ""):
            s, j, d, r = self.req("GET", "/api/runs", headers={"Host": host}, host=False) if host else self.req("GET", "/api/runs", host=False)
            self.assertEqual(s, 403, host)
            s, j, d, r = self.req("GET", "/", headers={"Host": host}, host=False) if host else self.req("GET", "/", host=False)
            self.assertEqual(s, 403, host)
            s, j, d, r = self.req("POST", "/api/keep", body={}, headers={"Host": host}, host=False) if host else (403, 0, 0, 0)
            self.assertEqual(s, 403, host)

    def test_host_accepted(self):
        for host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port, "LOCALHOST:%d" % self.port):
            s, j, d, r = self.req("GET", "/api/runs", headers={"Host": host}, host=False)
            self.assertEqual(s, 200, host)

    def test_origin_checked(self):
        for origin in ("http://evil.example", "https://127.0.0.1:%d" % self.port, "null", "http://127.0.0.1:1", "http://127.0.0.1:%d.evil.com" % self.port, "http://localhost.evil.com:%d" % self.port):
            s, j, d, r = self.req("GET", "/api/runs", headers={"Origin": origin})
            self.assertEqual(s, 403, origin)
            s, j, d, r = self.req("POST", "/api/rollback", body={}, headers={"Origin": origin})
            self.assertEqual(s, 403, origin)
        ok = "http://127.0.0.1:%d" % self.port
        s, j, d, r = self.req("GET", "/api/runs", headers={"Origin": ok})
        self.assertEqual(s, 200)

    def test_cross_site_fetch_metadata_refused_for_post(self):
        s, j, d, r = self.req("POST", "/api/keep", body={}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(s, 403)
        s, j, d, r = self.req("POST", "/api/keep", body={}, headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(s, 403)

    def test_binds_loopback_by_default(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_explicit_bind_adds_host(self):
        # an explicit non-loopback bind must add its address to the allowed Host names, nothing else
        c = srvmod.Controller(self.runs, port=1234, bind="192.168.9.9", extra_hosts=("192.168.9.9",))
        self.assertIn("192.168.9.9", c.allowed_hosts)
        self.assertNotIn("0.0.0.0", c.allowed_hosts)


class TestPostRules(Base):
    def test_content_type(self):
        for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "", "application/jsonx"):
            h = {"Content-Type": ct} if ct else {}
            s, j, d, r = self.req("POST", "/api/keep", raw_body=b"{}", headers=h)
            self.assertEqual(s, 415, ct)
        s, j, d, r = self.req("POST", "/api/keep", raw_body=b"{}", headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(s, 200)

    def test_body_cap(self):
        big = b'{"mode":"single","pad":"' + b"x" * (srvmod.MAX_BODY + 10) + b'"}'
        s, j, d, r = self.req("POST", "/api/runs", raw_body=big, headers={"Content-Type": "application/json"})
        self.assertEqual(s, 413)
        exact = b'{"mode":"single","simulate":true}'
        self.assertLess(len(exact), srvmod.MAX_BODY)

    def test_missing_length_and_chunked(self):
        sk = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sk.sendall(("POST /api/keep HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n\r\n" % self.port).encode())
        self.assertIn(b" 411 ", sk.recv(4096).split(b"\r\n")[0] + b" ")
        sk.close()
        sk = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sk.sendall(("POST /api/keep HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n" % self.port).encode())
        self.assertIn(b" 411 ", sk.recv(4096).split(b"\r\n")[0] + b" ")
        sk.close()

    def test_bad_json(self):
        for raw in (b"{", b"\xff\xfe", b"[]", b"1", b'"x"', b"null"):
            s, j, d, r = self.req("POST", "/api/runs", raw_body=raw, headers={"Content-Type": "application/json"})
            self.assertEqual(s, 400, raw)

    def test_field_validation(self):
        cases = [({"mode": "ten"}, 400), ({"mode": "single", "simulate": "yes"}, 400), ({"mode": "single", "simulate": 1}, 400), ({}, 400),
                 ({"mode": "single", "extra": 1}, 400), ({"mode": ["single"]}, 400), ({"mode": "single; rm -rf /"}, 400), ({"mode": "five", "runs_dir": "/etc"}, 400)]
        for body, code in cases:
            s, j = self.post("/api/runs", body)
            self.assertEqual(s, code, body)
        self.assertEqual(self.post("/api/keep", {"x": 1})[0], 400)
        self.assertEqual(self.post("/api/rollback", {"x": 1})[0], 400)
        self.assertEqual(self.post("/api/runs/%s/abort" % RID, {"x": 1})[0], 400)
        self.assertEqual(self.post("/api/runs/%s/apply" % RID, {"confirm": "APPLY", "argv": ["x"]})[0], 400)

    def test_errors_are_json(self):
        s, j, d, r = self.req("GET", "/api/zzz")
        self.assertEqual(r.getheader("Content-Type"), "application/json")
        self.assertIn("error", j)


class TestApply(Base):
    def test_requires_confirm_string(self):
        for body in ({}, {"confirm": ""}, {"confirm": "apply"}, {"confirm": "APPLY "}, {"confirm": True}, {"confirm": "yes"}, {"confirm": None}, {"confirm": ["APPLY"]}):
            s, j = self.post("/api/runs/%s/apply" % RID, body)
            self.assertEqual(s, 400, body)
            self.assertIn("confirm", j["error"])
        s, j = self.get("/api/action")
        self.assertFalse(j.get("running"))
        self.assertNotEqual(j.get("name"), "apply")

    def test_apply_with_confirm(self):
        s, j = self.post("/api/runs/%s/apply" % RID, {"confirm": "APPLY"})
        self.assertEqual(s, 200, j)
        self.assertEqual(j["name"], "apply")
        self.assertEqual(j["returncode"], 0)
        self.assertIn("trial", j["output"])
        self.assertIn(RID, j["output"])
        s, j = self.get("/api/action")
        self.assertEqual(j["returncode"], 0)

    def test_apply_unknown_or_unfinished_run(self):
        self.assertEqual(self.post("/api/runs/19990101-000000/apply", {"confirm": "APPLY"})[0], 404)
        rid = "20261026-120400"
        os.makedirs(os.path.join(self.runs, rid))
        self.assertEqual(self.post("/api/runs/%s/apply" % rid, {"confirm": "APPLY"})[0], 409)
        self.assertIn(self.post("/api/runs/../apply", {"confirm": "APPLY"})[0], (400, 404))

    def test_keep_and_rollback(self):
        s, j = self.post("/api/keep")
        self.assertEqual((s, j["returncode"]), (200, 0))
        self.assertIn("kept", j["output"])
        s, j = self.post("/api/rollback")
        self.assertEqual((s, j["returncode"]), (200, 0))
        self.assertIn("restored", j["output"])

    def test_argv_allow_list(self):
        c = self.ctl
        a = c.argv_action("apply", RID)
        self.assertTrue(all(isinstance(x, str) for x in a))
        self.assertEqual(a[-2:], ["apply", RID])
        self.assertEqual(c.argv_action("keep")[-1], "keep")
        with self.assertRaises(srvmod.ApiError):
            c.argv_action("rm")
        with self.assertRaises(srvmod.ApiError):
            c.argv_action("shell")

    def test_popen_never_uses_shell(self):
        calls = []

        class Fake:
            pid = 1
            stdout = None
            returncode = 0

            def __init__(self, argv, **kw):
                calls.append((argv, kw))
                import io
                self.stdout = io.StringIO("ok\n")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0
        real = srvmod.Controller(self.runs, mock=False, port=self.port)
        os.makedirs(os.path.join(self.runs, "20261026-120500"), exist_ok=True)
        with umock.patch.object(srvmod.subprocess, "Popen", Fake):
            real.run_action("keep")
            real.run_action("apply", RID)
            real.start_run("five", True)
            real.start_run("single", False)
        self.assertEqual(len(calls), 4)
        for argv, kw in calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kw.get("shell", False))
            self.assertEqual(argv[1:3], ["-m", "autocal"])
        self.assertEqual(calls[0][0][3:], ["keep"])
        self.assertEqual(calls[1][0][3:], ["apply", RID])
        self.assertIn("--simulate", calls[2][0])
        self.assertNotIn("--simulate", calls[3][0])
        self.assertEqual(calls[2][0][calls[2][0].index("--mode") + 1], "five")


class TestRunControl(Base):
    def test_start_continue_abort_semantics(self):
        self.wait_child()
        s, j = self.post("/api/runs", {"mode": "five", "simulate": True})
        self.assertEqual(s, 202, j)
        rid = j["id"]
        self.assertTrue(srvmod.valid_id(rid))
        # a second start is refused while the first runs
        s2, j2 = self.post("/api/runs", {"mode": "single", "simulate": True})
        self.assertEqual(s2, 409)
        st = self.wait_state(rid, ("waiting_user",))
        self.assertEqual(st["prompt"]["id"], "move-2")
        ctl_dir = os.path.join(self.runs, rid, "control")
        # wrong prompt id is refused and writes nothing
        s, j = self.post("/api/runs/%s/continue" % rid, {"prompt_id": "move-3"})
        self.assertEqual(s, 409)
        self.assertFalse(os.path.exists(os.path.join(ctl_dir, "continue-move-3")))
        # right prompt id writes exactly control/continue-move-2 (empty file)
        s, j = self.post("/api/runs/%s/continue" % rid, {"prompt_id": "move-2"})
        self.assertEqual(s, 200)
        p = os.path.join(ctl_dir, "continue-move-2")
        self.assertTrue(os.path.isfile(p))
        self.assertEqual(os.path.getsize(p), 0)
        self.assertEqual([f for f in os.listdir(ctl_dir) if f.startswith(".tmp")], [])
        end = time.time() + 20
        while time.time() < end:
            st = self.get("/api/runs/%s/status" % rid)[1]
            if st["state"] == "waiting_user" and st["prompt"]["id"] == "move-3":
                break
            time.sleep(0.05)
        self.assertEqual(st["prompt"]["id"], "move-3")
        self.assertFalse(os.path.exists(os.path.join(ctl_dir, "abort")))
        # abort writes control/abort and the (mock) core stops
        s, j = self.post("/api/runs/%s/abort" % rid, {})
        self.assertEqual(s, 200)
        self.assertTrue(os.path.isfile(os.path.join(ctl_dir, "abort")))
        st = self.wait_state(rid, ("aborted",))
        self.assertEqual(st["state"], "aborted")
        self.wait_child()

    def test_single_run_finishes_with_results(self):
        self.wait_child()
        s, j = self.post("/api/runs", {"mode": "single", "simulate": True})
        self.assertEqual(s, 202, j)
        rid = j["id"]
        st = self.wait_state(rid, ("done", "error", "aborted"), timeout=60)
        self.assertEqual(st["state"], "done")
        self.assertTrue(st["has_results"])
        s, r = self.get("/api/runs/%s/results" % rid)
        self.assertEqual((s, r["mode"]), (200, "single"))
        self.wait_child()

    def test_continue_without_prompt(self):
        s, j = self.post("/api/runs/%s/continue" % RID, {"prompt_id": "move-2"})
        self.assertEqual(s, 409)

    def test_abort_unknown_run(self):
        self.assertEqual(self.post("/api/runs/19990101-000000/abort", {})[0], 404)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1)
    if r.result.wasSuccessful():
        print("PASS autocal web (%d tests)" % r.result.testsRun)
    raise SystemExit(0 if r.result.wasSuccessful() else 1)
