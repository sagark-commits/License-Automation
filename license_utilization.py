#!/usr/bin/env python3
"""Monthly Ameyo license utilization reporting from CSV exports."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import yaml

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore

HOUR_COLUMNS = [f"Count at {h} hour" for h in range(1, 25)]


@dataclass
class DayPeak:
    peak_date: date
    user_type: str
    peak_count: int
    peak_hour: int


@dataclass
class MonthlyPeak:
    tenant_name: str
    license_category: str
    peak_date: date
    peak_count: int
    peak_hour: int
    source_folder: str
    source_file: str
    db_count: Optional[int] = None
    db_match: Optional[bool] = None


@dataclass
class TenantProcessResult:
    tenant_name: str
    folder: str
    monthly_peaks: List[MonthlyPeak] = field(default_factory=list)
    daily_peaks: List[Dict[str, Any]] = field(default_factory=list)
    session_details: List[Dict[str, Any]] = field(default_factory=list)
    skipped_reason: Optional[str] = None


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    defaults = data.get("defaults", {})
    return {
        "agent_user_types": list(defaults.get("agent_user_types", [])),
        "supervisor_user_types": list(defaults.get("supervisor_user_types", [])),
        "wallboard_filename_patterns": list(
            defaults.get("wallboard_filename_patterns", ["wallboard", "Wallboard"])
        ),
        "tenants": data.get("tenants", {}) or {},
    }


def is_wallboard_file(filename: str, patterns: Sequence[str]) -> bool:
    lower = filename.lower()
    return any(p.lower() in lower for p in patterns)


def match_tenant(path: Path, tenants: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    haystack = f"{path.parent.name}/{path.name}".lower()
    best: Optional[Tuple[str, Dict[str, Any], int]] = None
    for name, cfg in tenants.items():
        pattern = str(cfg.get("report_pattern", name)).lower()
        if pattern and pattern in haystack:
            score = len(pattern)
            if best is None or score > best[2]:
                best = (name, cfg, score)
    if best:
        return best[0], best[1]
    folder_lower = path.parent.name.lower()
    for name, cfg in tenants.items():
        if name.lower() in folder_lower:
            return name, cfg
    return None


def read_usage_csv(csv_path: Path, month: Optional[str]) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "Date" not in df.columns or "user_type" not in df.columns:
        raise ValueError(f"Missing required columns in {csv_path}")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Date"])
    for col in HOUR_COLUMNS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    if month:
        year_s, month_s = month.split("-")
        year_i, month_i = int(year_s), int(month_s)
        dt = pd.to_datetime(df["Date"])
        df = df[(dt.dt.year == year_i) & (dt.dt.month == month_i)]
    return df


def day_peak(row: pd.Series) -> Tuple[int, int]:
    counts = [int(row[col]) for col in HOUR_COLUMNS]
    peak_count = max(counts) if counts else 0
    peak_hour = counts.index(peak_count) + 1 if counts else 0
    return peak_count, peak_hour


def find_peaks_for_type(
    df: pd.DataFrame,
    user_types: Sequence[str],
    aggregate: bool = True,
) -> Tuple[Optional[date], int, int, List[DayPeak]]:
    subset = df[df["user_type"].isin(user_types)].copy()
    if subset.empty:
        return None, 0, 0, []

    per_row: List[DayPeak] = []
    for _, row in subset.iterrows():
        peak_count, peak_hour = day_peak(row)
        per_row.append(
            DayPeak(
                peak_date=row["Date"],
                user_type=str(row["user_type"]),
                peak_count=peak_count,
                peak_hour=peak_hour,
            )
        )

    if aggregate:
        daily_best: Dict[date, Tuple[int, int]] = {}
        for peak_date in sorted({p.peak_date for p in per_row}):
            day_rows = subset[subset["Date"] == peak_date]
            hourly_totals = [0] * 24
            for _, row in day_rows.iterrows():
                for i, col in enumerate(HOUR_COLUMNS):
                    hourly_totals[i] += int(row[col])
            peak_count = max(hourly_totals) if hourly_totals else 0
            peak_hour = hourly_totals.index(peak_count) + 1 if hourly_totals else 0
            daily_best[peak_date] = (peak_count, peak_hour)
        best_date = max(daily_best, key=lambda d: daily_best[d][0])
        peak_count, peak_hour = daily_best[best_date]
        return best_date, peak_count, peak_hour, per_row

    best = max(per_row, key=lambda p: p.peak_count)
    return best.peak_date, best.peak_count, best.peak_hour, per_row


def _hour_window(peak_date: date, peak_hour: int) -> Tuple[datetime, datetime]:
    start = datetime.combine(peak_date, datetime.min.time()) + timedelta(hours=peak_hour - 1)
    end = start + timedelta(hours=1)
    return start, end


def build_verification_query(
    tenant_cfg: Dict[str, Any],
    license_category: str,
    agent_user_types: Sequence[str],
    supervisor_user_types: Sequence[str],
    peak_date: date,
    peak_hour: int,
) -> Tuple[str, Dict[str, Any]]:
    cc_id = tenant_cfg["contact_center_id"]
    campaign_ids = tenant_cfg.get("campaign_ids") or []
    arc = str(tenant_cfg.get("arc", "ARC-1")).upper()
    use_simple = bool(tenant_cfg.get("use_simple_query", False))

    if license_category.lower() == "agent":
        user_types = list(agent_user_types)
    else:
        user_types = list(supervisor_user_types)

    start_dt, end_dt = _hour_window(peak_date, peak_hour)
    params: Dict[str, Any] = {
        "cc_id": cc_id,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "user_types": list(user_types),
    }

    base_where = """
        WHERE ush.contact_center_id = %(cc_id)s
          AND ush.session_start_time >= %(start_dt)s
          AND ush.session_start_time < %(end_dt)s
          AND ua.user_type = ANY(%(user_types)s)
    """

    if arc == "ARC-2" or use_simple:
        sql = f"""
            SELECT COUNT(DISTINCT ush.user_id) AS session_count
            FROM user_session_history ush
            INNER JOIN user_account ua ON ua.user_id = ush.user_id
            {base_where}
        """
        return sql.strip(), params

    if campaign_ids:
        params["campaign_ids"] = [str(c) for c in campaign_ids]
        sql = f"""
            SELECT COUNT(DISTINCT ush.user_id) AS session_count
            FROM user_session_history ush
            INNER JOIN user_account ua ON ua.user_id = ush.user_id
            {base_where}
              AND ush.campaign_id::text = ANY(%(campaign_ids)s)
        """
    else:
        sql = f"""
            SELECT COUNT(DISTINCT ush.user_id) AS session_count
            FROM user_session_history ush
            INNER JOIN user_account ua ON ua.user_id = ush.user_id
            {base_where}
        """
    return sql.strip(), params


def build_session_detail_query(
    tenant_cfg: Dict[str, Any],
    license_category: str,
    agent_user_types: Sequence[str],
    supervisor_user_types: Sequence[str],
    peak_date: date,
    peak_hour: int,
) -> Tuple[str, Dict[str, Any]]:
    count_sql, params = build_verification_query(
        tenant_cfg,
        license_category,
        agent_user_types,
        supervisor_user_types,
        peak_date,
        peak_hour,
    )
    detail_sql = count_sql.replace(
        "SELECT COUNT(DISTINCT ush.user_id) AS session_count",
        "SELECT ush.user_id, ua.user_type, ush.session_start_time, "
        "ush.session_end_time, ush.campaign_id",
    )
    detail_sql += " ORDER BY ush.user_id, ush.session_start_time"
    return detail_sql, params


def verify_db_count(conn, sql: str, params: Dict[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return 0
        return int(row[0])


def fetch_session_details(
    conn,
    tenant_cfg: Dict[str, Any],
    license_category: str,
    agent_user_types: Sequence[str],
    supervisor_user_types: Sequence[str],
    peak_date: date,
    peak_hour: int,
    tenant_name: str,
) -> List[Dict[str, Any]]:
    detail_sql, params = build_session_detail_query(
        tenant_cfg,
        license_category,
        agent_user_types,
        supervisor_user_types,
        peak_date,
        peak_hour,
    )

    rows: List[Dict[str, Any]] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(detail_sql, params)
        for rec in cur.fetchall():
            item = dict(rec)
            item["tenant"] = tenant_name
            item["license_category"] = license_category
            item["peak_date"] = peak_date.isoformat()
            item["peak_hour"] = peak_hour
            rows.append(item)
    return rows

﻿
def discover_csv_groups(input_dir: Path, wallboard_patterns: Sequence[str]) -> Dict[str, List[Path]]:
    groups: Dict[str, List[Path]] = {}
    for csv_path in sorted(input_dir.rglob("*.csv")):
        if is_wallboard_file(csv_path.name, wallboard_patterns):
            continue
        key = str(csv_path.parent.resolve())
        groups.setdefault(key, []).append(csv_path)
    return groups


def process_tenant(
    folder: Path,
    csv_files: List[Path],
    config: Dict[str, Any],
    month: Optional[str],
    verify_db: bool,
    db_conn=None,
) -> TenantProcessResult:
    tenants = config["tenants"]
    agent_types = config["agent_user_types"]
    supervisor_types = config["supervisor_user_types"]

    if not csv_files:
        return TenantProcessResult(tenant_name=folder.name, folder=str(folder), skipped_reason="wallboard-only")

    representative = csv_files[0]
    matched = match_tenant(representative, tenants)
    tenant_name = matched[0] if matched else folder.name
    tenant_cfg = matched[1] if matched else {}

    frames = []
    for csv_path in csv_files:
        try:
            frames.append(read_usage_csv(csv_path, month))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: skipping {csv_path}: {exc}", file=sys.stderr)
    if not frames:
        return TenantProcessResult(tenant_name=tenant_name, folder=str(folder), skipped_reason="no-data")

    df = pd.concat(frames, ignore_index=True)
    result = TenantProcessResult(tenant_name=tenant_name, folder=str(folder))

    categories = [
        ("Agent", agent_types),
        ("Supervisor", supervisor_types),
    ]
    for category, user_types in categories:
        peak_date, peak_count, peak_hour, per_row = find_peaks_for_type(df, user_types, aggregate=True)
        if peak_date is None:
            continue
        monthly = MonthlyPeak(
            tenant_name=tenant_name,
            license_category=category,
            peak_date=peak_date,
            peak_count=peak_count,
            peak_hour=peak_hour,
            source_folder=str(folder),
            source_file=representative.name,
        )
        if verify_db and db_conn is not None and tenant_cfg:
            sql, params = build_verification_query(
                tenant_cfg,
                category,
                agent_types,
                supervisor_types,
                peak_date,
                peak_hour,
            )
            try:
                db_count = verify_db_count(db_conn, sql, params)
                monthly.db_count = db_count
                monthly.db_match = db_count == peak_count
                result.session_details.extend(
                    fetch_session_details(
                        db_conn,
                        tenant_cfg,
                        category,
                        agent_types,
                        supervisor_types,
                        peak_date,
                        peak_hour,
                        tenant_name,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: DB verify failed for {tenant_name} {category}: {exc}", file=sys.stderr)
        result.monthly_peaks.append(monthly)

        for dp in per_row:
            if dp.user_type in user_types:
                result.daily_peaks.append(
                    {
                        "tenant": tenant_name,
                        "license_category": category,
                        "date": dp.peak_date.isoformat(),
                        "user_type": dp.user_type,
                        "peak_count": dp.peak_count,
                        "peak_hour": dp.peak_hour,
                        "source_file": representative.name,
                    }
                )

    return result


def summaries_to_dataframe(results: Iterable[TenantProcessResult]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for res in results:
        for peak in res.monthly_peaks:
            rows.append(
                {
                    "Tenant": peak.tenant_name,
                    "License Category": peak.license_category,
                    "Peak Date": peak.peak_date.isoformat(),
                    "Peak Count": peak.peak_count,
                    "Peak Hour": peak.peak_hour,
                    "CSV Peak Count": peak.peak_count,
                    "DB Count": peak.db_count,
                    "DB Match": peak.db_match,
                    "Source Folder": peak.source_folder,
                    "Source File": peak.source_file,
                }
            )
    columns = [
        "Tenant",
        "License Category",
        "Peak Date",
        "Peak Count",
        "Peak Hour",
        "CSV Peak Count",
        "DB Count",
        "DB Match",
        "Source Folder",
        "Source File",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)


def build_daily_peaks_report(results: Iterable[TenantProcessResult]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for res in results:
        rows.extend(res.daily_peaks)
    columns = ["tenant", "license_category", "date", "user_type", "peak_count", "peak_hour", "source_file"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)


def write_excel(
    output_path: Path,
    summary_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    session_df: pd.DataFrame,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        daily_df.to_excel(writer, sheet_name="Daily Peaks", index=False)
        session_df.to_excel(writer, sheet_name="DB Session Details", index=False)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Ameyo monthly license utilization reporting")
    parser.add_argument("--input", "-i", required=True, type=Path, help="Root folder containing tenant CSV exports")
    parser.add_argument("--output", "-o", type=Path, help="Output Excel path")
    parser.add_argument("--month", "-m", help="Filter month as YYYY-MM")
    parser.add_argument("--config", "-c", type=Path, default=script_dir / "tenants.yaml", help="Tenant YAML config")
    parser.add_argument("--verify-db", action="store_true", help="Verify peaks against PostgreSQL")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-password")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_dir = args.input.expanduser().resolve()
    if not input_dir.is_dir():
        print(f"ERROR: input folder not found: {input_dir}", file=sys.stderr)
        return 1

    config_path = args.config.expanduser().resolve()
    config = load_config(config_path)

    month = args.month
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        print("ERROR: --month must be YYYY-MM", file=sys.stderr)
        return 1

    groups = discover_csv_groups(input_dir, config["wallboard_filename_patterns"])
    if not groups:
        print(f"WARN: no usage CSV files found under {input_dir}", file=sys.stderr)

    db_conn = None
    if args.verify_db:
        if psycopg2 is None:
            print("ERROR: psycopg2 is required for --verify-db", file=sys.stderr)
            return 1
        if not args.db_name or not args.db_user:
            print("ERROR: --db-name and --db-user are required with --verify-db", file=sys.stderr)
            return 1
        db_conn = psycopg2.connect(
            host=args.db_host,
            port=args.db_port,
            dbname=args.db_name,
            user=args.db_user,
            password=args.db_password or "",
        )

    results: List[TenantProcessResult] = []
    for folder_str, csv_files in sorted(groups.items()):
        folder = Path(folder_str)
        res = process_tenant(folder, csv_files, config, month, args.verify_db, db_conn)
        if res.skipped_reason:
            print(f"SKIP: {folder.name} ({res.skipped_reason})")
            continue
        results.append(res)
        for peak in res.monthly_peaks:
            msg = (
                f"{peak.tenant_name} | {peak.license_category} | {peak.peak_date} | "
                f"peak={peak.peak_count} @ hour {peak.peak_hour}"
            )
            print(msg)

    summary_df = summaries_to_dataframe(results)
    daily_df = build_daily_peaks_report(results)
    session_rows: List[Dict[str, Any]] = []
    for res in results:
        session_rows.extend(res.session_details)
    session_df = pd.DataFrame(session_rows) if session_rows else pd.DataFrame()

    script_dir = Path(__file__).resolve().parent
    default_name = f"license_utilization_{month}.xlsx" if month else "license_utilization.xlsx"
    output_path = args.output.expanduser().resolve() if args.output else script_dir / default_name
    write_excel(output_path, summary_df, daily_df, session_df)
    print(f"Wrote {output_path}")

    if db_conn is not None:
        db_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
