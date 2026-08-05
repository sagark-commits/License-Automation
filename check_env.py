#!/usr/bin/env python3
"""Quick environment check for air-gapped deployments."""
from __future__ import annotations
import sys

def main() -> int:
    print("Python:", sys.version.replace("\n", " "))
    ok = True
    for name, mod in [
        ("pandas", "pandas"),
        ("openpyxl", "openpyxl"),
        ("PyYAML", "yaml"),
        ("psycopg2", "psycopg2"),
    ]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "ok")
            print(f"OK  {name}: {ver}")
        except Exception as exc:
            print(f"MISSING {name}: {exc}")
            ok = False
    if not ok:
        print("\nInstall offline wheels:")
        print("  PYTHON3=python3.9 ../install_offline.sh")
        print("See OFFLINE_INSTALL.txt")
        return 1
    print("\nEnvironment OK. Configure db_config.yaml then run:")
    print("  python3.9 run_monthly_from_db.py -m YYYY-MM")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())