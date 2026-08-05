#!/usr/bin/env python3
"""One-command monthly TMONE reports from BOTH ARC databases."""
from __future__ import annotations
import argparse
import sys
import warnings
from datetime import datetime
from pathlib import Path
import pandas as pd
if sys.version_info < (3, 8):
    sys.exit("ERROR: Python 3.8+ required (use python3.9 on server)")
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)
from db_connections import close_connection_pools, connect_login_databases, connection_for_arc, load_db_config
from login_export import write_login_workbook
from tmone_db_mode import build_tenant_data_from_db, iter_tenants_for_db
from tmone_report import (
    LicensePeak, TenantData, compute_peaks, fetch_login_sessions,
    load_config, normalize_month, write_utilization_workbook,
)

def parse_args():
    script_dir = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Fetch TMONE reports from ARC-1 and ARC-2 DBs merged")
    p.add_argument("-m", "--month", required=True, help="Report month YYYY-MM")
    p.add_argument("-o", "--output-dir", type=Path, default=script_dir / "output")
    p.add_argument("-c", "--config", type=Path, default=script_dir / "tenants.yaml")
    p.add_argument("--db-config", type=Path, default=script_dir / "db_config.yaml")
    p.add_argument("--arc", choices=["ARC-1", "ARC-2"], help="Only one ARC (default both)")
    p.add_argument("--tenants", help="Comma-separated tenant keys")
    p.add_argument("--active-only", action="store_true")
    p.add_argument("--db-user")
    p.add_argument("--db-password")
    p.add_argument("--db-port", type=int, default=5432)
    return p.parse_args()

def _print_connection_plan(script_dir, arc_filter):
    cfg = load_db_config(script_dir)
    arcs = cfg.get("arc_databases") or {}
    if not arcs:
        print("WARN: no arc_databases in db_config.yaml")
        return
    print("DB plan (tenant.arc -> host):")
    for arc, pools in arcs.items():
        if arc_filter and arc != arc_filter:
            continue
        login = (pools or {}).get("login", pools) or {}
        print("  %s: %s / %s" % (arc, login.get("host", "?"), login.get("name", "oneproduct")))

def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    try:
        args.month = normalize_month(args.month)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    db_cfg_path = args.db_config.expanduser().resolve()
    if not db_cfg_path.exists():
        print("ERROR: missing %s" % db_cfg_path, file=sys.stderr)
        print("  Copy db_config.yaml.example to db_config.yaml", file=sys.stderr)
        return 1
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("ERROR: psycopg2 required", file=sys.stderr)
        return 1
    config = load_config(args.config.expanduser().resolve())
    tenant_filter = [t.strip() for t in args.tenants.split(",") if t.strip()] if args.tenants else None
    arc_filter = args.arc
    print("=" * 60)
    print("TMONE monthly from DB | month=%s" % args.month)
    print("=" * 60)
    _print_connection_plan(script_dir, arc_filter)
    try:
        db_pools = connect_login_databases(args, script_dir)
    except Exception as exc:
        print("ERROR: database connection failed: %s" % exc, file=sys.stderr)
        return 1
    login_conns = db_pools.get("login", {})
    connected = ", ".join(sorted(login_conns.keys())) or "(none)"
    print("Connected ARCs: %s" % connected)
    tenants_data = []
    try:
        for key, cfg in iter_tenants_for_db(config, tenant_filter, arc_filter, active_only=args.active_only):
            arc = cfg.get("arc", "ARC-1")
            try:
                conn = connection_for_arc(login_conns, arc)
            except KeyError as exc:
                print("WARN: %s: %s" % (key, exc), file=sys.stderr)
                continue
            td = build_tenant_data_from_db(
                key, cfg, conn, config, args.month,
                TenantData=TenantData, compute_peaks=compute_peaks,
                LicensePeak=LicensePeak, normalize_month=normalize_month,
            )
            if td is None:
                td = TenantData(key=key, cfg=cfg, usage_df=pd.DataFrame())
                print("WARN: %s (%s): no usage data for %s" % (key, arc, args.month), file=sys.stderr)
            else:
                a = td.peaks.get("agent")
                s = td.peaks.get("supervisor")
                e = td.peaks.get("executive")
                parts = []
                if a and a.peak_count:
                    parts.append("Agent %s=%s@h%s" % (a.peak_date, a.peak_count, a.peak_hour))
                else:
                    parts.append("Agent -")
                if s and s.peak_count:
                    parts.append("Sup %s=%s@h%s" % (s.peak_date, s.peak_count, s.peak_hour))
                else:
                    parts.append("Sup -")
                if e and e.peak_count:
                    parts.append("Exec %s=%s@h%s" % (e.peak_date, e.peak_count, e.peak_hour))
                print("OK  %s (%s) | %s" % (key, arc, " | ".join(parts)))
            tenants_data.append(td)
        if not tenants_data:
            print("ERROR: no tenants matched", file=sys.stderr)
            return 1
        print("-" * 60)
        print("Fetching login sessions at peak hour from oneproduct...")
        for td in tenants_data:
            if not td.peaks:
                continue
            try:
                conn = connection_for_arc(login_conns, td.cfg.get("arc", "ARC-1"))
                fetch_login_sessions(conn, td, config)
            except Exception as exc:
                print("WARN: login %s: %s" % (td.key, exc), file=sys.stderr)
    finally:
        close_connection_pools(db_pools)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    util_path = args.output_dir / ("Tmone license-Utilaztion_%s.xlsx" % args.month.replace("-", "_"))
    month_label = datetime.strptime(args.month, "%Y-%m").strftime("%b_%y")
    login_path = args.output_dir / ("Tmone--Login Count Tmone_%s.xlsx" % month_label)
    write_utilization_workbook(util_path, tenants_data, args.month, config)
    sheet_count = write_login_workbook(login_path, tenants_data, config, args.month)
    with_data = sum(1 for td in tenants_data for s in td.login_sessions.values() if s is not None and len(s) > 0)
    print("=" * 60)
    print("Utilization : %s" % util_path)
    print("Login count : %s" % login_path)
    print("Tenants     : %s" % len(tenants_data))
    print("Login sheets: %s | with session data: %s" % (sheet_count, with_data))
    print("Done. ARC-1 and ARC-2 are merged in these files.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())