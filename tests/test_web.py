"""Web UI tests: auth, roles, CSRF, tenants, settings, databases, report runs and downloads."""
import copy
import io
import json
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import report_service
from lic_web.wiring import create_app
from scenario import DEFAULTS, TENANTS
from webclient import Client, wait_for

REPO = Path(__file__).resolve().parent.parent
PW = "Test-password-123"


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.gate = None
        self.fail = None
        self.health = "pass"

    def __call__(self, month, *, arc, tenants, output_dir, progress, should_cancel, previous_summary):
        self.calls.append({"month": month, "arc": arc, "tenants": tenants, "prev": previous_summary})
        progress({"stage": "connect", "i": 0, "n": 1, "message": "Connecting"})
        while self.gate is not None and not self.gate.is_set():
            if should_cancel():
                raise report_service.RunCancelled()
            time.sleep(0.02)
        if self.fail:
            raise report_service.ReportError(self.fail)
        names = report_service.output_names(month)
        (output_dir / names["utilization"]).write_bytes(b"UTIL-XLSX")
        (output_dir / names["login"]).write_bytes(b"LOGIN-XLSX")
        (output_dir / "manifest.json").write_text("{}", encoding="utf-8")
        progress({"stage": "usage", "i": 1, "n": 1, "message": "Alpha (ARC-1): usage"})
        findings = [] if self.health == "pass" else [{"severity": "error" if self.health == "fail" else "warn", "code": "X1", "tenant": "ALPHA", "message": "something <b>odd</b>"}]
        return {"month": month, "files": {"utilization": names["utilization"], "login": names["login"]}, "health": self.health,
                "counts": {"tenants": 1, "with_usage": 1, "errors": 1 if self.health == "fail" else 0, "warnings": 1 if self.health == "warn" else 0, "login_sheets": 3},
                "tenants": [{"key": "ALPHA", "project": "Alpha", "arc": "ARC-1", "sheet": "Alpha", "status": "ok",
                             "peaks": {"agent": {"count": 5, "date": "2026-09-02", "hour": 10}}, "login_sheets": {"agent": {"sheet": "S", "rows": 5, "users": 5}},
                             "unclassified": {"Ameyo-Pro": 1}, "errors": [], "warnings": ["w"]}],
                "findings": findings}


@pytest.fixture
def env(tmp_path):
    runner = FakeRunner()
    app = create_app(tmp_path / "data", REPO, runner=runner)
    app.data.save_registry({"defaults": copy.deepcopy(DEFAULTS), "tenants": copy.deepcopy(TENANTS)}, "test")
    app.users.create("admin1", PW, "admin")
    app.users.create("oper1", PW, "operator")
    app.users.create("view1", PW, "viewer")
    ns = SimpleNamespace(app=app, runner=runner, data=app.data, tmp=tmp_path)
    ns.client = lambda user=None: _client(app, user)
    yield ns
    app.jobs.stop()


def _client(app, user=None):
    c = Client(app)
    if user:
        assert c.login(user, PW).status == 303
    return c


def configure_db(env):
    for arc in ("ARC-1", "ARC-2"):
        env.data.save_db(arc, {"host": "10.0.0.1", "port": 5432, "name": "oneproduct", "user": "ro", "password": "s3cret-db-pass"}, "test")


# ------------------------------------------------------------------ basics / security
def test_health_is_open_and_pages_carry_security_headers(env):
    r = env.client().get("/healthz")
    assert r.status == 200 and r.json()["status"] == "ok"
    h = env.client().get("/login").headers
    assert "default-src 'self'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Cache-Control"] == "no-store"


def test_everything_requires_login(env):
    c = env.client()
    for path in ("/generate", "/runs", "/tenants", "/settings", "/settings/db", "/users", "/audit", "/account"):
        r = c.get(path)
        assert r.status == 303 and r.location.startswith("/login"), path
    assert c.post("/tenants/discover", json_body={"arc": "ARC-1", "cc": 1}).status == 401   # JSON API: 401 not a redirect


def test_login_logout_and_session_cookie_flags(env):
    c = Client(env.app)
    r = c.login("admin1", PW)
    assert r.status == 303 and r.location == "/generate"
    cookie = r.set_cookies[0]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert c.get("/generate").status == 200
    assert c.post_csrf("/logout").status == 303
    assert c.get("/generate").status == 303


def test_wrong_password_gets_generic_error_and_lockout(env):
    c = Client(env.app, ip="9.9.9.9")
    for _ in range(5):
        r = c.login("admin1", "wrong-password-1")
        assert "Invalid username or password" in r.text and r.status == 200
    r = c.login("admin1", PW)                                    # correct password, but locked
    assert "Too many failed attempts" in r.text
    r2 = Client(env.app, ip="8.8.8.8").login("admin1", PW)       # other address unaffected
    assert r2.status == 303


def test_unknown_user_and_wrong_password_look_identical(env):
    a = Client(env.app).login("ghost", "whatever-12345").text
    b = Client(env.app).login("admin1", "whatever-12345").text
    assert ("Invalid username or password" in a) and ("Invalid username or password" in b)


def test_login_form_token_is_required(env):
    r = Client(env.app).post("/login", {"username": "admin1", "password": PW, "_token": "forged"})
    assert r.status == 200 and "expired" in r.text


def test_open_redirect_is_blocked(env):
    c = Client(env.app)
    page = c.get("/login?next=//evil.example/x")
    assert "//evil.example" not in page.text
    import re
    tok = re.search(r'name="_token" value="([^"]+)"', page.text).group(1)
    r = c.post("/login", {"username": "admin1", "password": PW, "_token": tok, "next": "//evil.example/x"})
    assert r.location == "/generate"


def test_post_without_csrf_token_is_rejected(env):
    c = env.client("admin1")
    assert c.post("/users", {"action": "add", "username": "zed01", "password": PW, "role": "viewer"}).status == 403
    assert c.post("/users", {"action": "add", "username": "zed01", "password": PW, "role": "viewer", "_csrf": "wrong"}).status == 403
    assert [u["username"] for u in env.app.users.list() if u["username"] == "zed01"] == []


def test_cross_origin_post_is_rejected_even_with_token(env):
    c = env.client("admin1")
    tok = c.token()
    r = c.post("/users", {"action": "add", "username": "zed01", "password": PW, "role": "viewer", "_csrf": tok}, headers={"Origin": "http://evil.example"})
    assert r.status == 403


def test_get_requests_cannot_change_anything(env):
    c = env.client("admin1")
    assert c.get("/logout").status == 405 and c.get("/tenants/ALPHA/delete").status == 405


# ------------------------------------------------------------------ roles
def test_viewer_can_look_and_download_but_not_change(env):
    v = env.client("view1")
    assert v.get("/runs").status == 200 and v.get("/tenants").status == 200
    assert v.get("/tenants/new").status == 403 and v.get("/settings").status == 403 and v.get("/users").status == 403
    assert v.post_csrf("/generate", {"month": "2026-09", "scope": "all"}).status == 403
    assert v.post_csrf("/tenants/ALPHA/toggle").status == 403
    page = v.get("/tenants/ALPHA")
    assert page.status == 200 and "Save changes" not in page.text and " disabled" in page.text


def test_operator_cannot_manage_users_databases_or_delete(env):
    o = env.client("oper1")
    assert o.get("/generate").status == 200 and o.get("/tenants/new").status == 200 and o.get("/settings").status == 200
    for path in ("/users", "/settings/db", "/audit"):
        assert o.get(path).status == 403, path
    assert o.post_csrf("/tenants/DELTA/delete").status == 403


def test_nav_only_shows_what_the_role_may_open(env):
    assert "/settings/db" not in env.client("oper1").get("/generate").text
    assert "/settings/db" in env.client("admin1").get("/generate").text


# ------------------------------------------------------------------ generate / runs / downloads
def test_generate_is_blocked_until_databases_are_configured(env):
    c = env.client("oper1")
    assert "not set up yet" in c.get("/generate").text
    r = c.post_csrf("/generate", {"month": "2026-09", "scope": "all"})
    assert r.status == 303 and r.location == "/generate"
    assert env.runner.calls == []


def test_generate_run_and_download_files(env):
    configure_db(env)
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    r = c.post_csrf("/generate", {"month": month, "scope": "all"})
    assert r.status == 303 and r.location.startswith("/runs/")
    rid = r.location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] == "done")
    page = c.get("/runs/" + rid)
    assert "All checks passed" in page.text and ("/runs/%s/download/utilization" % rid) in page.text
    util = c.get("/runs/%s/download/utilization" % rid)
    assert util.status == 200 and util.raw == b"UTIL-XLSX" and "spreadsheetml" in util.headers["Content-Type"]
    assert "attachment" in util.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(c.get("/runs/%s/download/zip" % rid).raw))
    assert len(z.namelist()) == 3
    assert env.runner.calls[0]["month"] == month and env.runner.calls[0]["tenants"] is None


def test_generate_scope_selected_and_arc(env):
    configure_db(env)
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    r = c.post_csrf("/generate", {"month": month, "scope": "selected", "tenant": ["ALPHA", "NOPE"]})
    wait_for(lambda: env.runner.calls)
    assert env.runner.calls[0]["tenants"] == ["ALPHA"]                     # unknown keys dropped
    r = c.post_csrf("/generate", {"month": month, "scope": "arc:ARC-2"})
    wait_for(lambda: len(env.runner.calls) == 2)
    assert env.runner.calls[1]["arc"] == "ARC-2"
    r = c.post_csrf("/generate", {"month": month, "scope": "selected"})
    assert r.location == "/generate" and len(env.runner.calls) == 2         # nothing ticked: refused


def test_generate_rejects_bad_months(env):
    configure_db(env)
    c = env.client("oper1")
    for bad in ("2026-13", "26-09", "../etc", "2099-01", ""):
        assert c.post_csrf("/generate", {"month": bad, "scope": "all"}).location == "/generate"
    assert env.runner.calls == []


def test_failed_run_is_shown_with_its_reason(env):
    configure_db(env)
    env.runner.fail = "Database connection failed: nope"
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] == "failed")
    assert "Database connection failed: nope" in c.get("/runs/" + rid).text


def test_failing_checks_are_loud_and_findings_are_escaped(env):
    configure_db(env)
    env.runner.health = "fail"
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] == "done")
    page = c.get("/runs/" + rid).text
    assert "Do not send this report yet" in page and "&lt;b&gt;odd&lt;/b&gt;" in page and "<b>odd</b>" not in page


def test_queued_run_can_be_cancelled_and_running_run_stops(env):
    configure_db(env)
    env.runner.gate = threading.Event()
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    first = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    second = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % first).json()["status"] == "running")
    assert c.get("/runs/%s/status.json" % second).json()["status"] == "queued"
    c.post_csrf("/runs/%s/cancel" % second)
    assert c.get("/runs/%s/status.json" % second).json()["status"] == "cancelled"
    c.post_csrf("/runs/%s/cancel" % first)
    wait_for(lambda: c.get("/runs/%s/status.json" % first).json()["status"] == "cancelled")


def test_run_ids_cannot_be_used_for_path_traversal(env):
    c = env.client("admin1")
    for rid in ("..", "../../etc/passwd", "20260101-000000-zzzz", "x"):
        assert c.get("/runs/%s" % rid).status in (404, 405)
        assert c.get("/runs/%s/download/utilization" % rid).status == 404
    assert c.get("/runs/20260101-000000-abcd/download/utilization").status == 404
    assert c.get("/runs/20260101-000000-abcd/download/passwd").status == 404


def test_delete_run_is_admin_only(env):
    configure_db(env)
    o = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = o.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: o.get("/runs/%s/status.json" % rid).json()["status"] == "done")
    assert o.post_csrf("/runs/%s/delete" % rid).status == 403
    a = env.client("admin1")
    assert a.post_csrf("/runs/%s/delete" % rid).status == 303
    assert a.get("/runs/" + rid).status == 404
    assert not (env.data.runs_dir / rid).exists()


def test_previous_month_summary_reaches_the_runner(env):
    configure_db(env)
    c = env.client("oper1")
    mo = __import__("lic_web.app", fromlist=["month_options"]).month_options()
    this, prev = mo[1], mo[2]
    r1 = c.post_csrf("/generate", {"month": prev, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % r1).json()["status"] == "done")
    c.post_csrf("/generate", {"month": this, "scope": "all"})
    wait_for(lambda: len(env.runner.calls) == 2)
    assert env.runner.calls[1]["prev"] == {"ALPHA": {"agent": 5, "supervisor": 0}}


def test_interrupted_runs_are_marked_after_a_restart(env, tmp_path):
    from lic_web.jobs import JobManager
    configure_db(env)
    env.runner.gate = threading.Event()
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] == "running")
    # a second manager reading the same data dir = what a restarted server sees
    fresh = JobManager(env.data, env.runner)
    assert fresh.get(rid)["status"] == "interrupted"
    env.runner.gate.set()


def test_old_runs_are_pruned(env):
    configure_db(env)
    env.app.jobs.keep_runs = 3
    c = env.client("oper1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    ids = []
    for _ in range(5):
        rid = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
        wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] == "done")
        ids.append(rid)
        time.sleep(1.05)                                   # run ids carry a one-second timestamp
    kept = [r["id"] for r in env.app.jobs.list()]
    assert len(kept) == 3 and ids[-1] in kept and ids[0] not in kept


# ------------------------------------------------------------------ tenants
NEW = {"key": "ACME_ARCH1", "project_name": "Acme\nARCH 1", "sheet_name": "Acme_A1", "arc": "ARC-1", "contact_center_id": "77", "enabled": "1",
       "agent_mode": "default", "supervisor_mode": "default", "wallboard_mode": "default", "fetch_wallboard": "1", "query_mode": "auto"}


def test_add_tenant_inherits_global_agent_types(env):
    c = env.client("oper1")
    r = c.post_csrf("/tenants/new", NEW)
    assert r.status == 303, r.text
    cfg = env.data.load_registry()["tenants"]["ACME_ARCH1"]
    assert cfg["contact_center_id"] == 77 and cfg["arc"] == "ARC-1" and "agent_user_types" not in cfg      # = global default
    assert cfg["project_name"] == "Acme\nARCH 1"
    page = c.get("/tenants/ACME_ARCH1").text
    assert "Use the global default (Professional-Agent, Ameyo-express, Executive)" in page


def test_add_tenant_with_its_own_agent_types(env):
    c = env.client("oper1")
    d = dict(NEW, agent_mode="custom", agent_user_types="Ameyo-Pro\nProfessional-Agent")
    assert c.post_csrf("/tenants/new", d).status == 303
    assert env.data.load_registry()["tenants"]["ACME_ARCH1"]["agent_user_types"] == ["Ameyo-Pro", "Professional-Agent"]
    # switching back to the default removes the override
    d2 = dict(NEW, agent_mode="default", agent_user_types="Ameyo-Pro")
    assert c.post_csrf("/tenants/ACME_ARCH1", d2).status == 303
    assert "agent_user_types" not in env.data.load_registry()["tenants"]["ACME_ARCH1"]


@pytest.mark.parametrize("patch,needle", [
    ({"key": "acme space"}, "Key must be"),
    ({"key": "ALPHA"}, "already exists"),
    ({"sheet_name": "Alpha"}, "already used by tenant ALPHA"),
    ({"sheet_name": "x" * 32}, "longer than 31"),
    ({"sheet_name": "bad:name"}, "contains one of"),
    ({"sheet_name": "Summary"}, "reserved"),
    ({"contact_center_id": "abc"}, "whole number"),
    ({"arc": "ARC-9"}, "ARC must be one of"),
    ({"campaign_ids": "12, x9"}, "not a number"),
    ({"campaign_ids": "5", "supervisors_without_campaign": "1"}, "cannot be used together"),
    ({"agent_mode": "custom", "agent_user_types": ""}, "left it empty"),
    ({"agent_mode": "custom", "agent_user_types": "Executive", "supervisor_mode": "custom", "supervisor_user_types": "Executive"}, "both agent and supervisor"),
    ({"agent_mode": "custom", "agent_user_types": "bad$type"}, "not a valid Ameyo user type"),
    ({"ls_agent": "Ameyo-Express_Alpha"}, "already used by ALPHA"),
    ({"contact_center_id": "10", "arc": "ARC-1"}, "already covers the whole contact center"),
])
def test_invalid_tenants_are_refused_with_a_reason(env, patch, needle):
    c = env.client("oper1")
    before = env.data.load_registry()
    r = c.post_csrf("/tenants/new", dict(NEW, **patch))
    assert r.status == 400 and needle in r.text, r.text[:2000]
    assert env.data.load_registry() == before                          # nothing was saved


def test_editing_keeps_settings_the_form_does_not_show(env):
    reg = env.data.load_registry()
    reg["tenants"]["ALPHA"]["csv_patterns"] = ["ALPHA", "alpha"]
    reg["tenants"]["ALPHA"]["display_labels"] = {"agent": "Express"}
    env.data.save_registry(reg, "test")
    c = env.client("oper1")
    form = dict(NEW, key="ALPHA", sheet_name="Alpha", project_name="Alpha\nARCH 1", contact_center_id="10", ls_agent="Ameyo-Express_Alpha")
    assert c.post_csrf("/tenants/ALPHA", form).status == 303
    cfg = env.data.load_registry()["tenants"]["ALPHA"]
    assert cfg["csv_patterns"] == ["ALPHA", "alpha"] and cfg["display_labels"] == {"agent": "Express"}


def test_toggle_disables_a_tenant_and_it_leaves_the_generate_page(env):
    c = env.client("oper1")
    assert 'value="DELTA"' in c.get("/generate").text
    c.post_csrf("/tenants/DELTA/toggle")
    assert env.data.load_registry()["tenants"]["DELTA"]["enabled"] is False
    assert 'value="DELTA"' not in c.get("/generate").text
    c.post_csrf("/tenants/DELTA/toggle")
    assert "enabled" not in env.data.load_registry()["tenants"]["DELTA"]


def test_only_admin_deletes_a_tenant_and_a_backup_exists(env):
    a = env.client("admin1")
    assert a.post_csrf("/tenants/DELTA/delete").status == 303
    assert "DELTA" not in env.data.load_registry()["tenants"]
    assert env.data.list_history()


def test_user_supplied_text_is_html_escaped(env):
    c = env.client("oper1")
    evil = '<script>alert(1)</script>'
    assert c.post_csrf("/tenants/new", dict(NEW, project_name=evil)).status == 303
    for path in ("/tenants", "/tenants/ACME_ARCH1", "/audit"):
        page = env.client("admin1").get(path).text
        assert evil not in page
    assert "&lt;script&gt;" in env.client("admin1").get("/tenants/ACME_ARCH1").text


def test_registry_save_is_atomic_and_yaml_stays_readable_by_the_engine(env):
    from tmone_report import load_config
    env.client("oper1").post_csrf("/tenants/new", NEW)
    cfg = load_config(env.data.tenants_path)
    assert "ACME_ARCH1" in cfg["tenants"] and cfg["agent_types"]


# ------------------------------------------------------------------ settings
def test_global_defaults_can_be_changed(env):
    c = env.client("oper1")
    form = {"agent_user_types": "Professional-Agent\nAmeyo-Pro", "supervisor_user_types": "Supervisor", "wallboard_user_types": "Wallboard",
            "ignored_user_types": "Administrator\nTester", "label_agent": "Agent", "label_supervisor": "Sup", "label_wallboard": "WB", "keep_runs": "10"}
    assert c.post_csrf("/settings", form).status == 303
    d = env.data.load_registry()["defaults"]
    assert d["agent_user_types"] == ["Professional-Agent", "Ameyo-Pro"] and d["ignored_user_types"] == ["Administrator", "Tester"]
    assert env.app.jobs.keep_runs == 10 and env.data.get_settings()["keep_runs"] == 10


def test_global_defaults_reject_conflicts_and_empties(env):
    c = env.client("oper1")
    before = env.data.load_registry()["defaults"]
    bad = {"agent_user_types": "Supervisor", "supervisor_user_types": "Supervisor", "wallboard_user_types": "Wallboard", "ignored_user_types": "", "label_agent": "A", "label_supervisor": "S", "label_wallboard": "W", "keep_runs": "10"}
    c.post_csrf("/settings", bad)
    assert env.data.load_registry()["defaults"] == before
    assert "listed as both" in c.get("/settings").text
    bad2 = dict(bad, agent_user_types="")
    c.post_csrf("/settings", bad2)
    assert env.data.load_registry()["defaults"] == before


def test_registry_history_restore(env):
    a = env.client("admin1")
    a.post_csrf("/tenants/DELTA/delete")
    assert "DELTA" not in env.data.load_registry()["tenants"]
    name = env.data.list_history()[0]
    assert a.post_csrf("/settings/restore", {"name": name}).status == 303
    assert "DELTA" in env.data.load_registry()["tenants"]
    assert a.post_csrf("/settings/restore", {"name": "../../etc/passwd"}).status == 303          # refused, not followed
    assert "invalid backup name" in a.get("/settings").text


# ------------------------------------------------------------------ databases
def test_db_password_is_stored_but_never_rendered(env):
    a = env.client("admin1")
    form = {"arc": "ARC-1", "host": "db1.example", "port": "5432", "name": "oneproduct", "user": "ro_user", "password": "Sup3r-Secret-DB", "sslmode": "", "timeout_s": "300"}
    assert a.post_csrf("/settings/db", form).status == 303
    page = a.get("/settings/db").text
    assert "Sup3r-Secret-DB" not in page and "db1.example" in page and "(unchanged)" in page
    # blank password keeps the stored one
    assert a.post_csrf("/settings/db", dict(form, password="", host="db2.example")).status == 303
    s = env.data.db_settings_for_connect("ARC-1")
    assert s["password"] == "Sup3r-Secret-DB" and s["host"] == "db2.example" and s["statement_timeout_ms"] == 300000
    assert "Sup3r-Secret-DB" not in env.data.audit_path.read_text(encoding="utf-8")
    import stat, os
    if os.name == "posix":
        assert stat.S_IMODE(env.data.db_config_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("patch", [{"host": "bad host!"}, {"port": "70000"}, {"user": ""}, {"name": "a b"}, {"timeout_s": "0"}])
def test_db_settings_are_validated(env, patch):
    a = env.client("admin1")
    form = {"arc": "ARC-1", "host": "db1.example", "port": "5432", "name": "oneproduct", "user": "ro", "password": "x1", "sslmode": "", "timeout_s": "300"}
    before = env.data.db_view()
    a.post_csrf("/settings/db", dict(form, **patch))
    assert env.data.db_view() == before


def test_db_connection_test_endpoint(env, monkeypatch):
    configure_db(env)
    import lic_web.discovery as disc
    monkeypatch.setattr(disc, "test_connection", lambda s, connect=None: {"ok": True, "message": "Connected in 5 ms (PostgreSQL 14)"})
    a = env.client("admin1")
    r = a.post("/settings/db/test", json_body={"arc": "ARC-2"}, csrf=a.token())
    assert r.status == 200 and r.json()["ok"] is True
    assert a.post("/settings/db/test", json_body={"arc": "../x"}, csrf=a.token()).status == 400
    assert env.client("oper1").post("/settings/db/test", json_body={"arc": "ARC-1"}, csrf="x").status == 403


# ------------------------------------------------------------------ discovery (add-tenant wizard)
class StubCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.seen.append(sql)
        s = " ".join(sql.split())
        if s.startswith("SET statement_timeout"):
            self.rows = []
        elif "FROM contact_center" in s:
            self.rows = [("Acme Corp",)]
        elif "GROUP BY d.user_type" in s:
            self.rows = [("Professional-Agent", 120, 15, "2026-10-01"), ("Ameyo-Pro", 40, 6, "2026-10-01"), ("Supervisor", 30, 3, "x"), ("Administrator", 4, 1, "x")]
        elif "information_schema" in s:
            self.rows = [("id",), ("name",)]
        elif "FROM campaign_context" in s:
            self.rows = [(5, "Welcome"), (9, "Retention")]
        elif "ILIKE" in s:
            self.rows = [("ACME_wallboard", "Wallboard")]
        else:
            raise AssertionError("unexpected SQL: " + s)

    def fetchall(self):
        return self.rows


class StubConn:
    def __init__(self):
        self.seen = []

    def cursor(self):
        return StubCursor(self)

    def close(self):
        pass


def test_discovery_endpoint_reports_user_types_and_how_they_are_counted(env, monkeypatch):
    configure_db(env)
    import db_connections
    stub = StubConn()
    monkeypatch.setattr(db_connections, "connect_database", lambda s: stub)
    c = env.client("oper1")
    r = c.post("/tenants/discover", json_body={"arc": "ARC-1", "cc": 77}, csrf=c.token())
    data = r.json()
    assert data["name"] == "Acme Corp"
    buckets = {t["type"]: t["bucket"] for t in data["user_types"]}
    assert buckets == {"Professional-Agent": "agent", "Ameyo-Pro": None, "Supervisor": "supervisor", "Administrator": "ignored"}
    assert [x["id"] for x in data["campaigns"]] == ["5", "9"] and data["wallboard_users"][0]["user_id"] == "ACME_wallboard"
    assert all("INSERT" not in s.upper() and "UPDATE" not in s.upper() and "DELETE" not in s.upper() for s in stub.seen)   # read-only
    assert c.post("/tenants/discover", json_body={"arc": "ARC-1", "cc": "77; drop table x"}, csrf=c.token()).status == 400
    assert env.client("view1").post("/tenants/discover", json_body={"arc": "ARC-1", "cc": 1}, csrf="x").status == 403


def test_discovery_connection_failure_is_reported_not_raised(env, monkeypatch):
    configure_db(env)
    import db_connections

    def boom(s):
        raise RuntimeError("could not connect to server: Connection refused\nmore detail")
    monkeypatch.setattr(db_connections, "connect_database", boom)
    c = env.client("oper1")
    r = c.post("/tenants/discover", json_body={"arc": "ARC-1", "cc": 3}, csrf=c.token())
    assert r.status == 200 and "Connection refused" in r.json()["error"] and "more detail" not in r.json()["error"]


# ------------------------------------------------------------------ users / audit / misc
def test_admin_user_management_and_last_admin_guard(env):
    a = env.client("admin1")
    assert a.post_csrf("/users", {"action": "add", "username": "carol", "password": "Carol-pass-123", "role": "viewer"}).status == 303
    assert "carol" in [u["username"] for u in env.app.users.list()]
    assert a.post_csrf("/users", {"action": "delete", "username": "admin1"}).status == 303                # cannot delete yourself
    assert "admin1" in [u["username"] for u in env.app.users.list()]
    a.post_csrf("/users", {"action": "disable", "username": "carol"})
    assert Client(env.app).login("carol", "Carol-pass-123").status == 200                                  # disabled -> refused
    assert "Password must" in a.get("/users").text or a.post_csrf("/users", {"action": "add", "username": "dave1", "password": "short", "role": "viewer"}).status == 303


def test_disabling_a_user_ends_their_session_immediately(env):
    v = env.client("view1")
    assert v.get("/generate").status == 200
    env.app.users.set_disabled("view1", True)
    assert v.get("/generate").status == 303


def test_role_change_applies_to_open_sessions(env):
    o = env.client("oper1")
    assert o.get("/tenants/new").status == 200
    env.app.users.set_role("oper1", "viewer")
    assert o.get("/tenants/new").status == 403


def test_change_own_password(env):
    c = env.client("oper1")
    assert c.post_csrf("/account", {"current": "wrong", "new": "New-password-456", "new2": "New-password-456"}).status == 303
    assert env.app.users.authenticate("oper1", PW)                                                        # unchanged
    c.post_csrf("/account", {"current": PW, "new": "New-password-456", "new2": "New-password-456"})
    assert env.app.users.authenticate("oper1", "New-password-456")


def test_audit_log_records_actions_without_secrets(env):
    configure_db(env)
    a = env.client("admin1")
    a.post_csrf("/tenants/DELTA/toggle")
    text = env.data.audit_path.read_text(encoding="utf-8")
    assert '"login.ok"' in text and '"registry.save"' in text and "s3cret-db-pass" not in text
    page = a.get("/audit").text
    assert "registry.save" in page


def test_sql_preview_shows_real_queries_without_unfilled_placeholders(env):
    page = env.client("oper1").get("/tenants/ALPHA/sql?month=2026-09").text
    assert "FROM user_session_history" in page and "campaign_user_working_history" in page
    assert "%(" not in page and "2026-09-01" in page


def test_unknown_paths_and_methods(env):
    c = env.client("admin1")
    assert c.get("/nothing-here").status == 404 and c.get("/tenants/lowercase").status == 404
    assert c.request("PUT", "/generate").status == 405


def test_static_assets_have_no_inline_script_dependencies(env):
    c = Client(env.app)
    assert c.get("/static/app.js").status == 200 and c.get("/static/app.css").status == 200
    assert c.get("/static/../../etc/passwd").status == 404
    html = c.get("/login").text
    assert "<script>" not in html and 'onclick=' not in html and 'style="' not in html.split("<main>")[0]


def test_oversized_request_bodies_are_refused(env):
    c = env.client("admin1")
    big = "a" * (1024 * 1024 + 10)
    r = c.request("POST", "/users", data={"x": big})
    assert r.status == 413


# ------------------------------------------------------------------ CSP hygiene: nothing inline may ever be needed
def test_no_page_uses_inline_styles_scripts_or_handlers(env):
    import re
    configure_db(env)
    a = env.client("admin1")
    month = __import__("lic_web.app", fromlist=["month_options"]).month_options()[1]
    rid = a.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: a.get("/runs/%s/status.json" % rid).json()["status"] == "done")
    pages = ["/generate", "/runs", "/runs/" + rid, "/tenants", "/tenants/new", "/tenants/ALPHA", "/tenants/ALPHA/sql", "/settings",
             "/settings/db", "/users", "/audit", "/account"]
    bad = []
    for path in pages:
        r = a.get(path)
        assert r.status == 200, (path, r.status)
        html = r.text
        if re.search(r"<[a-z][^>]*\sstyle\s*=", html, re.I) or re.search(r"<style", html, re.I):
            bad.append((path, "inline style"))
        if re.search(r"<script(?![^>]*\ssrc=)", html, re.I):
            bad.append((path, "inline script"))
        if re.search(r"<[a-z][^>]*\son[a-z]+\s*=", html, re.I):
            bad.append((path, "inline event handler"))
    assert bad == [], bad


def test_head_requests_work_for_health_probes(env):
    r = env.client().request("HEAD", "/healthz")
    assert r.status == 200 and r.raw == b""
    assert env.client().request("HEAD", "/generate").status == 303


def test_forwarded_for_uses_the_proxy_appended_entry(env):
    env.app.trust_proxy = True
    c = Client(env.app, ip="127.0.0.1")
    for i in range(5):
        c.request("GET", "/login")
        page = c.get("/login").text
        tok = __import__("re").search(r'name="_token" value="([^"]+)"', page).group(1)
        c.post("/login", {"username": "admin1", "password": "bad-password-1", "_token": tok}, headers={"X-Forwarded-For": "6.6.6.%d, 10.9.9.9" % i})
    assert env.app.throttle.blocked_for("10.9.9.9", "admin1") > 0          # spoofed left-hand values did not dodge the lock


def test_password_change_ends_other_sessions(env):
    a, b = env.client("oper1"), env.client("oper1")
    assert b.get("/generate").status == 200
    a.post_csrf("/account", {"current": PW, "new": "New-password-456", "new2": "New-password-456"})
    assert b.get("/generate").status == 303 and a.get("/generate").status == 200


def test_login_rejects_cross_origin_post(env):
    page = Client(env.app).get("/login").text
    tok = __import__("re").search(r'name="_token" value="([^"]+)"', page).group(1)
    r = Client(env.app).post("/login", {"username": "admin1", "password": PW, "_token": tok}, headers={"Origin": "http://evil.example"})
    assert r.status == 403
