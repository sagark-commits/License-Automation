"""Login session SQL for oneproduct DB."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

SESSION_COLUMNS = (
    "SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration"
)

# Matches JRXML hourly usage overlap (same logic as license_utilization.py / assemble.py).
PEAK_HOUR_OVERLAP = """
  AND u.logout_time >= %(peak_date)s::date + (%(hour_start)s * interval '1 hour')
  AND u.login_time <= %(peak_date)s::date + (%(hour_end)s * interval '1 hour')
"""


def _user_types_for_license(license_key, config, tenant_cfg=None):
    from user_type_config import (
        resolve_agent_types,
        resolve_supervisor_types,
        resolve_wallboard_types,
    )

    # Executive is treated as agent (Professional-Agent / tenant agent label).
    if license_key in ("agent", "executive"):
        return resolve_agent_types(tenant_cfg, config)
    if license_key == "supervisor":
        return resolve_supervisor_types(tenant_cfg, config)
    return resolve_wallboard_types(tenant_cfg, config)


def _peak_hour_params(params: dict[str, Any], peak_hour: int) -> str:
    if peak_hour and peak_hour > 0:
        params["hour_start"] = peak_hour - 1
        params["hour_end"] = peak_hour
        return PEAK_HOUR_OVERLAP
    return ""


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
        "peak_date": peak_date,
        "user_types": _user_types_for_license(license_key, config, tenant_cfg),
    }
    overlap = _peak_hour_params(params, peak_hour)

    if license_key == "wallboard":
        wb = tenant_cfg.get("wallboard_user_id")
        if wb:
            params["wallboard_user_id"] = wb
            sql = (
                f"{SESSION_COLUMNS} FROM user_session_history u "
                f"WHERE u.user_id = %(wallboard_user_id)s "
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
                f"AND d.user_id ILIKE '%%wallboard%%'{overlap} "
                f"ORDER BY u.login_time"
            )
            return sql.strip(), params
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"JOIN campaign_user_working_history c ON u.session_id = c.session_id "
            f"WHERE c.contact_center_id = %(cc_id)s "
            f"AND u.login_time::date = %(peak_date)s "
            f"AND (d.user_type ILIKE '%%Wallboard%%' OR d.user_id ILIKE '%%wallboard%%'){overlap} "
            f"ORDER BY u.login_time"
        )
        return sql.strip(), params

    if use_simple:
        sql = (
            f"{SESSION_COLUMNS} FROM user_session_history u "
            f"JOIN users d ON u.user_id = d.user_id "
            f"WHERE d.contact_center_id = %(cc_id)s "
            f"AND u.login_time::date = %(peak_date)s "
            f"AND d.user_type = ANY(%(user_types)s){overlap} "
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
            f"AND d.user_type = ANY(%(user_types)s) "
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
        f"AND d.user_type = ANY(%(user_types)s) "
        f"AND u.login_time::date = %(peak_date)s{overlap} "
        f"ORDER BY u.login_time"
    )
    return sql.strip(), params


def _substitute_params(sql, params, peak_date_placeholder):
    out = sql
    for key, value in params.items():
        if key == "user_types":
            replacement = "ARRAY[" + ", ".join(f"'{v}'" for v in value) + "]"
        elif key == "campaign_ids":
            replacement = "ARRAY[" + ", ".join(f"'{v}'" for v in value) + "]"
        elif key == "peak_date":
            replacement = f"'{peak_date_placeholder}'"
        elif isinstance(value, str):
            replacement = f"'{value}'"
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


def generate_arc_sql_files(tenants, config, output_dir=None):
    from pathlib import Path

    base = Path(output_dir) if output_dir else Path(__file__).resolve().parent
    for arc, host, filename in (
        ("ARC-1", "10.28.9.103", "login_queries_arc1.sql"),
        ("ARC-2", "10.28.9.110", "login_queries_arc2.sql"),
    ):
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
