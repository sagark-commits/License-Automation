"""Report generation service - one callable used by the web UI (and by scripts).

``run_report()`` does what ``run_monthly_from_db.py`` does, but:
  * reports progress through a callback instead of printing,
  * never swallows a failure: every error / warning is returned as structured data,
  * writes the workbooks atomically and re-verifies the written files (verify_report),
  * compares each tenant with the previous month when a previous summary is supplied.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

log = logging.getLogger("tmone")
REPO_DIR = Path(__file__).resolve().parent

Progress = Callable[[Dict[str, Any]], None]


class ReportError(Exception):
    """A run-level failure with a message that is safe to show to the operator."""


class RunCancelled(Exception):
    pass


def _fail_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [exc.__class__.__name__])[0]


def output_names(month: str) -> Dict[str, str]:
    return {
        "utilization": "Tmone license-Utilaztion_%s.xlsx" % month.replace("-", "_"),
        "login": "Tmone--Login Count Tmone_%s.xlsx" % datetime.strptime(month, "%Y-%m").strftime("%b_%y"),
        "manifest": "manifest.json",
    }


def _peak_dict(peak) -> Optional[Dict[str, Any]]:
    if peak is None or not getattr(peak, "peak_count", 0):
        return None
    return {
        "count": int(peak.peak_count),
        "date": peak.peak_date.isoformat() if peak.peak_date else None,
        "hour": int(peak.peak_hour or 0),
    }


def _select_tenants(config: Dict[str, Any], keys: Optional[List[str]], arc: Optional[str]) -> List[tuple]:
    from tmone_db_mode import iter_tenants_for_db

    return list(iter_tenants_for_db(config, keys or None, arc, active_only=False))


def previous_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    return "%04d-%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))


def compare_with_previous(tenants: List[Dict[str, Any]], previous: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    """Month-over-month sanity: a tenant that collapses (or appears) deserves a look before billing."""
    out: List[Dict[str, Any]] = []
    for t in tenants:
        prev = previous.get(t["key"])
        if not prev:
            continue
        for lic in ("agent", "supervisor"):
            before = int(prev.get(lic, 0) or 0)
            now = int((t["peaks"].get(lic) or {}).get("count", 0))
            if before >= 5 and now == 0:
                out.append({"severity": "warn", "code": "M01", "tenant": t["key"],
                            "message": "%s: %s peak dropped from %d to 0 compared with last month" % (t["project"], lic, before)})
            elif before >= 5 and now <= before * 0.5:
                out.append({"severity": "warn", "code": "M01", "tenant": t["key"],
                            "message": "%s: %s peak fell from %d to %d (%d%%) compared with last month"
                                       % (t["project"], lic, before, now, round(100 * (now - before) / before))})
            elif before == 0 and now >= 5:
                out.append({"severity": "info", "code": "M02", "tenant": t["key"],
                            "message": "%s: %s peak is %d (was 0 last month)" % (t["project"], lic, now)})
    return out


def run_report(
    month: str,
    *,
    config_path: Path,
    db_config_path: Path,
    output_dir: Path,
    arc: Optional[str] = None,
    tenants: Optional[List[str]] = None,
    progress: Optional[Progress] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    previous_summary: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Generate the utilization + login workbooks for ``month`` and return a JSON-able result."""
    import db_connections
    from login_export import write_login_workbook
    from login_sheets import effective_login_sheets, configured_sheet_names
    from tmone_db_mode import build_tenant_data_from_db
    from tmone_report import (
        LicensePeak, TenantData, compute_peaks, fetch_login_sessions, load_config,
        normalize_month, unclassified_types, write_utilization_workbook,
    )
    from user_type_config import type_conflicts
    import verify_report

    def emit(stage: str, i: int = 0, n: int = 0, message: str = "") -> None:
        if progress:
            progress({"stage": stage, "i": i, "n": n, "message": message})

    def check_cancel() -> None:
        if should_cancel and should_cancel():
            raise RunCancelled()

    month = normalize_month(month)
    config = load_config(Path(config_path))
    selected = _select_tenants(config, tenants, arc)
    if not selected:
        raise ReportError("No tenants selected (all disabled, or the ARC filter matched nothing).")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    names = output_names(month)

    needed_arcs = sorted({cfg.get("arc", "ARC-1") for _, cfg in selected})
    emit("connect", 0, len(needed_arcs), "Connecting to %s" % ", ".join(needed_arcs))
    ns = argparse.Namespace(db_host=None, db_port=None, db_user=None, db_password=None, db_name=None,
                            login_db_name=None, usage_db_name=None)
    try:
        pools = db_connections.connect_login_databases(ns, REPO_DIR, Path(db_config_path), needed_arcs)
    except Exception as exc:
        raise ReportError("Database connection failed: %s" % _fail_line(exc))
    conns = pools.get("login", {})

    tenants_data: List[Any] = []
    findings: List[Dict[str, Any]] = []
    try:
        total = len(selected)
        for i, (key, cfg) in enumerate(selected, start=1):
            check_cancel()
            arc_key = cfg.get("arc", "ARC-1")
            label = str(cfg.get("project_name", key)).split("\n")[0]
            emit("usage", i, total, "%s (%s): usage" % (label, arc_key))
            errs: List[str] = []
            try:
                conn = db_connections.connection_for_arc(conns, arc_key)
            except KeyError as exc:
                td = TenantData(key=key, cfg=cfg, usage_df=pd.DataFrame())
                td.errors.append("no database connection for %s" % arc_key)
                tenants_data.append(td)
                continue
            td = build_tenant_data_from_db(
                key, cfg, conn, config, month,
                TenantData=TenantData, compute_peaks=compute_peaks, LicensePeak=LicensePeak,
                normalize_month=normalize_month, errors=errs,
            )
            if td is None:
                td = TenantData(key=key, cfg=cfg, usage_df=pd.DataFrame())
                if not errs:
                    td.warnings.append("no usage recorded for %s" % month)
            td.errors.extend(errs)
            td.unclassified = unclassified_types(td.usage_df, config, cfg)
            for ut, mx in td.unclassified.items():
                td.warnings.append("user type '%s' (up to %d logged in) is not mapped to Agent/Supervisor/Wallboard and is NOT counted" % (ut, mx))
            for problem in type_conflicts(cfg, config):
                td.warnings.append("user-type setup conflict: %s" % problem)
            tenants_data.append(td)

        # login evidence at each peak hour
        taken = configured_sheet_names(config.get("tenants", {}))
        todo = [td for td in tenants_data if td.peaks and not td.errors]
        for i, td in enumerate(todo, start=1):
            check_cancel()
            emit("login", i, len(todo), "%s: login sessions" % str(td.cfg.get("project_name", td.key)).split("\n")[0])
            td.login_sheet_map = effective_login_sheets(td.cfg, td.peaks, taken, td.key)
            try:
                conn = db_connections.connection_for_arc(conns, td.cfg.get("arc", "ARC-1"))
                fetch_login_sessions(conn, td, config)
            except Exception as exc:
                td.errors.append("login sessions failed: %s" % _fail_line(exc))
    finally:
        db_connections.close_connection_pools(pools)

    check_cancel()
    emit("write", 0, 0, "Writing workbooks")
    util_path, login_path = output_dir / names["utilization"], output_dir / names["login"]
    tmp_u, tmp_l = util_path.with_name(util_path.name + ".partial"), login_path.with_name(login_path.name + ".partial")
    try:
        write_utilization_workbook(tmp_u, tenants_data, month, config)
        sheet_count = write_login_workbook(tmp_l, tenants_data, config, month)
        if not sheet_count:
            raise ReportError("The login workbook has no sheets - nothing was generated.")
        os.replace(tmp_u, util_path)
        os.replace(tmp_l, login_path)
    finally:
        for p in (tmp_u, tmp_l):
            if p.exists():
                p.unlink()

    emit("verify", 0, 0, "Verifying the written workbooks")
    try:
        findings.extend(verify_report.verify_workbooks(util_path, login_path, config, month=month))
    except Exception as exc:
        findings.append({"severity": "error", "code": "V00", "tenant": None,
                         "message": "The workbooks could not be re-read for verification: %s" % _fail_line(exc)})

    out_tenants: List[Dict[str, Any]] = []
    for td in tenants_data:
        sheets_info = {}
        for lic, sheet in (td.login_sheet_map or td.cfg.get("login_sheets") or {}).items():
            df = td.login_sessions.get(sheet)
            sheets_info[lic] = {
                "sheet": sheet,
                "rows": 0 if df is None else int(len(df)),
                "users": 0 if df is None or df.empty else int(df["user_id"].nunique()),
            }
        status = "error" if td.errors else ("ok" if td.peaks else "no_data")
        out_tenants.append({
            "key": td.key,
            "project": str(td.cfg.get("project_name", td.key)).split("\n")[0],
            "arc": td.cfg.get("arc"),
            "sheet": str(td.cfg.get("sheet_name", td.key)),
            "status": status,
            "peaks": {k: _peak_dict(td.peaks.get(k)) for k in ("agent", "supervisor", "wallboard") if _peak_dict(td.peaks.get(k))},
            "login_sheets": sheets_info,
            "unclassified": td.unclassified,
            "errors": list(td.errors),
            "warnings": list(td.warnings),
        })
        for e in td.errors:
            findings.append({"severity": "error", "code": "E01", "tenant": td.key, "message": "%s: %s" % (out_tenants[-1]["project"], e)})
        for w in td.warnings:
            findings.append({"severity": "warn", "code": "W01", "tenant": td.key, "message": "%s: %s" % (out_tenants[-1]["project"], w)})
    if previous_summary:
        findings.extend(compare_with_previous(out_tenants, previous_summary))

    errors = sum(1 for f in findings if f["severity"] == "error")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    result = {
        "month": month,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": {"utilization": names["utilization"], "login": names["login"]},
        "tenants": out_tenants,
        "findings": findings,
        "health": "fail" if errors else ("warn" if warns else "pass"),
        "counts": {
            "tenants": len(out_tenants),
            "with_usage": sum(1 for t in out_tenants if t["status"] == "ok"),
            "errors": errors,
            "warnings": warns,
            "login_sheets": sheet_count,
        },
    }
    (output_dir / names["manifest"]).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    emit("done", 1, 1, "Done")
    return result


def summary_for_comparison(result: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """{tenant_key: {agent: n, supervisor: n}} - stored per run for next month's comparison."""
    return {
        t["key"]: {lic: int((t["peaks"].get(lic) or {}).get("count", 0)) for lic in ("agent", "supervisor")}
        for t in result.get("tenants", [])
    }
