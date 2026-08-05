TMONE License Utilization — Server install (no internet)
=========================================================

FULL GUIDES
-----------
  HOWTO_USE.txt          Operator how-to (run reports)
  OFFLINE_INSTALL.txt    Air-gapped wheel install

PREREQUISITES
-------------
- python3.9 on server (from internal OS packages)
- Network to PostgreSQL 10.28.9.x (NOT public internet)

INSTALL FROM USB ZIP
--------------------
1. Unzip tmone_offline_bundle_py3.9.zip to /opt/
2. cd /opt/offline_bundle
3. PYTHON3=python3.9 ./install_offline.sh
4. cd license-utilization-automation
5. cp db_config.yaml.example db_config.yaml  (set credentials)
6. python3.9 check_env.py
7. python3.9 run_monthly_from_db.py -m YYYY-MM

BUILD USB ZIP (on PC with internet + Docker)
--------------------------------------------
  .\prepare_usb_bundle.ps1 -PythonVersion 3.9

REPO
----
  https://github.com/sagark-commits/License-Automation