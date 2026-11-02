"""Production-hardening tests: one (or more) per fix from the security audit."""
import argparse
import http.client
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

import report_service
from lic_web import auth, server
from lic_web.app import safe_next
from lic_web.jobs import JobManager
from test_web import PW, FakeRunner, configure_db, env  # noqa: F401  (env fixture)
from webclient import Client, wait_for


# ---- 5. redirect allowlist
@pytest.mark.parametrize("url,ok", [
    ("/runs/20260101-000000-abcd", True), ("/tenants?x=1&y=%20", True), ("/", True),
    ("//evil.example", False), ("/a\tb", False), ("/a b", False), ("/a\\b", False), ("/x\nSet-Cookie: a=b", False),
    ("http://evil.example", False), ("javascript:alert(1)", False), ("/" + "a" * 400, False), ("", False),
])
def test_safe_next_is_a_strict_allowlist(url, ok):
    assert (safe_next(url) == url) == ok


# ---- 6. non-ASCII signatures / tokens are "invalid", never a 500
def test_unsign_and_csrf_survive_non_ascii_input(env):
    secret = b"k" * 32
    assert auth.unsign(secret, "abc.ééé") is None
    c = Client(env.app)
    c.cookies["lic_sid"] = "é.é"
    assert c.get("/generate").status == 303
    a = env.client("admin1")
    assert a.post("/users", {"action": "add", "username": "zed01", "password": PW, "role": "viewer", "_csrf": "éé"}).status == 403


# ---- 4. login throttle: bounded memory, per-IP limit, one audit line per lockout
def test_throttle_has_a_per_ip_limit_across_usernames():
    t = auth.LoginThrottle(max_failures=5, window=60, lockout=60, max_ip_failures=10)
    for i in range(10):
        t.failure("7.7.7.7", "user%d" % i)                 # a different name every time: per-name limit never trips
    assert t.blocked_for("7.7.7.7", "someone-new") > 0
    assert t.blocked_for("8.8.8.8", "someone-new") == 0


def test_throttle_maps_are_hard_capped():
    t = auth.LoginThrottle()
    t.CAP = 50
    for i in range(500):
        t.failure("9.9.%d.%d" % (i // 250, i % 250), "name%d" % i)
    assert len(t._f) <= 60 and len(t._locked) <= 60


def test_blocked_attempts_write_one_audit_line_per_lockout(env):
    c = Client(env.app, ip="5.5.5.5")
    for _ in range(5):
        c.login("admin1", "wrong-password-1")
    for _ in range(6):
        assert "Too many failed attempts" in c.login("admin1", PW).text
    text = env.data.audit_path.read_text(encoding="utf-8")
    assert text.count('"login.blocked"') == 1


def test_very_long_username_is_cut_before_throttling(env):
    c = Client(env.app, ip="4.4.4.4")
    c.login("x" * 5000, "wrong-password-1")
    assert all(len(k) < 200 for k in env.app.throttle._f)


def test_account_password_check_is_throttled(env):
    c = env.client("oper1")
    for _ in range(5):
        c.post_csrf("/account", {"current": "wrong", "new": "New-password-456", "new2": "New-password-456"})
    c.post_csrf("/account", {"current": PW, "new": "New-password-456", "new2": "New-password-456"})
    assert env.app.users.authenticate("oper1", PW)             # still the old password: the right guess was refused while locked
    assert "Too many wrong passwords" in c.get("/account").text


# ---- 3. keep_runs / run limit are admin-only
def test_operator_cannot_lower_keep_runs_but_admin_can(env):
    form = {"agent_user_types": "Professional-Agent", "supervisor_user_types": "Supervisor", "wallboard_user_types": "Wallboard",
            "ignored_user_types": "Administrator", "label_agent": "A", "label_supervisor": "S", "label_wallboard": "W",
            "keep_runs": "3", "run_timeout_min": "5"}
    env.client("oper1").post_csrf("/settings", form)
    assert env.app.jobs.keep_runs == 24 and env.app.jobs.timeout_min == 30
    env.client("admin1").post_csrf("/settings", form)
    assert env.app.jobs.keep_runs == 3 and env.app.jobs.timeout_min == 5
    assert env.data.get_settings()["run_timeout_min"] == 5


# ---- 7. health / banner / HEAD / paths
def test_healthz_exposes_only_status(env):
    r = env.client().get("/healthz")
    assert r.json() == {"status": "ok"}


def test_route_patterns_match_the_whole_path(env):
    a = env.client("admin1")
    assert a.get("/runs/20260101-000000-abcd%0A").status == 404
    assert a.get("/tenants/ALPHA%0A").status == 404


def test_viewer_gets_a_generic_message_for_failed_runs(env):
    configure_db(env)
    env.runner.fail = "Database connection failed: could not connect to server at 10.28.9.103"
    o, v = env.client("oper1"), env.client("view1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = o.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: o.get("/runs/%s/status.json" % rid).json()["status"] == "failed")
    assert "10.28.9.103" in o.get("/runs/" + rid).text
    for text in (v.get("/runs/" + rid).text, v.get("/runs/%s/status.json" % rid).text):
        assert "10.28.9.103" not in text and "Ask an operator" in text


# ---- 7. startup refusals
def _serve_args(**kw):
    base = dict(data_dir=None, host="0.0.0.0", port=0, cert=None, key=None, trust_proxy=False, secure_cookies=False, insecure_http=False, verbose=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_serve_refuses_unsafe_listen_combinations(tmp_path, capsys):
    import run_web
    assert run_web.cmd_serve(_serve_args(data_dir=str(tmp_path))) == 1
    assert "without TLS" in capsys.readouterr().out
    assert run_web.cmd_serve(_serve_args(data_dir=str(tmp_path), trust_proxy=True)) == 1
    assert "only allowed with --host 127.0.0.1" in capsys.readouterr().out


# ---- 7. users.json guard works (and CLI + server can share it)
def test_user_store_guard_is_reentrant_and_consistent(tmp_path):
    us = auth.UserStore(tmp_path / "u.json")
    us.create("alice", "Alice-pass-123", "admin")
    errs = []

    def work(i):
        try:
            us.create("user%02d" % i, "User-pass-12345", "viewer")
        except Exception as e:                              # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(us.list()) == 9                  # no lost updates


# ---- 8. resource safety
def _manager(env, runner):
    return JobManager(env.data, runner)


def test_run_that_exceeds_the_time_limit_is_failed_cleanly(env):
    configure_db(env)
    env.runner.gate = threading.Event()                      # the fake runner now loops until cancelled
    env.app.jobs.timeout_min = 0.02                          # ~1.2 s
    o = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = o.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: o.get("/runs/%s/status.json" % rid).json()["status"] == "failed", timeout=15)
    run = env.app.jobs.get(rid)
    assert "time limit" in run["error"] and run["result"] is None


def test_new_runs_are_refused_when_the_disk_is_nearly_full(env):
    configure_db(env)
    env.app.jobs.min_free_bytes = shutil.disk_usage(str(env.data.runs_dir)).free + 10 ** 9
    o = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    r = o.post_csrf("/generate", {"month": month, "scope": "all"})
    assert r.location == "/generate" and "free disk space" in o.get("/generate").text
    assert env.runner.calls == []


def test_per_run_log_is_size_capped(env):
    rid = "20260101-000000-abcd"
    (env.data.runs_dir / rid).mkdir()
    jm = env.app.jobs
    for _ in range(30000):
        jm._add_event(rid, "x" * 400)
    assert (env.data.runs_dir / rid / "events.log").stat().st_size < 1024 * 1024 + 10000
    assert len(jm.events(rid, 99999)) <= 2000


# ---- 2. Excel formula injection
def test_formula_like_text_is_neutralised_in_every_cell():
    import openpyxl
    from excel_format import neutralize_formulas
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["=HYPERLINK(\"http://evil\",\"x\")", "+1+1", "-2+3", "@SUM(1)", "\tcmd", "plain", 5, "-"])
    neutralize_formulas(wb)
    vals = [c.value for c in ws[1]]
    assert vals[:5] == ["'=HYPERLINK(\"http://evil\",\"x\")", "'+1+1", "'-2+3", "'@SUM(1)", "'\tcmd"] and vals[5:7] == ["plain", 5]
    assert all(c.data_type != "f" for c in ws[1])


def test_registry_text_cannot_start_with_a_formula_character(env):
    c = env.client("oper1")
    base = {"key": "EVIL1", "project_name": "ok", "sheet_name": "Evil1", "arc": "ARC-1", "contact_center_id": "88", "enabled": "1",
            "agent_mode": "default", "supervisor_mode": "default", "wallboard_mode": "default", "fetch_wallboard": "1", "query_mode": "auto"}
    for field, value in (("project_name", "=1+1"), ("sheet_name", "@x"), ("agent_display_label", "+evil"), ("ls_agent", "-bad")):
        r = c.post_csrf("/tenants/new", dict(base, **{field: value}))
        assert r.status == 400 and "must not start with" in r.text, field


def test_engine_output_has_no_live_formulas(tmp_path, monkeypatch):
    import copy
    import fakedb
    from scenario import TENANTS, build_world, write_config, D
    world = build_world()
    world.add("ARC-2", "=cmd|' /C calc'!A0", "=EVIL", 20, D(7, 9, 0), D(7, 10, 0))
    fakedb.install(world, monkeypatch)
    tenants = copy.deepcopy(TENANTS)
    cfg = write_config(tmp_path, tenants=tenants)
    res = report_service.run_report("2026-09", config_path=cfg, db_config_path=tmp_path / "d.yaml", output_dir=tmp_path / "out")
    import openpyxl
    for name in (res["files"]["utilization"], res["files"]["login"]):
        wb = openpyxl.load_workbook(tmp_path / "out" / name)
        bad = [(ws.title, c.coordinate) for ws in wb for row in ws.iter_rows() for c in row if c.data_type == "f"]
        assert bad == [], bad


# ---- 1. server: thread cap, no banner, TLS handshake cannot block the accept loop
def _hello(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2")])
    return [b"hi"]


def _run(srv):
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return t


def test_server_caps_concurrent_connections_and_sends_no_banner():
    srv = server.make_app_server(_hello, "127.0.0.1", 0, max_threads=2)
    _run(srv)
    port = srv.server_address[1]
    idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]      # occupy both slots, send nothing
    time.sleep(0.3)
    third = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    third.request("GET", "/")
    assert third.getresponse().status == 503
    for s in idle:
        s.close()
    time.sleep(0.5)
    ok = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    ok.request("GET", "/")
    resp = ok.getresponse()
    assert resp.status == 200 and resp.getheader("Server") is None and resp.read() == b"hi"
    srv.shutdown()
    srv.server_close()


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl CLI needed to make a test certificate")
@pytest.mark.skipif(ssl.SSLContext.__module__ != "ssl", reason="ssl.SSLContext is patched (e.g. truststore) and cannot act as a server")
def test_idle_tcp_connection_cannot_freeze_a_tls_server(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt), "-days", "1", "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    srv = server.make_app_server(_hello, "127.0.0.1", 0, str(crt), str(key), max_threads=8)
    _run(srv)
    port = srv.server_address[1]
    idle = socket.create_connection(("127.0.0.1", port))       # never starts a handshake
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=ctx)
    c.request("GET", "/")
    assert c.getresponse().read() == b"hi"                      # served although another client is stalled
    idle.close()
    srv.shutdown()
    srv.server_close()
