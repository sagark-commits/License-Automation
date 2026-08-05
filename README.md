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
HOWTO_USE.md             # full how-to
```

## Security note

Internal TMONE / Ameyo operations tooling. Do **not** commit `db_config.yaml` with live passwords.