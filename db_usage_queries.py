"""Build TMONE usage SQL matching Ameyo JRXML report queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

HOUR_COLUMNS = [f"Count at {h} hour" for h in range(1, 25)]


def month_interval(month: str) -> tuple[datetime, datetime]:
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1)
    if m == 12:
        end = datetime(y + 1, 1, 1)
    else:
        end = datetime(y, m + 1, 1)
    return start, end


def _hourly_select_sql() -> str:
    parts = []
    for hour in range(1, 25):
        h0 = hour - 1
        parts.append(
            "count(distinct (case when mc.logout_time >= mc.login_time::date + interval "
            f"'{h0} hour' and mc.login_time <= mc.login_time::date + interval '{hour} hour' "
            f'then mc.user_id end)) as "Count at {hour} hour"'
        )
    return ",\n        ".join(parts)


def _wallboard_predicate(tenant_cfg: dict[str, Any]) -> str:
    if tenant_cfg.get("wallboard_user_id"):
        return "bc.user_id = %(wallboard_user_id)s"
    return "bc.user_id ILIKE %(wallboard_pattern)s"


def usage_query(
    tenant_cfg: dict[str, Any],
    interval_start: datetime,
    interval_end: datetime,
    *,
    wallboard: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Return SQL + params for production or wallboard usage (JRXML-equivalent)."""
    cc_id = tenant_cfg["contact_center_id"]
    campaign_ids = tenant_cfg.get("campaign_ids")
    use_simple = bool(tenant_cfg.get("use_simple_query", False))
    hourly = _hourly_select_sql()
    params: dict[str, Any] = {
        "interval_start": interval_start,
        "interval_end": interval_end,
        "cc_id": cc_id,
        "cc_id_text": str(cc_id),
    }

    if wallboard:
        params["wallboard_user_id"] = tenant_cfg.get("wallboard_user_id")
        params["wallboard_pattern"] = tenant_cfg.get("wallboard_user_pattern", "%wallboard%")
        sql = f"""
            SELECT
                mc.login_time::date AS "Date",
                bc.user_type AS user_type,
                {hourly}
            FROM user_session_history mc
            JOIN users bc ON mc.user_id = bc.user_id
            WHERE bc.contact_center_id = %(cc_id_text)s
              AND {_wallboard_predicate(tenant_cfg)}
              AND mc.login_time >= %(interval_start)s
              AND mc.login_time <= %(interval_end)s
            GROUP BY mc.login_time::date, bc.user_type
            ORDER BY mc.login_time::date
        """
        return sql.strip(), params

    if use_simple:
        sql = f"""
            SELECT
                mc.login_time::date AS "Date",
                bc.user_type AS user_type,
                {hourly}
            FROM user_session_history mc
            JOIN users bc ON mc.user_id = bc.user_id
            WHERE bc.contact_center_id = %(cc_id_text)s
              AND bc.user_id NOT ILIKE '%%wallboard%%'
              AND mc.login_time >= %(interval_start)s
              AND mc.login_time <= %(interval_end)s
            GROUP BY mc.login_time::date, bc.user_type
            ORDER BY mc.login_time::date
        """
        return sql.strip(), params

    if campaign_ids:
        params["campaign_ids"] = [str(c) for c in campaign_ids]
        sql = f"""
            SELECT
                mc.login_time::date AS "Date",
                bc.user_type AS user_type,
                {hourly}
            FROM user_session_history mc
            JOIN users bc ON mc.user_id = bc.user_id
            JOIN campaign_user_working_history c ON mc.session_id = c.session_id
            WHERE mc.user_id = bc.user_id
              AND c.campaign_id::text = ANY(%(campaign_ids)s)
              AND bc.contact_center_id = %(cc_id_text)s
              AND mc.login_time >= %(interval_start)s
              AND mc.login_time <= %(interval_end)s
            GROUP BY mc.login_time::date, bc.user_type
            ORDER BY mc.login_time::date
        """
        return sql.strip(), params

    sql = f"""
        SELECT
            mc.login_time::date AS "Date",
            bc.user_type AS user_type,
            {hourly}
        FROM user_session_history mc
        JOIN users bc ON mc.user_id = bc.user_id
        JOIN campaign_user_working_history c ON mc.session_id = c.session_id
        WHERE mc.user_id = bc.user_id
          AND mc.session_id = c.session_id
          AND c.contact_center_id = %(cc_id)s
          AND bc.contact_center_id = %(cc_id_text)s
          AND mc.login_time >= %(interval_start)s
          AND mc.login_time <= %(interval_end)s
        GROUP BY mc.login_time::date, bc.user_type
        ORDER BY mc.login_time::date
    """
    return sql.strip(), params


def tenant_wants_wallboard(tenant_cfg: dict[str, Any]) -> bool:
    return bool(tenant_cfg.get("login_sheets", {}).get("wallboard")) or bool(
        tenant_cfg.get("fetch_wallboard", False)
    )
