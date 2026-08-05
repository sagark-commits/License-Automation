#!/usr/bin/env python3
"""Run all peak-date login SQL files and save CSVs to input/login."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

from db_connections import close_connection_pools, connect_dual_databases, connection_for_arc
from login_export import _sql_query_from_file, prepare_login_dataframe


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--sql-dir", type=Path, default=Path("output/login_sql"))
    p.add_argument("--login-dir", type=Path, default=Path("input/login"))
    p.add_argument("--manifest", type=Path, help="login_manifest.csv path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    sql_dir = args.sql_dir.expanduser().resolve()
    login_dir = args.login_dir.expanduser().resolve()
    manifest_path = args.manifest or (sql_dir / "login_manifest.csv")
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    manifest = pd.read_csv(manifest_path)
    login_dir.mkdir(parents=True, exist_ok=True)

    class A: pass
    a = A()
    db_pools = connect_dual_databases(a, script_dir)
    login_conns = db_pools.get("login", {})
    ok = 0
    for _, r in manifest.iterrows():
        sheet_name = r["Sheet Name"]
        arc = r["ARC"]
        peak_date = r["Peak Date"]
        sql_file = sql_dir / r["SQL File"]
        if not peak_date or str(peak_date) == "nan" or not sql_file.exists():
            print(f"SKIP {sheet_name}: no peak date or SQL file")
            continue
        try:
            conn = connection_for_arc(login_conns, arc)
            sql = _sql_query_from_file(sql_file)
            df = pd.read_sql(sql, conn)
            df = prepare_login_dataframe(df)
            out = login_dir / f"{sheet_name}.csv"
            df.to_csv(out, index=False)
            print(f"OK {sheet_name}: {len(df)} rows -> {out.name}")
            ok += 1
        except Exception as exc:
            print(f"FAIL {sheet_name}: {exc}", file=sys.stderr)
    close_connection_pools(db_pools)
    print(f"Exported {ok} login CSV files to {login_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())