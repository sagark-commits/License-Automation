"""End-to-end engine tests against the fake database (real SQL builders, peaks and workbooks)."""
import copy
import json

import pytest

import fakedb
import report_service
from scenario import TENANTS, build_world, write_config


@pytest.fixture
def world(monkeypatch):
    w = build_world()
    fakedb.install(w, monkeypatch)
    return w


def run(tmp_path, tenants=None, **kw):
    cfg = write_config(tmp_path, tenants=tenants)
    return report_service.run_report(
        "2026-09", config_path=cfg, db_config_path=tmp_path / "db.yaml", output_dir=tmp_path / "out", **kw)


def tenant(res, key):
    return next(t for t in res["tenants"] if t["key"] == key)


def test_full_run_writes_files_and_no_errors(tmp_path, world):
    res = run(tmp_path)
    out = tmp_path / "out"
    assert (out / res["files"]["utilization"]).exists() and (out / res["files"]["login"]).exists()
    assert (out / "manifest.json").exists()
    errors = [f for f in res["findings"] if f["severity"] == "error"]
    assert errors == [], errors


def test_agent_types_sharing_a_bucket_are_summed(tmp_path, world):
    # 3 Professional-Agent + 2 Executive in the same hour -> licence peak 5 (max-of-rows would say 3)
    alpha = tenant(run(tmp_path), "ALPHA")
    assert alpha["peaks"]["agent"]["count"] == 5
    assert alpha["peaks"]["agent"]["date"] == "2026-09-02"


def test_wallboard_login_sheet_is_filled_without_campaign_rows(tmp_path, world):
    # regression: wallboard users have no campaign rows; their login sheet used to come out empty
    res = run(tmp_path)
    alpha = tenant(res, "ALPHA")
    assert alpha["peaks"]["wallboard"]["count"] == 1
    assert alpha["login_sheets"]["wallboard"]["users"] == 1
    assert tenant(res, "BETA")["login_sheets"]["wallboard"]["users"] == 1


def test_licences_without_configured_sheet_get_an_automatic_one(tmp_path, world):
    alpha = tenant(run(tmp_path), "ALPHA")
    assert alpha["login_sheets"]["supervisor"]["sheet"] == "Supervisor_Alpha"
    assert alpha["login_sheets"]["wallboard"]["sheet"] == "Wallboard_Alpha"
    assert alpha["login_sheets"]["supervisor"]["users"] == 1


def test_supervisor_without_campaign_row_is_not_counted_by_default(tmp_path, world):
    alpha = tenant(run(tmp_path), "ALPHA")
    assert alpha["peaks"]["supervisor"]["count"] == 1
    assert alpha["peaks"]["supervisor"]["date"] == "2026-09-02"   # alpha_s2 (3 Sep, no campaign row) ignored


def test_supervisor_policy_counts_campaignless_supervisors_when_enabled(tmp_path, world):
    tenants = copy.deepcopy(TENANTS)
    tenants["ALPHA"]["supervisors_without_campaign"] = True
    res = run(tmp_path, tenants=tenants)
    alpha = tenant(res, "ALPHA")
    # 3 Sep (alpha_s2) now counts as well; tie at 1 resolves to the latest date
    assert alpha["peaks"]["supervisor"]["count"] == 1
    assert alpha["peaks"]["supervisor"]["date"] == "2026-09-03"
    assert alpha["login_sheets"]["supervisor"]["users"] == 1
    assert [f for f in res["findings"] if f["severity"] == "error"] == []


def test_policy_is_ignored_for_campaign_scoped_tenants(tmp_path, world):
    tenants = copy.deepcopy(TENANTS)
    tenants["GAMMA"]["supervisors_without_campaign"] = True        # not allowed: tenant has campaign_ids
    gamma = tenant(run(tmp_path, tenants=tenants), "GAMMA")
    assert gamma["peaks"]["supervisor"]["count"] == 1


def test_campaign_scoped_tenant_only_counts_its_campaigns(tmp_path, world):
    gamma = tenant(run(tmp_path), "GAMMA")
    assert gamma["peaks"]["agent"]["count"] == 2                   # gamma_x1 belongs to campaign 9


def test_unmapped_user_type_is_flagged_not_silently_dropped(tmp_path, world):
    res = run(tmp_path)
    beta = tenant(res, "BETA")
    assert "Ameyo-Pro" in beta["unclassified"]
    assert any("Ameyo-Pro" in w for w in beta["warnings"])
    assert res["health"] == "warn"
    assert beta["peaks"]["agent"]["count"] == 4                    # the unmapped user is not counted


def test_ignored_admin_type_does_not_warn(tmp_path, world):
    assert "Administrator" not in tenant(run(tmp_path), "ALPHA")["unclassified"]


def test_tenant_agent_type_override_is_exclusive(tmp_path, world):
    tenants = copy.deepcopy(TENANTS)
    tenants["BETA"]["agent_user_types"] = ["Ameyo-Pro"]            # replaces the defaults for this tenant only
    beta = tenant(run(tmp_path, tenants=tenants), "BETA")
    assert beta["peaks"]["agent"]["count"] == 1                    # only the Ameyo-Pro user counts now
    assert "Ameyo-express" in beta["unclassified"]                 # the old default type is now flagged


def test_failed_login_query_surfaces_as_error(tmp_path, world):
    world.fail_login_for.add("campaign_user_working_history c ON u.session_id")
    res = run(tmp_path)
    assert res["health"] == "fail"
    assert any(f["severity"] == "error" and "login query" in f["message"] for f in res["findings"])


def test_empty_tenant_is_reported_not_hidden(tmp_path, world):
    delta = tenant(run(tmp_path), "DELTA")
    assert delta["status"] == "no_data"
    assert any("no usage" in w for w in delta["warnings"])


def test_month_over_month_flags_a_collapse(tmp_path, world):
    res = run(tmp_path, previous_summary={"ALPHA": {"agent": 40, "supervisor": 2}})
    assert any(f["code"] == "M01" and f["tenant"] == "ALPHA" for f in res["findings"])


def test_disabled_tenant_is_skipped(tmp_path, world):
    tenants = copy.deepcopy(TENANTS)
    tenants["DELTA"]["enabled"] = False
    res = run(tmp_path, tenants=tenants)
    assert "DELTA" not in [t["key"] for t in res["tenants"]]


def test_arc_filter(tmp_path, world):
    res = run(tmp_path, arc="ARC-2")
    assert {t["arc"] for t in res["tenants"]} == {"ARC-2"}


def test_cancel_stops_the_run_and_leaves_no_files(tmp_path, world):
    with pytest.raises(report_service.RunCancelled):
        run(tmp_path, should_cancel=lambda: True)
    assert not list((tmp_path / "out").glob("*.xlsx*"))


def test_manifest_is_valid_json(tmp_path, world):
    run(tmp_path)
    data = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert data["month"] == "2026-09" and data["tenants"]
