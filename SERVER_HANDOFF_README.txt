TMONE License Utilization — Server install (no internet)
=========================================================

You received this folder from a colleague. The server does NOT need internet.

FULL USAGE GUIDE
----------------
See: HOWTO_USE.txt  (or HOWTO_USE.md)
     HOWTO_DUAL_ARC_DB.txt

PREREQUISITES ON SERVER
-----------------------
- python3.9 installed (run: python3.9 --version)
  On secondaryappdb: /usr/bin/python3.9
- Do NOT use `python` (2.7) or default `python3` (3.6)
- Network access to internal PostgreSQL hosts (10.28.9.x)

INSTALL
-------
1. Unzip tmone_offline_bundle_py*.zip to /opt/
2. Install packages:
     cd /opt/offline_bundle
     chmod +x install_offline.sh
     PYTHON3=python3.9 ./install_offline.sh
3. Configure DB:
     cd /opt/offline_bundle/license-utilization-automation
     cp db_config.yaml.example db_config.yaml
     vi db_config.yaml
   Set real database.user and database.password (not placeholders).

MONTHLY RUN (recommended)
-------------------------
  python3.9 run_monthly_from_db.py -m 2026-07

Produces:
  output/Tmone license-Utilaztion_2026_07.xlsx
  output/Tmone--Login Count Tmone_Jul_26.xlsx

Both ARC-1 and ARC-2 are fetched and merged automatically.

VERIFY
------
  python3.9 -c "import pandas, openpyxl, yaml, psycopg2, user_type_config; print('OK')"

TROUBLESHOOTING
---------------
- "role YOUR_DB_USER does not exist" -> fix db_config.yaml credentials
- "null bytes" -> re-copy UTF-8 scripts from server_hotfix
- "no wheels for cp36" -> PYTHON3=python3.9 ./install_offline.sh
- See HOWTO_USE.txt section 10 for more

FILES
-----
offline_bundle/wheels/                         Python packages
offline_bundle/license-utilization-automation/ Application + HOWTO_USE.txt