import yaml
from pathlib import Path
import sys
sys.path.insert(0, r"C:\Users\sagar.k\Downloads\license-utilization-automation")
from db_login_queries import generate_arc_sql_files
from tmone_report import load_config

script_dir = Path(r"C:\Users\sagar.k\Downloads\license-utilization-automation")
config = load_config(script_dir / "tenants.yaml")
generate_arc_sql_files(config["tenants"], config, str(script_dir))
print("generated SQL files")
