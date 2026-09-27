# TMONE License Utilization Automation

Automates monthly **TMONE license utilization** and **login count** reports for ARC-1 (VPC) and ARC-2 (BPO).

Instead of exporting Ameyo UI CSVs tenant-by-tenant, the tool connects to both PostgreSQL databases, computes peak dates/hours, fetches peak-hour login sessions, and writes merged Excel workbooks.

## Quick start (server)

```bash
cd /opt/offline_bundle/license-utilization-automation
cp db_config.yaml.example db_config.yaml   # set real user/password
python3.9 run_monthly_from_db.py -m 2026-07
```

**Output**

- `output/Tmone license-Utilaztion_YYYY_MM.xlsx` - Dashboard + Summary + tenant sheets
- `output/Tmone--Login Count Tmone_Mon_YY.xlsx` - peak-hour login sessions

## Dashboard (web UI)

A browser dashboard wraps the CLI so you don't need the terminal for day-to-day use:

```bash
cd /opt/offline_bundle/license-utilization-automation
./run_dashboard.sh          # streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
```

Open `http://<server-ip>:8501`. It runs on the same server (same `db_config.yaml`, same DB access as the CLI) and follows the actual monthly billing workflow, tab by tab:

1. **New tenants** — scans ARC-1/ARC-2 for `contact_center_id`s active in the last 30 days and flags any not yet in `tenants.yaml`; quick-register one right there.
2. **Run report** — pick a month/ARC, see exactly which tenants will be included, run `run_monthly_from_db.py` with a live log, download the resulting `.xlsx` files.
3. **Verify login counts** — cross-checks the utilization workbook's peak counts against the login workbook's peak-hour session counts per tenant/license; these must always agree, so a mismatch here is a real problem, not noise.
4. **Campaigns** — for tenants billed per-campaign instead of the whole contact center: fetches the live campaign list for a tenant's `contact_center_id` and lets you pick full-tenant vs. specific campaigns, writing straight into `campaign_ids`.
5. **Tenants registry** — full table, add-tenant form, and a remove action for offboarded customers.
6. **Run history** — every run triggered from the dashboard, with tenant counts at the time, so month-to-month tenant churn is visible.

No login is enforced — keep it on the internal network only. Requires `streamlit`, `ruamel.yaml`, and `pandas` (see `requirements.txt`).

### Reliability notes

The dashboard is split into `dashboard.py` (Streamlit UI only) and `dashboard_core.py` (all business logic, no Streamlit import — unit tested in `tests/`). Things it guards against by design:

- **tenants.yaml races** — every mutation (add/remove/campaign edit) goes through `dashboard_core.mutate_tenants()`: acquire a short advisory lock (`.tenants.yaml.lock`, auto-stolen if the holder crashed and the lock is >30s old) → reload the file fresh → validate → **back up** the pre-mutation file to `tenants_backups/` (last 30 kept) → round-trip-validate the new YAML → **atomically** write it (temp file + rename, so a crash mid-write can't truncate the file every CLI script reads).
- **Concurrent report runs** — a `.report.lock` in `output/` prevents two dashboard sessions from launching `run_monthly_from_db.py` at the same time (which would race on the same output `.xlsx` filenames). The run is also capped at a wall-clock timeout (default 3h, `TMONE_DASHBOARD_MAX_RUNTIME_SECONDS`) so a hung DB connection can't hang the dashboard forever; the lock has a stuck-process "force clear" escape hatch in the UI, gated behind an explicit confirmation.
- **psycopg2 aborted-transaction cascades** — a connection is left in an aborted state by Postgres after any failed query until `ROLLBACK`. The scan/verify/campaign-fetch helpers roll back defensively after every query attempt, so one tenant's bad query can't silently break every tenant queried after it on the same connection.
- **Ad-hoc query hangs** — every dashboard-opened connection gets a `statement_timeout` (default 60s) so a slow/blocked query fails fast instead of freezing the page.
- **Double-counting risk** — some contact centers intentionally have both a full-tenant entry and campaign-scoped entries (e.g. `AIG_FM` vs. `IGLOO_247`/`BONUSLINK_209` on cc 14). The dashboard surfaces this as an informational heads-up wherever it's relevant, never a hard block.

### Testing

`dashboard_core.py` has a real unit test suite (DB-touching functions are exercised against fakes, never a live DB):

```bash
pip install -r requirements-dev.txt   # dev-only: adds pytest on top of requirements.txt
pytest tests/ -v
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [HOWTO_USE.md](HOWTO_USE.md) | Full operator guide |
| [HOWTO_DUAL_ARC_DB.txt](HOWTO_DUAL_ARC_DB.txt) | Dual ARC DB merge |
| [SERVER_HANDOFF_README.txt](SERVER_HANDOFF_README.txt) | Offline server install |

## Requirements

- Python **3.9+** (`python3.9` on RHEL servers)
- Packages: see `requirements.txt` (`pandas`, `openpyxl`, `PyYAML`, `psycopg2-binary`)

## Configuration

1. **`db_config.yaml`** - copy from `db_config.yaml.example` (never commit real credentials)
2. **`tenants.yaml`** - tenant registry, ARC, contact center IDs, `agent_display_label` overrides

### User types

| CSV/DB type | Report bucket | Default sheet label |
|-------------|---------------|---------------------|
| Professional-Agent, Executive, Ameyo-express | agent | Professional-Agent |
| Supervisor | supervisor | Supervisor |
| Wallboard* | wallboard | Wallboard User |

Per-tenant rename example:

```yaml
PRUBSN:
  agent_display_label: "Ameyo Pro-Dialer"
```

## Main commands

```bash
# Both ARCs: utilization + login (recommended)
python3.9 run_monthly_from_db.py -m YYYY-MM

# Utilization only from DB
python3.9 tmone_report.py -m YYYY-MM --from-db -o output

# Login workbook only
python3.9 run_full_login.py -m YYYY-MM --from-db

# CSV mode (UI exports under input/)
python3.9 tmone_report.py -m YYYY-MM -i "input/JULY LIC" -o output
```

## Project layout

```
run_monthly_from_db.py   # one-command monthly runner
tmone_report.py          # utilization workbook
run_full_login.py        # login workbook
user_type_config.py     # classification + display labels
db_connections.py        # ARC-1 / ARC-2 connections
db_usage_queries.py      # JRXML-equivalent usage SQL
db_login_queries.py      # peak-hour login SQL
tenants.yaml             # tenant registry
db_config.yaml.example   # DB config template
dashboard.py             # Streamlit UI (thin — see below)
dashboard_core.py        # dashboard business logic, no Streamlit import, unit tested
tests/test_dashboard_core.py  # pytest suite for dashboard_core.py
HOWTO_USE.md             # full how-to
```

## Security note

Internal TMONE / Ameyo operations tooling. Do **not** commit `db_config.yaml` with live passwords.

## Offline / no-internet servers

Python packages are **bundled as wheels** for air-gapped install.

1. On a PC with internet + Docker: `.\prepare_usb_bundle.ps1 -PythonVersion 3.9`
2. Copy ZIP to server USB
3. On server: `PYTHON3=python3.9 ./install_offline.sh`
4. See **[OFFLINE_INSTALL.md](OFFLINE_INSTALL.md)** for full steps

This tool needs Python 3.9+; it is not a zero-dependency binary. Dependencies travel with the USB zip.