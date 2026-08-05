# TMONE License Utilization & Login Count — How To Use

**Project:** license-utilization-automation  
**Last updated:** 2026-08-05  
**Server Python:** `python3.9` only (not `python` / `python3`)

---

## 1. What this tool does

Automates the monthly TMONE reports that were previously built by exporting Ameyo UI CSVs one tenant at a time.

| Report | Contents |
|--------|----------|
| **Utilization** | Dashboard, Summary, per-tenant hourly peaks (Agent / Supervisor / Wallboard) |
| **Login count** | Session detail sheets (`user_id`, `login_time`, `logout_time`, `duration`) for each peak hour |

**Dual ARC:** ARC-1 (VPC) and ARC-2 (BPO) use different PostgreSQL hosts. One command connects to both and merges into a single Excel set.

---

## 2. Quick start (recommended — DB mode, no UI export)

### One-time setup on server

```bash
cd /opt/offline_bundle/license-utilization-automation

# Install wheels (if not done)
cd /opt/offline_bundle && PYTHON3=python3.9 ./install_offline.sh
cd license-utilization-automation

# DB credentials
cp db_config.yaml.example db_config.yaml
vi db_config.yaml
```

Set real values in `db_config.yaml`:

```yaml
enabled: true
database:
  port: 5432
  user: YOUR_REAL_DB_USER
  password: YOUR_REAL_PASSWORD
login_database:
  name: oneproduct
arc_databases:
  ARC-1:
    login: { host: 10.28.9.103, name: oneproduct }
  ARC-2:
    login: { host: 10.28.9.110, name: oneproduct }
```

Verify packages:

```bash
python3.9 -c "import pandas, openpyxl, yaml, psycopg2; print('OK')"
```

### Monthly run (both ARCs + utilization + login)

```bash
python3.9 run_monthly_from_db.py -m 2026-07
```

Or:

```bash
chmod +x run_monthly_from_db.sh
./run_monthly_from_db.sh 2026-07
```

### Output

```
output/Tmone license-Utilaztion_2026_07.xlsx
output/Tmone--Login Count Tmone_Jul_26.xlsx
```

---

## 3. Windows quick start

### DB mode

1. Edit `db_config.yaml` (same as above; network must reach `10.28.9.x`).
2. Run:

```bat
run_monthly_from_db.bat 2026-07
```

### CSV mode (UI exports)

1. Put Ameyo usage CSVs under `input\JULY LIC\<tenant folder>\*.csv`
2. Run:

```bat
python tmone_report.py -m 2026-07 -i "input/JULY LIC" -o output
```

Or edit `run_config.yaml` (`month`, `input_dir`) and use `run_monthly.bat`.

**Note:** CSV mode only includes tenants that have CSV files. Empty folders (e.g. missing UNI5G CSV) are skipped. DB mode uses all tenants in `tenants.yaml`.

---

## 4. Optional filters

```bash
# One ARC only
python3.9 run_monthly_from_db.py -m 2026-07 --arc ARC-1

# Specific tenants
python3.9 run_monthly_from_db.py -m 2026-07 --tenants SAMB,TESCO,CCLITE

# Active contact centers only
python3.9 run_monthly_from_db.py -m 2026-07 --active-only
```

Login-only workbook (existing flow):

```bash
python3.9 run_full_login.py -m 2026-07 --from-db
```

Utilization-only:

```bash
python3.9 tmone_report.py -m 2026-07 --from-db -o output
```

---

## 5. User types (Professional-Agent, Executive, Supervisor, Wallboard)

### Classification rules

| CSV / DB `user_type` | License bucket | Shown on sheet (default) |
|----------------------|----------------|---------------------------|
| Professional-Agent | **agent** | Professional-Agent |
| Executive | **agent** | Professional-Agent |
| Ameyo-express | **agent** | Professional-Agent |
| Supervisor | **supervisor** | Supervisor |
| Wallboard / Wallboard-User | **wallboard** | Wallboard User |

Executive is counted as **agent** so tenants like **CCLite** / **SAMB** show agent peaks on Dashboard (not only Supervisor).

### Per-tenant agent rename (dynamic)

Edit `tenants.yaml` — no code change needed.

**Global default:**

```yaml
defaults:
  tenant_sheet_agent_label: Professional-Agent
  agent_user_types:
    - Professional-Agent
    - Ameyo-express
    - Executive
```

**Rename agent label for specific tenants** (already configured):

```yaml
PRUBSN:
  agent_display_label: "Ameyo Pro-Dialer"

BONUSLINK_209:
  agent_display_label: "Ameyo Pro-Dialer"

AIG_FMAD_132:
  agent_display_label: "Ameyo Pro-Dialer"
```

**Full per-tenant control:**

```yaml
SOME_TENANT:
  agent_display_label: "My Custom Name"
  agent_user_types:          # optional: which CSV types count as agent
    - Professional-Agent
    - Executive
  display_labels:            # optional: rename all buckets
    agent: "My Custom Name"
    supervisor: "Supervisor"
    wallboard: "Wallboard User"
```

Logic file: `user_type_config.py`

---

## 6. Login count rules

For each tenant / license sheet:

1. Find **peak date** + **peak hour** (max concurrent users).
2. Query sessions that **overlap that hour** (not the whole day).
3. Write sheet: `user_id`, `login_time`, `logout_time`, `duration`.

Session count should be close to the utilization peak count.

---

## 7. CSV vs DB — why counts can differ

| | CSV mode | DB mode |
|--|----------|---------|
| Tenant list | Folders with CSV files only | All keys in `tenants.yaml` |
| Example July | 27 tenants, combined peak 691 | 30 tenants, combined peak 700 |

Extra DB-only tenants often: `PETRON_ARCH2`, `MBSA_ARCH2`, `IGLOO_247` (may be 0 if no July data).

`Combined agent peak` = sum of each tenant’s own monthly max (not one same-day total across all tenants).

---

## 8. Important files

| File | Role |
|------|------|
| `run_monthly_from_db.py` | One-command utilization + login from both ARCs |
| `tmone_report.py` | Utilization workbook builder |
| `run_full_login.py` | Full login workbook |
| `tenants.yaml` | Tenant registry, ARC, IDs, display labels |
| `db_config.yaml` | DB credentials (do not commit secrets) |
| `user_type_config.py` | User-type classification + display labels |
| `db_connections.py` | Dual ARC DB connections |
| `db_login_queries.py` | Peak-hour login SQL |
| `db_usage_queries.py` | JRXML-equivalent usage SQL |
| `excel_format.py` | Dashboard / Excel styling |

---

## 9. Monthly checklist

1. Set month: `-m YYYY-MM`
2. Confirm `db_config.yaml` user/password
3. Run: `python3.9 run_monthly_from_db.py -m YYYY-MM`
4. Open utilization Excel → check Dashboard tenant count and peaks
5. Open login Excel → spot-check a few sheets (e.g. TMSRC, CCLite, SAMB)
6. Copy `output/*.xlsx` for delivery

---

## 10. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `role "YOUR_DB_USER" does not exist` | Edit `db_config.yaml` — replace placeholders with real credentials |
| `source code string cannot contain null bytes` | File saved as UTF-16; re-copy UTF-8 scripts or run `fix_utf8_encoding.sh` |
| Use `python` / SyntaxError | Always use `python3.9` |
| Dashboard agent = 0 but sheet has Executive | Need `user_type_config.py` + `Executive` in `agent_user_types`; re-run |
| CSV shows fewer tenants than DB | Empty / missing CSV folders; DB uses full `tenants.yaml` |
| Login sessions ≫ peak count | Need peak-hour filter in `db_login_queries.py` (current code has it) |
| `no wheels for cp36` | `PYTHON3=python3.9 ./install_offline.sh` |

Connection test:

```bash
python3.9 -c "
from pathlib import Path
from argparse import Namespace
from db_connections import connect_login_databases, close_connection_pools
args = Namespace(db_user=None, db_password=None, db_port=5432, db_host=None, db_name=None, login_db_name=None, usage_db_name=None)
pools = connect_login_databases(args, Path('.'))
print('Connected:', list(pools['login'].keys()))
close_connection_pools(pools)
"
```

---

## 11. Deploy / copy to server

From Windows project folder, copy to `/opt/offline_bundle/license-utilization-automation/`:

- Prefer folder: `server_hotfix\` (latest scripts), or full `offline_bundle\`
- Always keep server `db_config.yaml` credentials (do not overwrite with example passwords)

After copy, confirm UTF-8:

```bash
file *.py | head
python3.9 -c "import user_type_config, tmone_report, run_monthly_from_db; print('OK')"
```

---

## 12. Flow diagram

```
tenants.yaml (arc + labels)
        |
        +-- ARC-1 tenants --> oneproduct @ 10.28.9.103
        |
        +-- ARC-2 tenants --> oneproduct @ 10.28.9.110
        |
        v
Compute peak date + peak hour
  (Executive counted as agent)
        |
        v
Fetch login sessions for that peak hour
        |
        v
output/
  Tmone license-Utilaztion_YYYY_MM.xlsx
  Tmone--Login Count Tmone_Mon_YY.xlsx
```

---

## 13. Support contacts / ownership

- Config owner: edit `tenants.yaml` for new tenants, `agent_display_label`, campaign IDs
- DB access: `db_config.yaml` on server only
- Rebuild offline wheels: `prepare_usb_bundle.ps1` on a machine with internet/Docker if needed

For dual-ARC notes only, see also: `HOWTO_DUAL_ARC_DB.txt`  
For offline install: `SERVER_HANDOFF_README.txt`