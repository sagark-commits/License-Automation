"""Login count report helpers: SQL export, dataframe formatting, workbook writer."""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

LOGIN_COLUMNS = ["user_id", "login_time", "logout_time", "duration"]


@contextlib.contextmanager
def _atomic_excel_path(path: Path):
    """Yields a temp path in the same directory to write the workbook to;
    only replaces `path` on success. A crash/kill mid-write must never leave
    a truncated/corrupt .xlsx sitting at the exact filename an operator would
    otherwise download and forward for billing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        yield tmp_path
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def format_duration(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, pd.Timedelta):
        td = value
    elif isinstance(value, timedelta):
        td = value
    else:
        return str(value)
    total = td.total_seconds()
    if total < 0:
        total = 0
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def prepare_login_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=LOGIN_COLUMNS)
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    rename = {}
    for c in out.columns:
        if c in ("userid", "user id"):
            rename[c] = "user_id"
        elif c in ("logintime", "login time"):
            rename[c] = "login_time"
        elif c in ("logouttime", "logout time"):
            rename[c] = "logout_time"
    out = out.rename(columns=rename)
    for col in ("login_time", "logout_time"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    if "duration" not in out.columns and {"login_time", "logout_time"}.issubset(out.columns):
        out["duration"] = out["logout_time"] - out["login_time"]
    if "duration" in out.columns:
        out["duration"] = out["duration"].map(format_duration)
    cols = [c for c in LOGIN_COLUMNS if c in out.columns]
    out = out[cols].sort_values("login_time") if "login_time" in out.columns and not out.empty else out[cols]
    return out.reset_index(drop=True)


def iter_all_login_workbook_sheets(tenants_data: list[Any], config: dict[str, Any]):
    """Yield every login sheet for processed tenants (empty sheet if no session data)."""
    order = list(config.get("tenants", {}).keys())

    def sort_key(td: Any) -> int:
        try:
            return order.index(td.key)
        except ValueError:
            return 999

    seen: set[str] = set()
    for td in sorted(tenants_data, key=sort_key):
        for license_key, sheet_name in td.cfg.get("login_sheets", {}).items():
            if sheet_name in seen:
                continue
            seen.add(sheet_name)
            df = td.login_sessions.get(sheet_name)
            if df is None:
                df = pd.DataFrame(columns=LOGIN_COLUMNS)
            yield sheet_name, prepare_login_dataframe(df), td, license_key


def export_login_sql_files(
    tenants_data: list[Any],
    config: dict[str, Any],
    output_dir: Path,
) -> pd.DataFrame:
    from db_login_queries import render_sql_for_docs

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for td in tenants_data:
        arc = td.cfg.get("arc", "ARC-1")
        host = "10.28.9.103" if arc == "ARC-1" else "10.28.9.110"
        for license_key, sheet_name in td.cfg.get("login_sheets", {}).items():
            peak = td.peaks.get(license_key)
            peak_date = peak.peak_date.isoformat() if peak and peak.peak_date else None
            peak_count = peak.peak_count if peak else 0
            peak_hour = peak.peak_hour if peak else 0
            sql_text = render_sql_for_docs(
                td.key,
                td.cfg,
                config,
                license_key,
                peak_date if peak_date else ":PEAK_DATE",
                peak_hour,
            )
            safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in sheet_name).strip()
            sql_path = output_dir / f"{safe_name}.sql"
            header = (
                f"-- Database: oneproduct @ {host}\n"
                f"-- Peak date: {peak_date or 'N/A'} | Peak hour: {peak_hour or 'N/A'} | Peak count: {peak_count}\n"
                f"-- Export result to CSV and save as: {sheet_name}.csv\n\n"
            )
            sql_path.write_text(header + sql_text, encoding="utf-8")
            manifest_rows.append(
                {
                    "Sheet Name": sheet_name,
                    "Tenant": td.key,
                    "ARC": arc,
                    "License": license_key,
                    "Peak Date": peak_date or "",
                    "Peak Hour": peak_hour or "",
                    "Peak Count": peak_count,
                    "DB Host": host,
                    "SQL File": sql_path.name,
                    "Expected CSV": f"{sheet_name}.csv",
                }
            )
    manifest = pd.DataFrame(manifest_rows)
    if not manifest.empty:
        manifest.to_csv(output_dir / "login_manifest.csv", index=False)
    return manifest


def write_login_workbook(path: Path, tenants_data: list[Any], config: dict[str, Any], month: str) -> int:
    from excel_format import format_login_workbook

    sheets = list(iter_all_login_workbook_sheets(tenants_data, config))
    if not sheets:
        return 0
    with _atomic_excel_path(path) as tmp_path:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            for sheet_name, df, td, license_key in sheets:
                df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            format_login_workbook(writer.book, month)
    return len(sheets)


def _sql_query_from_file(sql_path: Path) -> str:
    lines = []
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    sql = "\n".join(lines).strip()
    if sql.endswith(";"):
        sql = sql[:-1]
    return sql