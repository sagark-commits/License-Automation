"""Render the exact SQL the report would run for a tenant (with literal values) so it can be reviewed."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Tuple


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dt.datetime):
        return "'%s'::timestamp" % v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return "'%s'::date" % v.isoformat()
    if isinstance(v, (list, tuple)):
        return "ARRAY[%s]" % ", ".join("'%s'" % str(x).replace("'", "''") for x in v)
    return "'%s'" % str(v).replace("'", "''")


def render(sql: str, params: Dict[str, Any]) -> str:
    out = " ".join(sql.split())
    for key in sorted(params, key=len, reverse=True):
        out = out.replace("%%(%s)s" % key, _lit(params[key]))
    return out.replace("%%", "%")


def preview_for_tenant(key: str, cfg: Dict[str, Any], config: Dict[str, Any], month: str) -> List[Tuple[str, str]]:
    """[(title, sql), ...] for the usage queries and the login-evidence query of each licence."""
    from db_login_queries import login_session_query
    from db_usage_queries import month_interval, usage_query, tenant_wants_wallboard

    start, end = month_interval(month)
    out: List[Tuple[str, str]] = []
    sql, params = usage_query(cfg, start, end, wallboard=False, config=config)
    out.append(("Usage per hour - agents and supervisors (%s)" % month, render(sql, params)))
    if tenant_wants_wallboard(cfg):
        sql, params = usage_query(cfg, start, end, wallboard=True, config=config)
        out.append(("Usage per hour - wallboard", render(sql, params)))
    sample = dt.date(int(month[:4]), int(month[5:7]), 1)
    for lic in ("agent", "supervisor", "wallboard"):
        sql, params = login_session_query(cfg, lic, config, sample, 10)
        out.append(("Login sessions at the peak hour - %s (example: %s, 09:00-10:00)" % (lic, sample.isoformat()), render(sql, params)))
    return out
