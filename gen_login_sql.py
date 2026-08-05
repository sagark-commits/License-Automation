"""Generate login_queries_arc1.sql and login_queries_arc2.sql from tenants.yaml."""
from __future__ import annotations

import yaml
from pathlib import Path

from db_login_queries import generate_arc_sql_files
from tmone_report import load_config


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    tenants_cfg = yaml.safe_load((script_dir / "tenants.yaml").read_text(encoding="utf-8"))
    config = load_config(tenants_cfg)
    generate_arc_sql_files(tenants_cfg["tenants"], config, str(script_dir))
    print("Wrote login_queries_arc1.sql and login_queries_arc2.sql")


if __name__ == "__main__":
    main()
