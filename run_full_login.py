#!/usr/bin/env python3
"""Build full login count workbook (all sheets) from DB and/or input/login CSVs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

if sys.version_info < (3, 8):
    sys.exit("ERROR: Python 3.8+ required")

from csv_login import attach_login_csvs
from db_connections import close_connection_pools, connect_login_databases, connection_for_arc
from login_export import export_login_sql_files, write_login_workbook
from tmone_db_mode import build_tenant_data_from_db, iter_tenants_for_db
from tmone_report import (
    LicensePeak,
    TenantData,
    _discover_login_files_multi,
    _resolve_login_dirs,
    _tenant_allowed,
    build_tenant_data,
    compute_peaks,
    discover_files,
    fetch_login_sessions,
    load_config,
    normalize_month,
)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Build full TMONE login count workbook")
    p.add_argument("-m", "--month", required=True, help="Report month YYYY-MM")
    p.add_argument("-i", "--input", type=Path, help="Usage CSV root folder (optional with --from-db)")
    p.add_argument("--login-dir", type=Path, help="Folder with login session CSV exports")
    p.add_argument("-o", "--output-dir", type=Path, default=script_dir / "output")
    p.add_argument("-c", "--config", type=Path, default=script_dir / "tenants.yaml")
    p.add_argument("--db-config", type=Path, default=script_dir / "db_config.yaml")
    p.add_argument("--from-db", action="store_true", help="Peak dates + login sessions from oneproduct")
    p.add_argument("--export-sql", action="store_true", help="Also write SQL files to output/login_sql")
    p.add_argument("--tenants", help="Comma-separated tenant keys")
    p.add_argument("--arc", choices=["ARC-1", "ARC-2"], help="Only tenants for this ARC")
    p.add_argument("--active-only", action="store_true", help="Only active_contact_centers tenants")
    p.add_argument("--login-db-name", help="Login DB (default oneproduct)")
    p.add_argument("--usage-db-name", help="Usage DB (default reportsdb)")
    p.add_argument("--db-host")
    p.add_argument("--db-port", type=int, default=5432)
    p.add_argument("--db-user")
    p.add_argument("--db-password")
    return p.parse_args()


def _tenant_filter(args, run_cfg: dict) -> list[str] | None:
    if getattr(args, "tenants", None):
        return [t.strip() for t in args.tenants.split(",") if t.strip()]
    tenants = run_cfg.get("tenants")
    return list(tenants) if tenants else None


def _resolve_input_dir(args, script_dir: Path, run_cfg: dict) -> Path:
    if args.input:
        return args.input.expanduser().resolve()
    cfg_path = run_cfg.get("input_dir")
    if cfg_path:
        candidate = Path(cfg_path)
        if not candidate.is_absolute():
            candidate = (script_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate
    return (script_dir / "input").resolve()


def _build_tenants_from_db(
    args,
    config: dict,
    run_cfg: dict,
    script_dir: Path,
) -> list[TenantData]:
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("ERROR: psycopg2 required for --from-db", file=sys.stderr)
        raise SystemExit(1)

    tenant_filter = _tenant_filter(args, run_cfg)
    arc_filter = getattr(args, "arc", None) or run_cfg.get("arc") or None
    if arc_filter == "":
        arc_filter = None
    active_only = bool(getattr(args, "active_only", False) or run_cfg.get("active_only", False))

    db_pools = connect_login_databases(args, script_dir)
    # JRXML-equivalent usage SQL uses oneproduct tables (user_session_history, etc.)
    peak_conns = db_pools.get("login", {})
    print("DB: oneproduct (peak dates + login sessions)")

    tenants_data: list[TenantData] = []
    try:
        for key, cfg in iter_tenants_for_db(config, tenant_filter, arc_filter, active_only=active_only):
            if not cfg.get("login_sheets"):
                continue
            try:
                conn = connection_for_arc(peak_conns, cfg.get("arc", "ARC-1"))
            except KeyError as exc:
                print(f"WARN: {key}: {exc}", file=sys.stderr)
                continue
            td = build_tenant_data_from_db(
                key,
                cfg,
                conn,
                config,
                args.month,
                TenantData=TenantData,
                compute_peaks=compute_peaks,
                LicensePeak=LicensePeak,
                normalize_month=normalize_month,
            )
            if td is None:
                td = TenantData(key=key, cfg=cfg, usage_df=pd.DataFrame())
                print(f"WARN: no DB usage data for {key} — empty peak dates", file=sys.stderr)
            tenants_data.append(td)
            a, s = td.peaks.get("agent"), td.peaks.get("supervisor")
            print(
                f"{cfg.get('project_name', key).split(chr(10))[0]} ({cfg.get('arc')}) [DB] | "
                f"Agent: {a.peak_date if a else '-'}={a.peak_count if a else 0} | "
                f"Sup: {s.peak_date if s else '-'}={s.peak_count if s else 0}"
            )
    finally:
        close_connection_pools(db_pools)

    return tenants_data


def _build_tenants_from_csv(
    args,
    config: dict,
    run_cfg: dict,
    script_dir: Path,
) -> tuple[list[TenantData], dict]:
    input_dir = _resolve_input_dir(args, script_dir, run_cfg)
    if not input_dir.is_dir():
        print(f"ERROR: input folder not found: {input_dir}", file=sys.stderr)
        print("  Use --from-db to run without usage CSVs (needs db_config.yaml on server).", file=sys.stderr)
        raise SystemExit(1)

    arc_filter = getattr(args, "arc", None) or run_cfg.get("arc") or None
    if arc_filter == "":
        arc_filter = None
    active_only = bool(getattr(args, "active_only", False) or run_cfg.get("active_only", False))

    tenants_data: list[TenantData] = []
    grouped = discover_files(input_dir, config)
    for key, entry in grouped.items():
        if not _tenant_allowed(key, entry["cfg"], config, arc_filter, active_only):
            continue
        td = build_tenant_data(key, entry, config, args.month)
        if td:
            tenants_data.append(td)

    if not tenants_data:
        print("ERROR: no tenant usage data found in CSV input", file=sys.stderr)
        raise SystemExit(1)

    login_dirs = _resolve_login_dirs(input_dir, run_cfg, args)
    login_index = _discover_login_files_multi(login_dirs, config)
    attach_login_csvs(tenants_data, login_index, config)
    return tenants_data, login_index


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    run_cfg_path = script_dir / "run_config.yaml"
    run_cfg = yaml.safe_load(run_cfg_path.read_text(encoding="utf-8")) if run_cfg_path.exists() else {}

    try:
        args.month = normalize_month(args.month)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    config = load_config(args.config.expanduser().resolve())
    use_db = args.from_db or bool(run_cfg.get("verify_db", False) or run_cfg.get("from_db", False))

    if use_db:
        tenants_data = _build_tenants_from_db(args, config, run_cfg, script_dir)
        if not tenants_data:
            print("ERROR: no tenants with login_sheets matched", file=sys.stderr)
            return 1
        input_dir = _resolve_input_dir(args, script_dir, run_cfg)
        if input_dir.is_dir():
            login_dirs = _resolve_login_dirs(input_dir, run_cfg, args)
            login_index = _discover_login_files_multi(login_dirs, config)
            attach_login_csvs(tenants_data, login_index, config)
    else:
        tenants_data, _ = _build_tenants_from_csv(args, config, run_cfg, script_dir)

    if use_db:
        db_pools = {}
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            print("ERROR: psycopg2 required for --from-db", file=sys.stderr)
            return 1
        try:
            db_pools = connect_login_databases(args, script_dir)
            login_conns = db_pools.get("login", {})
            print("Fetching login sessions from oneproduct...")
            for td in tenants_data:
                try:
                    conn = connection_for_arc(login_conns, td.cfg.get("arc", "ARC-1"))
                    fetch_login_sessions(conn, td, config)
                except Exception as exc:
                    print(f"WARN: {td.key}: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"ERROR: database connection failed: {exc}", file=sys.stderr)
            return 1
        finally:
            close_connection_pools(db_pools)

    if args.export_sql or not use_db:
        sql_dir = args.output_dir / "login_sql"
        manifest = export_login_sql_files(tenants_data, config, sql_dir)
        print(f"SQL files: {len(manifest)} in {sql_dir}")

    login_path = args.output_dir / (
        f"Tmone--Login Count Tmone_{datetime.strptime(args.month, '%Y-%m').strftime('%b_%y')}.xlsx"
    )
    count = write_login_workbook(login_path, tenants_data, config, args.month)
    with_data = sum(
        1 for td in tenants_data for s in td.login_sessions.values() if s is not None and len(s) > 0
    )
    print(f"Wrote login count report: {login_path}")
    print(f"  Sheets: {count} | With session data: {with_data}")
    if count and with_data < count:
        print("  Missing data: check peak dates / DB access, or place login CSVs in input/login/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
