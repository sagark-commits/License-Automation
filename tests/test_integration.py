"""The real report engine, driven through the web app, against the fake database.

This is the closest thing to production that runs without a PostgreSQL server: the actual SQL
builders, peak logic, workbook writers, the verifier, background worker, HTTP layer and download.
"""
import copy
import io
import re
from pathlib import Path

import pytest

import fakedb
import report_service
from lic_web.wiring import create_app
from scenario import DEFAULTS, TENANTS, build_world
from webclient import Client, wait_for

REPO = Path(__file__).resolve().parent.parent
PW = "Test-password-123"


@pytest.fixture
def web(tmp_path, monkeypatch):
    world = build_world()
    fakedb.install(world, monkeypatch)
    app = create_app(tmp_path / "data", REPO)                       # default_runner = the real engine
    app.data.save_registry({"defaults": copy.deepcopy(DEFAULTS), "tenants": copy.deepcopy(TENANTS)}, "test")
    for arc in ("ARC-1", "ARC-2"):
        app.data.save_db(arc, {"host": "h", "port": 5432, "name": "oneproduct", "user": "u", "password": "p"}, "test")
    app.users.create("oper1", PW, "operator")
    c = Client(app)
    assert c.login("oper1", PW).status == 303
    yield app, c, world
    app.jobs.stop()


def generate(c, month="2026-09"):
    rid = c.post_csrf("/generate", {"month": month, "scope": "all"}).location.split("/")[-1]
    wait_for(lambda: c.get("/runs/%s/status.json" % rid).json()["status"] in ("done", "failed"), timeout=90)
    return rid


@pytest.fixture(autouse=True)
def allow_old_month(monkeypatch):
    import lic_web.app as appmod
    monkeypatch.setattr(appmod, "month_options", lambda n=13: ["2026-10", "2026-09", "2026-08"])


def test_report_generated_from_the_browser_is_correct_and_verifies(web):
    app, c, world = web
    rid = generate(c)
    status = c.get("/runs/%s/status.json" % rid).json()
    assert status["status"] == "done", status
    page = c.get("/runs/" + rid).text
    assert "Alpha" in page and "Beta" in page and "Gamma" in page and "Delta" in page
    # the user type nobody mapped is called out, so it cannot be missed
    assert "Ameyo-Pro" in page

    util = c.get("/runs/%s/download/utilization" % rid).raw
    login = c.get("/runs/%s/download/login" % rid).raw
    assert util[:2] == b"PK" and login[:2] == b"PK"                  # real xlsx (zip) files

    import openpyxl, verify_report
    from tmone_report import load_config
    wb = openpyxl.load_workbook(io.BytesIO(util), data_only=True)
    assert {"Dashboard", "Summary", "Alpha", "Beta", "Gamma", "Delta"} <= set(wb.sheetnames)
    lwb = openpyxl.load_workbook(io.BytesIO(login), data_only=True)
    names = set(lwb.sheetnames)
    assert {"Supervisor_Alpha", "Wallboard_Alpha", "Wallboard_Beta"} <= names           # incl. automatic sheets
    assert lwb["Wallboard_Alpha"].max_row == 2 and lwb["Wallboard_Beta"].max_row == 2   # wallboard evidence present

    out = app.data.runs_dir / rid
    ws_u, ws_l = out / report_service.output_names("2026-09")["utilization"], out / report_service.output_names("2026-09")["login"]
    findings = verify_report.verify_workbooks(ws_u, ws_l, load_config(app.data.tenants_path), month="2026-09")
    assert [f for f in findings if f["severity"] == "error"] == []


def test_a_new_tenant_added_in_the_ui_shows_up_in_the_next_report(web):
    app, c, world = web
    world.add("ARC-2", "newco_a1", "Ameyo-Pro", 99, __import__("datetime").datetime(2026, 9, 9, 9, 0), __import__("datetime").datetime(2026, 9, 9, 12, 0))
    world.add("ARC-2", "newco_a2", "Ameyo-Pro", 99, __import__("datetime").datetime(2026, 9, 9, 9, 5), __import__("datetime").datetime(2026, 9, 9, 11, 0))
    # 1. add the tenant with only the essentials; nothing says which user type is the agent type
    r = c.post_csrf("/tenants/new", {"key": "NEWCO", "project_name": "NewCo\nARCH 2", "sheet_name": "NewCo", "arc": "ARC-2", "contact_center_id": "99", "enabled": "1",
                                     "agent_mode": "default", "supervisor_mode": "default", "wallboard_mode": "default", "fetch_wallboard": "1", "query_mode": "simple"})
    assert r.status == 303
    rid = generate(c)
    res = (app.jobs.get(rid) or {})["result"]
    newco = next(t for t in res["tenants"] if t["key"] == "NEWCO")
    # 2. the report says plainly that Ameyo-Pro is not counted - it does not silently report 0
    assert newco["peaks"].get("agent") is None and "Ameyo-Pro" in newco["unclassified"]
    assert res["health"] == "warn"
    # 3. the operator sets the tenant's agent type; the next report counts it
    form = {"key": "NEWCO", "project_name": "NewCo\nARCH 2", "sheet_name": "NewCo", "arc": "ARC-2", "contact_center_id": "99", "enabled": "1",
            "agent_mode": "custom", "agent_user_types": "Ameyo-Pro", "supervisor_mode": "default", "wallboard_mode": "default", "fetch_wallboard": "1", "query_mode": "simple"}
    assert c.post_csrf("/tenants/NEWCO", form).status == 303
    rid2 = generate(c)
    newco2 = next(t for t in app.jobs.get(rid2)["result"]["tenants"] if t["key"] == "NEWCO")
    assert newco2["peaks"]["agent"]["count"] == 2 and newco2["unclassified"] == {}
    assert newco2["login_sheets"]["agent"]["sheet"] == "Ameyo-Express_NewCo" and newco2["login_sheets"]["agent"]["users"] == 2
    # and the second report knows the first one's numbers (month-over-month input)
    assert app.jobs.get(rid2)["result"]["counts"]["errors"] == 0


def test_database_outage_gives_a_clear_failure_not_a_half_report(web, monkeypatch):
    app, c, world = web
    import db_connections

    def down(args, script_dir, db_config_path=None, only_arcs=None):
        raise RuntimeError("could not connect to server: Connection timed out")
    monkeypatch.setattr(db_connections, "connect_login_databases", down)
    rid = generate(c)
    run = app.jobs.get(rid)
    assert run["status"] == "failed" and "Database connection failed" in run["error"]
    assert not list((app.data.runs_dir / rid).glob("*.xlsx"))
    assert "Connection timed out" in c.get("/runs/" + rid).text


def test_engine_log_lines_are_captured_for_the_run_page(web):
    app, c, world = web
    rid = generate(c)
    events = "\n".join(app.jobs.events(rid))
    assert "usage" in events and "login sessions" in events
