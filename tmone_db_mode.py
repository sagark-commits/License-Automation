"""Fetch TMONE usage data directly from PostgreSQL (JRXML-equivalent queries)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from db_usage_queries import month_interval, tenant_wants_wallboard, usage_query

HOUR_COLUMNS = [f"Count at {h} hour" for h in range(1, 25)]

log = logging.getLogger("tmone")


def normalize_usage_dataframe(df: pd.DataFrame, month: str | None, normalize_month) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.date
    out = out.dropna(subset=["Date"])
    for col in HOUR_COLUMNS:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    if month:
        y, m = map(int, normalize_month(month).split("-"))
        out = out[out["Date"].apply(lambda d: d.year == y and d.month == m)]
    out["Max count"] = out[HOUR_COLUMNS].max(axis=1)
    return out


def fetch_usage_from_db(conn, tenant_cfg: dict[str, Any], month: str, normalize_month, config: dict[str, Any] | None = None) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    start, end = month_interval(normalize_month(month))
    sql, params = usage_query(tenant_cfg, start, end, wallboard=False, config=config)
    usage_df = normalize_usage_dataframe(pd.read_sql(sql, conn, params=params), month, normalize_month)

    wallboard_df = None
    if tenant_wants_wallboard(tenant_cfg):
        wsql, wparams = usage_query(tenant_cfg, start, end, wallboard=True)
        wallboard_df = normalize_usage_dataframe(pd.read_sql(wsql, conn, params=wparams), month, normalize_month)

    return usage_df, wallboard_df


def apply_wallboard_peaks(td, wallboard_df: pd.DataFrame | None, config: dict[str, Any], compute_peaks, LicensePeak) -> None:
    """Wallboard peak from the wallboard usage query.

    Every row that query returns IS a wallboard user by definition (it filters on the wallboard
    user_id rule), so the rows are forced into the wallboard bucket regardless of the Ameyo
    user_type stored for that user (some tenants type their wallboard login as Supervisor).
    """
    if wallboard_df is None or wallboard_df.empty:
        return
    forced = wallboard_df.copy()
    forced["user_type"] = "Wallboard"
    wb = compute_peaks(forced, config, td.cfg)
    src = wb.get("wallboard")
    if src and src.daily_counts:
        from user_type_config import resolve_display_label

        td.peaks["wallboard"] = LicensePeak(
            license_key="wallboard",
            license_label=resolve_display_label("wallboard", config, td.cfg),
            peak_date=src.peak_date,
            peak_count=src.peak_count,
            peak_hour=src.peak_hour,
            daily_counts=dict(src.daily_counts),
        )


def build_tenant_data_from_db(
    key: str,
    cfg: dict[str, Any],
    conn,
    config: dict[str, Any],
    month: str,
    *,
    TenantData,
    compute_peaks,
    LicensePeak,
    normalize_month,
    errors: list[str] | None = None,
) -> Any | None:
    """Fetch one tenant's usage and compute its peaks.

    Returns None when the query fails (reason appended to ``errors``) or the tenant has no usage.
    """
    try:
        usage_df, wallboard_df = fetch_usage_from_db(conn, cfg, month, normalize_month, config)
    except Exception as exc:
        msg = "usage query failed: %s" % str(exc).strip().splitlines()[0]
        log.warning("%s: %s", key, msg)
        if errors is not None:
            errors.append(msg)
        return None

    if usage_df.empty and (wallboard_df is None or wallboard_df.empty):
        return None

    td = TenantData(key=key, cfg=cfg, usage_df=usage_df, wallboard_df=wallboard_df)
    td.peaks = compute_peaks(usage_df, config, cfg) if not usage_df.empty else {}
    apply_wallboard_peaks(td, wallboard_df, config, compute_peaks, LicensePeak)
    return td


def _active_contact_center_ids(config: dict[str, Any], arc: str | None) -> set[int] | None:
    active = config.get("active_contact_centers") or {}
    if not active:
        return None
    if arc:
        return {int(x) for x in active.get(arc, [])}
    allowed: set[int] = set()
    for ids in active.values():
        allowed.update(int(x) for x in ids)
    return allowed


def iter_tenants_for_db(
    config: dict[str, Any],
    tenant_keys: list[str] | None,
    arc: str | None,
    *,
    active_only: bool = False,
):
    tenants = config["tenants"]
    keys = tenant_keys if tenant_keys else list(tenants.keys())
    allowed_cc = _active_contact_center_ids(config, arc) if active_only else None

    for key in keys:
        cfg = tenants.get(key)
        if not cfg:
            log.warning("unknown tenant key %s", key)
            continue
        if arc and cfg.get("arc") != arc:
            continue
        if "contact_center_id" not in cfg:
            log.warning("skip %s: missing contact_center_id", key)
            continue
        if not tenant_keys and cfg.get("enabled", True) is False:
            continue  # disabled tenants are skipped unless named explicitly
        cc_id = int(cfg["contact_center_id"])
        if allowed_cc is not None and cc_id not in allowed_cc:
            continue
        yield key, cfg
