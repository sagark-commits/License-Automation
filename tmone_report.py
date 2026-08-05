#!/usr/bin/env python3
"""Generate TMONE license utilization workbooks matching the manual May 2026 format."""

from __future__ import annotations

import argparse
import calendar
import sys

if sys.version_info < (3, 8):
    sys.exit("ERROR: Python 3.8+ required. Use: python3 tmone_report.py")

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

try:
    import psycopg2
except ImportError:
    psycopg2 = None

HOUR_COLUMNS = [f"Count at {h} hour" for h in range(1, 25)]
LICENSE_KEYS = ("agent", "supervisor", "wallboard")
_MONTH_NAMES = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTH_ABBR = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}


def normalize_month(value: str, default_year: int | None = None) -> str:
    """Accept YYYY-MM, or a month name like June / Jun (uses default_year or current year)."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("month is empty")
    if len(raw) >= 7 and raw[4] == "-":
        year_s, month_s = raw.split("-", 1)[:2]
        year, month = int(year_s), int(month_s)
        if month < 1 or month > 12:
            raise ValueError(f"invalid month in {raw!r}")
        return f"{year:04d}-{month:02d}"
    low = raw.lower()
    month_num = _MONTH_NAMES.get(low) or _MONTH_ABBR.get(low)
    if month_num is None:
        raise ValueError(f"month must be YYYY-MM or a month name (got {raw!r})")
    year = default_year if default_year is not None else date.today().year
    return f"{year:04d}-{month_num:02d}"


@dataclass
class LicensePeak:
    license_key: str
    license_label: str
    peak_date: date | None = None
    peak_count: int = 0
    peak_hour: int = 0
    daily_counts: dict[date, int] = field(default_factory=dict)


@dataclass
class TenantData:
    key: str
    cfg: dict[str, Any]
    usage_df: pd.DataFrame
    wallboard_df: pd.DataFrame | None = None
    peaks: dict[str, LicensePeak] = field(default_factory=dict)
    login_sessions: dict[str, pd.DataFrame] = field(default_factory=dict)


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    defaults = data.get("defaults", {})
    return {
        "defaults": defaults,
        "tenants": data.get("tenants", {}) or {},
        "agent_types": list(defaults.get("agent_user_types", [])),
        "supervisor_types": list(defaults.get("supervisor_user_types", [])),
        "wallboard_types": list(defaults.get("wallboard_user_types", [])),
        "wallboard_patterns": list(defaults.get("wallboard_filename_patterns", ["wallboard"])),
        "license_labels": defaults.get(
            "license_labels",
            {"agent": "Ameyo Express", "supervisor": "Ameyo Supervisor", "wallboard": "Ameyo Wallboard"},
        ),
        "tenant_agent_label": defaults.get("tenant_sheet_agent_label", "Ameyo-express"),
        "active_contact_centers": defaults.get("active_contact_centers", {}) or {},
    }


def is_wallboard(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns)


def classify_user_type(
    user_type: str,
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None = None,
) -> str | None:
    from user_type_config import classify_user_type as _classify

    return _classify(user_type, config, tenant_cfg)


def display_user_type(
    license_key: str,
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None = None,
) -> str:
    from user_type_config import resolve_display_label

    return resolve_display_label(license_key, config, tenant_cfg)


def match_tenant(path: Path, tenants: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    folder = path.parent.name
    haystack = f"{folder}/{path.name}".lower()
    best: tuple[str, dict[str, Any], int] | None = None
    for key, cfg in tenants.items():
        score = 0
        for pat in cfg.get("csv_patterns", [key]):
            if str(pat).lower() in haystack:
                score = max(score, len(str(pat)))
        for pat in cfg.get("folder_patterns", []):
            if str(pat).lower() in folder.lower():
                score += 50
        if score and (best is None or score > best[2]):
            best = (key, cfg, score)
    return (best[0], best[1]) if best else None


def read_usage_csv(path: Path, month: str | None) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    if "Date" not in df.columns:
        raise ValueError(f"Missing Date column in {path.name}")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Date"])
    for col in HOUR_COLUMNS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    if month:
        y, m = map(int, normalize_month(month).split("-"))
        df = df[df["Date"].apply(lambda d: d.year == y and d.month == m)]
    for col in HOUR_COLUMNS:
        if col not in df.columns:
            df[col] = 0
    df["Max count"] = df[HOUR_COLUMNS].max(axis=1)
    return df


def row_peak_hour(row: pd.Series) -> int:
    counts = [int(row[c]) for c in HOUR_COLUMNS]
    return counts.index(max(counts)) + 1 if counts else 0


def compute_peaks(
    df: pd.DataFrame,
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None = None,
) -> dict[str, LicensePeak]:
    from user_type_config import apply_peak_display_labels, resolve_display_label

    peaks = {
        k: LicensePeak(
            license_key=k,
            license_label=resolve_display_label(k, config, tenant_cfg),
        )
        for k in LICENSE_KEYS
    }
    if df.empty:
        return peaks
    for _, row in df.iterrows():
        license_key = classify_user_type(row.get("user_type", ""), config, tenant_cfg)
        if not license_key:
            continue
        if license_key not in peaks:
            peaks[license_key] = LicensePeak(
                license_key=license_key,
                license_label=resolve_display_label(license_key, config, tenant_cfg),
            )
        d = row["Date"]
        count = int(row["Max count"])
        peaks[license_key].daily_counts[d] = max(peaks[license_key].daily_counts.get(d, 0), count)
    for license_key, peak in peaks.items():
        if not peak.daily_counts:
            continue
        peak.peak_count = max(peak.daily_counts.values())
        peak.peak_date = max(d for d, c in peak.daily_counts.items() if c == peak.peak_count)
        day_rows = df[
            df["Date"].eq(peak.peak_date)
            & df["user_type"].apply(
                lambda ut: classify_user_type(ut, config, tenant_cfg) == license_key
            )
        ]
        if not day_rows.empty:
            peak.peak_hour = row_peak_hour(day_rows.loc[day_rows["Max count"].idxmax()])
    apply_peak_display_labels(peaks, config, tenant_cfg)
    return peaks


def _tenant_allowed(key: str, cfg: dict, config: dict, arc: str | None, active_only: bool) -> bool:
    if arc and cfg.get("arc") != arc:
        return False
    if not active_only:
        return True
    active = config.get("active_contact_centers") or {}
    arc_key = cfg.get("arc")
    if not arc_key:
        return True
    allowed = {int(x) for x in active.get(arc_key, [])}
    if not allowed:
        return True
    cc = cfg.get("contact_center_id")
    return cc is not None and int(cc) in allowed


def _arc_folder_bonus(folder: str, arc: str | None) -> int:
    if not arc:
        return 0
    f = folder.lower().replace("-", " ")
    if arc == "ARC-1" and any(x in f for x in ("arc 1", "arch 1", "acr 1")):
        return 100
    if arc == "ARC-2" and any(x in f for x in ("arc 2", "arch 2")):
        return 100
    return 0

def discover_files(input_dir: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for csv_path in sorted(input_dir.rglob("*.csv")):
        folder = csv_path.parent.name.lower()
        filename = csv_path.name.lower()
        best_key: str | None = None
        best_score = 0
        best_cfg: dict[str, Any] | None = None
        for key, cfg in config["tenants"].items():
            score = 0
            csv_hit = False
            for pat in cfg.get("csv_patterns", [key]):
                p = str(pat).lower()
                if p in filename:
                    score += len(p) * 3
                    csv_hit = True
                elif p in folder:
                    score += len(p) * 2
                    csv_hit = True
            score += _arc_folder_bonus(folder, cfg.get("arc"))
            for pat in cfg.get("folder_patterns", []):
                if str(pat).lower() in folder:
                    score += 50
            folder_hit = any(str(pat).lower() in folder for pat in cfg.get("folder_patterns", []))
            if (csv_hit or folder_hit) and score > best_score:
                best_key, best_cfg, best_score = key, cfg, score
        if not best_key or not best_cfg:
            continue
        entry = grouped.setdefault(best_key, {"cfg": best_cfg, "usage": [], "wallboard": []})
        if is_wallboard(csv_path.name, config["wallboard_patterns"]):
            entry["wallboard"].append(csv_path)
        else:
            entry["usage"].append(csv_path)
    return grouped


def build_tenant_data(key: str, entry: dict[str, Any], config: dict[str, Any], month: str | None) -> TenantData | None:
    if not entry.get("usage"):
        return None
    frames = []
    for p in entry["usage"]:
        try:
            frames.append(read_usage_csv(p, month))
        except Exception as exc:
            print(f"WARN: skip {p.name}: {exc}", file=sys.stderr)
    if not frames:
        return None
    usage_df = pd.concat(frames, ignore_index=True)
    wallboard_df = None
    if entry.get("wallboard"):
        wallboard_df = pd.concat([read_usage_csv(p, month) for p in entry["wallboard"]], ignore_index=True)
    tenant_cfg = entry["cfg"]
    td = TenantData(key=key, cfg=tenant_cfg, usage_df=usage_df, wallboard_df=wallboard_df)
    td.peaks = compute_peaks(usage_df, config, tenant_cfg)
    if wallboard_df is not None and not wallboard_df.empty:
        wb = compute_peaks(wallboard_df, config, tenant_cfg)
        src = wb.get("wallboard") or wb.get("agent")
        if src and src.daily_counts:
            td.peaks["wallboard"] = LicensePeak(
                license_key="wallboard",
                license_label=display_user_type("wallboard", config, tenant_cfg),
                peak_date=src.peak_date,
                peak_count=src.peak_count,
                peak_hour=src.peak_hour,
                daily_counts=dict(src.daily_counts),
            )
    return td


def month_dates(month: str) -> list[date]:
    y, m = map(int, normalize_month(month).split("-"))
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def build_summary_sheet(tenants_data: list[TenantData], month: str, config: dict[str, Any]) -> pd.DataFrame:
    dates = month_dates(month)
    month_label = datetime.strptime(normalize_month(month), "%Y-%m").strftime("%B %Y")
    rows: list[list[Any]] = [
        [f"TmOne License Utilization Report — {month_label}", *([None] * (2 + len(dates)))],
        ["Project Name", "License Type", "Summary of Max Login", *[d.strftime("%d-%b") for d in dates]],
        [None, None, None, *[d.strftime("%a") for d in dates]],
    ]
    order = list(config["tenants"].keys())

    def sort_key(td: TenantData) -> int:
        try:
            return order.index(td.key)
        except ValueError:
            return 999

    for td in sorted(tenants_data, key=sort_key):
        project = td.cfg.get("project_name", td.key)
        first = True
        keys_to_show = ["agent", "supervisor"]
        if td.cfg.get("login_sheets", {}).get("wallboard") or (
            td.wallboard_df is not None and not td.wallboard_df.empty
        ):
            keys_to_show.append("wallboard")
        for license_key in keys_to_show:
            peak = td.peaks.get(license_key)
            label = (
                peak.license_label
                if peak and peak.license_label
                else display_user_type(license_key, config, td.cfg)
            )
            if peak and peak.daily_counts:
                rows.append([
                    project if first else None,
                    label,
                    peak.peak_count,
                    *[peak.daily_counts.get(d, 0) for d in dates],
                ])
            else:
                rows.append([project if first else None, label, 0, *[0 for _ in dates]])
            first = False
        rows.append([None] * (3 + len(dates)))
    return pd.DataFrame(rows)


def build_tenant_sheet(td: TenantData, config: dict[str, Any]) -> pd.DataFrame:
    detail_cols = ["Date", "user_type", "Max count", *HOUR_COLUMNS]
    peak_rows: list[list[Any]] = [["Date", "user_type", "Max_count"]]
    for license_key in LICENSE_KEYS:
        peak = td.peaks.get(license_key)
        if peak and peak.peak_date and peak.peak_count > 0:
            peak_rows.append(
                [peak.peak_date, display_user_type(license_key, config, td.cfg), peak.peak_count]
            )
    peak_rows.extend([[None, None, None], [None, None, None]])
    detail = td.usage_df.copy()
    if detail.empty or "user_type" not in detail.columns:
        width = len(detail_cols)
        all_rows = peak_rows + [detail_cols]
        return pd.DataFrame([row + [None] * (width - len(row)) for row in all_rows])

    def _map_ut(ut: Any) -> Any:
        key = classify_user_type(ut, config, td.cfg)
        return display_user_type(key, config, td.cfg) if key else ut

    detail["user_type"] = detail["user_type"].apply(_map_ut)
    detail = detail[detail_cols]
    width = len(detail_cols)
    all_rows = peak_rows + [detail_cols] + detail.values.tolist()
    return pd.DataFrame([row + [None] * (width - len(row)) for row in all_rows])


def fetch_login_sessions(conn, td: TenantData, config: dict[str, Any]) -> None:
    from db_login_queries import login_session_query

    for license_key, sheet_name in td.cfg.get("login_sheets", {}).items():
        # Executive is folded into agent peaks/display; still allow executive login sheets.
        peak_key = "agent" if license_key == "executive" else license_key
        peak = td.peaks.get(peak_key) or td.peaks.get(license_key)
        if not peak or not peak.peak_date or peak.peak_count <= 0:
            continue
        from login_export import prepare_login_dataframe

        peak_hour = peak.peak_hour or 0
        query_key = "agent" if license_key == "executive" else license_key
        sql, params = login_session_query(td.cfg, query_key, config, peak.peak_date, peak_hour)
        try:
            df = pd.read_sql(sql, conn, params=params)
        except Exception as exc:
            print(f"  WARN login query {sheet_name}: {exc}", file=sys.stderr)
            df = pd.DataFrame()
        td.login_sessions[sheet_name] = prepare_login_dataframe(df)
        n = len(td.login_sessions[sheet_name])
        hour_label = f" hour {peak_hour}" if peak_hour else ""
        print(
            f"  Login DB: {sheet_name} | peak {peak.peak_date}{hour_label} "
            f"(max {peak.peak_count}) | {n} sessions"
        )


def write_utilization_workbook(path: Path, tenants_data: list[TenantData], month: str, config: dict[str, Any]) -> None:
    from excel_format import format_utilization_workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        build_summary_sheet(tenants_data, month, config).to_excel(writer, sheet_name="Summary", index=False, header=False)
        for td in tenants_data:
            build_tenant_sheet(td, config).to_excel(writer, sheet_name=str(td.cfg.get("sheet_name", td.key))[:31], index=False, header=False)
        format_utilization_workbook(writer.book, normalize_month(month), tenants_data, config)




def _resolve_login_dirs(input_dir: Path, run_cfg: dict[str, Any], args) -> list[Path]:
    dirs: list[Path] = []
    if getattr(args, "login_dir", None):
        dirs.append(Path(args.login_dir).expanduser().resolve())
    cfg_dir = run_cfg.get("login_dir")
    if cfg_dir:
        dirs.append(Path(cfg_dir).expanduser().resolve())
        if not Path(cfg_dir).is_absolute():
            dirs.append((input_dir / cfg_dir).expanduser().resolve())
    dirs.append((input_dir / "login").expanduser().resolve())
    script_login = Path(__file__).resolve().parent / "input" / "login"
    dirs.append(script_login.resolve())
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _discover_login_files_multi(login_dirs: list[Path], config: dict[str, Any]) -> dict[str, dict[str, list[Path]]]:
    merged: dict[str, dict[str, list[Path]]] = {}
    for login_dir in login_dirs:
        if not login_dir.is_dir():
            continue
        found = discover_login_files(login_dir, config)
        for tenant_key, lic_map in found.items():
            for lic, paths in lic_map.items():
                merged.setdefault(tenant_key, {}).setdefault(lic, []).extend(paths)
    return merged

from csv_login import attach_login_csvs, build_peak_summary_workbook, discover_login_files
from login_export import export_login_sql_files, write_login_workbook

from db_connections import close_connection_pools, connect_login_databases, connection_for_arc, resolve_db_settings
from tmone_db_mode import build_tenant_data_from_db, iter_tenants_for_db


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Generate TMONE license utilization Excel reports")
    p.add_argument("--input", "-i", type=Path, help="Root folder containing tenant CSV exports")
    p.add_argument("--login-dir", type=Path, help="Login session CSV folder (default: input/login)")
    p.add_argument("--month", "-m", help="Report month YYYY-MM")
    p.add_argument("--config", "-c", type=Path, default=script_dir / "tenants.yaml")
    p.add_argument("--db-config", type=Path, default=script_dir / "db_config.yaml")
    p.add_argument("--output-dir", "-o", type=Path, help="Folder for generated Excel files")
    p.add_argument("--from-db", action="store_true", help="Fetch usage from PostgreSQL instead of CSV")
    p.add_argument("--verify-db", action="store_true", help="Fetch login sessions from PostgreSQL")
    p.add_argument("--export-login-sql", action="store_true", help="Export peak-date login SQL files")
    p.add_argument("--tenants", help="Comma-separated tenant keys (DB mode)")
    p.add_argument("--arc", choices=["ARC-1", "ARC-2"], help="Only process tenants for this ARC")
    p.add_argument("--active-only", action="store_true", help="Only active_contact_centers tenants")
    p.add_argument("--login-db-name", help="Login DB (default oneproduct)")
    p.add_argument("--usage-db-name", help="Usage DB (default reportsdb)")
    p.add_argument("--db-host", default=None)
    p.add_argument("--db-port", type=int, default=5432)
    p.add_argument("--db-name", default="")
    p.add_argument("--db-user", default="")
    p.add_argument("--db-password", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    run_cfg_path = script_dir / "run_config.yaml"
    run_cfg: dict[str, Any] = {}
    if run_cfg_path.exists():
        with run_cfg_path.open(encoding="utf-8") as fh:
            run_cfg = yaml.safe_load(fh) or {}

    if not args.input:
        args.input = Path(run_cfg.get("input_dir", script_dir / "input"))
    if not args.month:
        args.month = str(run_cfg.get("month", ""))
    if not args.output_dir:
        args.output_dir = Path(run_cfg.get("output_dir", script_dir / "output"))

    if not args.month:
        print("ERROR: --month is required (or set month in run_config.yaml)", file=sys.stderr)
        return 1
    try:
        args.month = normalize_month(args.month)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    config = load_config(args.config.expanduser().resolve())
    from_db = bool(getattr(args, "from_db", False) or run_cfg.get("from_db", False))
    tenant_filter = None
    if getattr(args, "tenants", None):
        tenant_filter = [t.strip() for t in args.tenants.split(",") if t.strip()]
    elif run_cfg.get("tenants"):
        tenant_filter = list(run_cfg.get("tenants") or [])

    tenants_data = []
    db_pools: dict = {}

    if from_db:
        if psycopg2 is None:
            print("ERROR: psycopg2 required for --from-db", file=sys.stderr)
            return 1
        active_only = bool(getattr(args, "active_only", False) or run_cfg.get("active_only", False))
        arc_filter = getattr(args, "arc", None) or run_cfg.get("arc")
        try:
            db_pools = connect_login_databases(args, script_dir)
        except Exception as exc:
            print(f"ERROR: database connection failed: {exc}", file=sys.stderr)
            return 1
        # JRXML-equivalent usage SQL runs on oneproduct (not reportsdb).
        peak_conns = db_pools["login"]
        print("DB: oneproduct (usage peaks + login)")
        for key, cfg in iter_tenants_for_db(config, tenant_filter, arc_filter, active_only=active_only):
            try:
                conn = connection_for_arc(peak_conns, cfg.get("arc", "ARC-1"))
            except KeyError as exc:
                print(f"WARN: {key}: {exc}", file=sys.stderr)
                continue
            td = build_tenant_data_from_db(
                key, cfg, conn, config, args.month,
                TenantData=TenantData, compute_peaks=compute_peaks,
                LicensePeak=LicensePeak, normalize_month=normalize_month,
            )
            if not td:
                print(f"WARN: no DB usage data for {key}", file=sys.stderr)
                continue
            tenants_data.append(td)
            a, s = td.peaks.get("agent"), td.peaks.get("supervisor")
            print(f"{td.cfg.get('project_name', key).split(chr(10))[0]} ({td.cfg.get('arc')}) [DB] | Agent: {a.peak_date if a else '-'}={a.peak_count if a else 0} | Sup: {s.peak_date if s else '-'}={s.peak_count if s else 0}")
        if not tenants_data:
            print("ERROR: no tenant DB usage data returned", file=sys.stderr)
            close_connection_pools(db_pools)
            return 1
    else:
        input_dir = args.input.expanduser().resolve()
        if not input_dir.is_dir():
            print(f"ERROR: input folder not found: {input_dir}", file=sys.stderr)
            return 1
        arc_filter = getattr(args, "arc", None) or run_cfg.get("arc") or None
        if arc_filter == "":
            arc_filter = None
        active_only = bool(getattr(args, "active_only", False) or run_cfg.get("active_only", False))
        grouped = discover_files(input_dir, config)
        login_dirs = _resolve_login_dirs(input_dir, run_cfg, args)
        login_index = _discover_login_files_multi(login_dirs, config)
        for key, entry in grouped.items():
            if not _tenant_allowed(key, entry["cfg"], config, arc_filter, active_only):
                continue
            td = build_tenant_data(key, entry, config, args.month)
            if not td:
                continue
            tenants_data.append(td)
            a, s = td.peaks.get("agent"), td.peaks.get("supervisor")
            print(f"{td.cfg.get('project_name', key).split(chr(10))[0]} ({td.cfg.get('arc')}) | Agent: {a.peak_date if a else '-'}={a.peak_count if a else 0} | Sup: {s.peak_date if s else '-'}={s.peak_count if s else 0}")
        if not tenants_data:
            print("ERROR: no tenant CSV data matched", file=sys.stderr)
            return 1
        attach_login_csvs(tenants_data, login_index, config)
    util_path = args.output_dir / f"Tmone license-Utilaztion_{args.month.replace('-', '_')}.xlsx"
    login_path = args.output_dir / f"Tmone--Login Count Tmone_{datetime.strptime(args.month, '%Y-%m').strftime('%b_%y')}.xlsx"
    verify_db = bool(getattr(args, "verify_db", False) or run_cfg.get("verify_db", False))
    if from_db:
        verify_db = True
    if verify_db:
        if psycopg2 is None:
            print("ERROR: psycopg2 required", file=sys.stderr)
            return 1
        if not db_pools:
            try:
                db_pools = connect_login_databases(args, script_dir)
            except Exception as exc:
                print(f"ERROR: database connection failed: {exc}", file=sys.stderr)
                return 1
        login_conns = db_pools.get("login", {})
        for td in tenants_data:
            try:
                conn = connection_for_arc(login_conns, td.cfg.get("arc", "ARC-1"))
                fetch_login_sessions(conn, td, config)
            except Exception as exc:
                print(f"WARN: DB login {td.key}: {exc}", file=sys.stderr)
    if from_db:
        login_dirs = _resolve_login_dirs(script_dir / "input", run_cfg, args)
        login_index = _discover_login_files_multi(login_dirs, config)
        attach_login_csvs(tenants_data, login_index, config)
    close_connection_pools(db_pools)
    write_utilization_workbook(util_path, tenants_data, args.month, config)
    print(f"Wrote utilization report: {util_path}")
    export_login_sql = bool(run_cfg.get("export_login_sql", True))
    if getattr(args, "export_login_sql", False):
        export_login_sql = True
    if export_login_sql and not verify_db:
        sql_dir = args.output_dir / "login_sql"
        manifest = export_login_sql_files(tenants_data, config, sql_dir)
        if not manifest.empty:
            print(f"Wrote {len(manifest)} login SQL files: {sql_dir}")
            print("  Run SQL on oneproduct, export CSV per sheet name, place in input/login/, re-run")

    sheet_count = write_login_workbook(login_path, tenants_data, config, args.month)
    if sheet_count:
        print(f"Wrote login count report: {login_path} ({sheet_count} sheets)")
    else:
        summary = build_peak_summary_workbook(tenants_data, config)
        summary.to_excel(login_path, sheet_name="Peak Summary", index=False)
        print(f"Wrote peak login summary: {login_path}")
        print("  To get user sessions: run with --verify-db OR place login CSVs in input/login/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
