# Web UI for the monthly licence report

Generate the TMONE utilization + login workbooks from a browser instead of running scripts.
Pure Python standard library plus the packages the report engine already uses - nothing new to install.

## Install (server, python3.9, no internet)

```bash
cd /opt/offline_bundle/license-utilization-automation      # copy the repo here (git clone or USB)
python3.9 check_env.py                                     # pandas / openpyxl / PyYAML / psycopg2
python3.9 run_web.py init --admin sagar                    # creates ./data and the first administrator
python3.9 run_web.py serve --host 0.0.0.0 --port 8088
```

Open `http://<server>:8088`, sign in, then **Databases**: enter host / database / user / password for ARC-1
(`10.28.9.103`) and ARC-2 (`10.28.9.110`), save, and press **Test saved connection**.
Passwords are stored in `data/config/db_config.yaml` (mode 600) and never shown again.

Use a **read-only** Postgres role for the report. Connections are opened read-only with a query time limit anyway.

## Daily use

1. **Generate** -> choose the month -> *Generate report*. It runs in the background (you can close the tab).
2. The run page shows live progress, then **Checks and findings**. Green = the two workbooks agree with each other and with
   the tenant list. Red = *do not send*; the findings say what is wrong.
3. Download the two workbooks (or the zip).

Every report is re-read after writing and cross-checked (`verify_report.py`): Dashboard vs Summary vs tenant sheets, every
licence with a peak has a login sheet, and the sheet lists exactly as many distinct users as the peak. It also warns about
Ameyo user types that are not counted and about tenants that dropped sharply against last month.

## Adding a tenant / changing the Ameyo agent type

**Tenants -> Add tenant.** Enter ARC and contact center id, then **Find from database**: it lists the user types that contact
center really uses (last 90 days), its campaigns and wallboard logins, and lets you assign each user type to Agent,
Supervisor, Wallboard or Not counted.

- By default a tenant uses the **global** lists (Settings): Agent = Professional-Agent, Ameyo-express, Executive;
  Supervisor = Supervisor; Wallboard = Wallboard*.
- Choose **Custom list for this tenant** to replace a list for that tenant only (the override is exclusive).
- A user type in none of the lists is **not counted** and the report warns about it - it is never silently reported as 0.
- Login sheet names are generated automatically if left blank.
- *Count supervisors even when they have no campaign* is off by default (legacy behaviour). It cannot be combined with
  campaign ids. Decide with billing before turning it on.

`Tenants -> <tenant> -> Show the SQL` displays exactly what is run for that tenant.

## Users and roles

`viewer` downloads; `operator` also generates reports and edits tenants/settings; `admin` also manages users and databases.
Sign-ins, config changes, runs and downloads are in **Audit**. CLI: `run_web.py add-user NAME --role operator`,
`run_web.py set-password NAME`.

## Running as a service / TLS

* Direct TLS: `serve --cert server.crt --key server.key` (e.g. made with the ssl-cert-tool).
* Or nginx in front: see `deploy/nginx-license-ui.conf` and `deploy/license-ui.service` (`--trust-proxy --secure-cookies`).
* Without either, passwords cross the network in clear text - keep it on the internal network only.

## Backup, upgrade, rollback

* Back up `data/` (tenants, users, DB settings, past reports). Every tenant-registry save keeps a copy in
  `data/config/history/`; **Settings -> Restore** (admin) rolls back.
* Upgrade: stop the service, replace the code (keep `data/`), `python3.9 run_web.py check`, start.
* `python3.9 run_web.py check` verifies packages, tenant list and database settings.

## Verify any pair of workbooks from the command line

```bash
python3.9 verify_report.py "Tmone license-Utilaztion_2026_09.xlsx" "Tmone--Login Count Tmone_Sep_26.xlsx"
```

## Notes

* The command-line scripts (`run_monthly_from_db.py` ...) still work; `data/config/tenants.yaml` is the UI's copy of
  `tenants.yaml` (seeded from it on first start). Edit tenants in the UI, not in both places.
* `fix_utf8_encoding.sh` is no longer needed: files were converted to UTF-8.
