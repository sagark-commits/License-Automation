"""Login session SQL for the oneproduct DB (peak-hour evidence for each licence sheet)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

SESSION_COLUMNS = (
    "SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration"
)

# Matches the hourly usage overlap used by db_usage_queries (JRXML-equivalent).
PEAK_HOUR_OVERLAP = """
  AND u.logout_time >= %(peak_date)s::date + (%(hour_start)s * interval '1 hour')
  AND u.login_time <= %(peak_date)s::date + (%(hour_end)s * interval '1 hour')
"""


def _user_types_for_license(license_key, config, tenant_cfg=None):
    """Spelling variants of the configured user types, for ``lower(trim(user_type)) = ANY(...)``.

    Uses exactly the lists that classify_user_type() uses (tenant override, else defaults), so
    the sessions listed always belong to the same population that was counted.
    """
    from user_type_config import sql_type_variants, types_for_bucket

    # Executive login sheets and agent sheets both use the agent list.
    return sql_type_variants(types_for_bucket(license_key, tenant_cfg, config))


def _peak_hour_params(params: dict[str, Any], peak_hour: int) -> str:
    if peak_hour and peak_hour > 0:
        params["hour_start"] = peak_hour - 1
        params["hour_end"] = peak_hour
        return PEAK_HOUR_OVERLAP
    return ""


def supervisors_without_campaign(tenant_cfg: dict[str, Any]) -> bool:
    """True if supervisors count even when they have no campaign row (tenant owns its contact center)."""
    return bool(tenant_cfg.get("supervisors_without_campaign")) and not tenant_cfg.get("campaign_ids")


def wallboard_predicate(tenant_cfg: dict[str, Any], alias: str = "d") -> str:
    """Same wallboard-user rule the usage query uses (wallboard_user_id, else user_id pattern)."""
    if tenant_cfg.get("wallboard_user_id"):
        return f"{alias}.user_id = %(wallboard_user_id)s"
    return f"{alias}.user_id ILIKE %(wallboard_pattern)s"


def login_session_query(
    tenant_cfg,
    license_key,
    config,
    peak_date,
    peak_hour: int = 0,
):
    cc_id = int(tenant_cfg["contact_center_id"])
    campaign_ids = tenant_cfg.get("campaign_ids")
    use_simple = bool(tenant_cfg.get("use_simple_query", False))
    params: dict[str, Any] = {
        "cc_id": cc_id,
        "cc_id_text": str(cc_id),
        "peak_date": peak_date,
        "user_types": _user_types_for_license(license_key, config, tenant_cfg),
    }
    overlap = _peak_hour_params(params, peak_hour)

    if license_key == "wallboard":
        # Wallboard users normally have NO campaign_user_working_history rows, so this query must
        # not join that table (the usage query does not either).  It mirrors the usage predicate.
        params["wallboard_user_id"] = tenant_cfg.get("wallboard_user_id")
        params["wallboard_pattern"] = tenant_cfg.get("wallboard_user_pattern", "%wallboard%")
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"WHERE d.contact_center_id::text = %(cc_id_text)s "
            f"AND {wallboard_predicate(tenant_cfg)} "
            f"AND u.login_time::date = %(peak_date)s{overlap} "
            f"ORDER BY u.login_time"
        )
        return sql.strip(), params

    if use_simple:
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"WHERE d.contact_center_id = %(cc_id)s "
            f"AND u.login_time::date = %(peak_date)s "
            f"AND lower(trim(d.user_type)) = ANY(%(user_types)s){overlap} "
            f"ORDER BY u.login_time"
        )
        return sql.strip(), params

    if license_key == "supervisor" and supervisors_without_campaign(tenant_cfg):
        params["wallboard_pattern"] = tenant_cfg.get("wallboard_user_pattern", "%wallboard%")
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"WHERE d.contact_center_id::text = %(cc_id_text)s "
            f"AND lower(trim(d.user_type)) = ANY(%(user_types)s) "
            f"AND d.user_id NOT ILIKE %(wallboard_pattern)s "
            f"AND u.login_time::date = %(peak_date)s{overlap} "
            f"ORDER BY u.login_time"
        )
        return sql.strip(), params

    if campaign_ids:
        params["campaign_ids"] = [str(c) for c in campaign_ids]
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"JOIN campaign_user_working_history c ON u.session_id = c.session_id "
            f"WHERE c.campaign_id::text = ANY(%(campaign_ids)s) "
            f"AND c.contact_center_id = %(cc_id)s "
            f"AND lower(trim(d.user_type)) = ANY(%(user_types)s) "
            f"AND u.login_time::date = %(peak_date)s{overlap} "
            f"ORDER BY u.login_time"
        )
        return sql.strip(), params

    sql = (
        f"{SESSION_COLUMNS} FROM user_session_history u "
        f"JOIN users d ON u.user_id = d.user_id "
        f"JOIN campaign_user_working_history c ON u.session_id = c.session_id "
        f"WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = %(cc_id)s) "
        f"AND c.contact_center_id = %(cc_id)s "
        f"AND lower(trim(d.user_type)) = ANY(%(user_types)s) "
        f"AND u.login_time::date = %(peak_date)s{overlap} "
        f"ORDER BY u.login_time"
    )
    return sql.strip(), params


def _substitute_params(sql, params, peak_date_placeholder):
    out = sql
    for key, value in params.items():
        if key in ("user_types", "campaign_ids"):
            replacement = "ARRAY[" + ", ".join("'%s'" % str(v).replace("'", "''") for v in value) + "]"
        elif key == "peak_date":
            replacement = f"'{peak_date_placeholder}'"
        elif value is None:
            replacement = "NULL"
        elif isinstance(value, str):
            replacement = "'%s'" % value.replace("'", "''")
        else:
            replacement = str(value)
        out = out.replace(f"%({key})s", replacement)
    return out


def render_sql_for_docs(
    tenant_key,
    tenant_cfg,
    config,
    license_key,
    peak_date=":PEAK_DATE",
    peak_hour: int = 0,
):
    sample_date = datetime.strptime("2026-06-01", "%Y-%m-%d").date()
    if peak_date != ":PEAK_DATE":
        sample_date = datetime.strptime(peak_date, "%Y-%m-%d").date()
    sql, params = login_session_query(tenant_cfg, license_key, config, sample_date, peak_hour)
    rendered = _substitute_params(sql, params, peak_date)
    sheet = tenant_cfg.get("login_sheets", {}).get(license_key, license_key)
    cc = tenant_cfg.get("contact_center_id")
    hour_note = f" | peak_hour={peak_hour}" if peak_hour else ""
    return (
        f"-- Tenant: {tenant_key} | Sheet: {sheet} | contact_center_id={cc} | license={license_key}{hour_note}\n"
        f"-- Replace :PEAK_DATE with peak date from utilization report\n"
        f"{rendered};\n\n"
    )


def generate_arc_sql_files(tenants, config, output_dir=None, hosts=None):
    from pathlib import Path

    hosts = hosts or {"ARC-1": "10.28.9.103", "ARC-2": "10.28.9.110"}
    base = Path(output_dir) if output_dir else Path(__file__).resolve().parent
    for arc, filename in (("ARC-1", "login_queries_arc1.sql"), ("ARC-2", "login_queries_arc2.sql")):
        host = hosts.get(arc, "?")
        lines = [
            f"-- TMONE login count queries - {arc}",
            f"-- Database: oneproduct @ {host}",
            "-- Peak date + hour: use peak date and hour from the utilization workbook",
            "",
        ]
        for tenant_key, tenant_cfg in sorted(tenants.items()):
            if tenant_cfg.get("arc") != arc:
                continue
            lines.append(f"-- ========== {tenant_key} ==========")
            for license_key in tenant_cfg.get("login_sheets", {}):
                lines.append(render_sql_for_docs(tenant_key, tenant_cfg, config, license_key).rstrip())
            lines.append("")
        (base / filename).write_text("\n".join(lines), encoding="utf-8")
