# TMONE License Utilization & Login Count — How To Use

**Project:** license-utilization-automation  
**Repo:** https://github.com/sagark-commits/License-Automation  
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

## 2. Offline / air-gapped servers (no internet)

Most TMONE servers have **no internet**. This tool needs Python packages (`pandas`, `openpyxl`, `PyYAML`, `psycopg2`), but they are **shipped as wheels** in a USB zip. The server never runs `pip install` from the internet.

### Full guide
See **OFFLINE_INSTALL.md** / **OFFLINE_INSTALL.txt** for details.

### Build USB zip (PC WITH internet + Docker)

```powershell
cd license-utilization-automation
.\prepare_usb_bundle.ps1 -PythonVersion 3.9
```

Creates: `tmone_offline_bundle_py3.9.zip`

### Install on air-gapped server

```bash
unzip -o tmone_offline_bundle_py3.9.zip -d /opt/
cd /opt/offline_bundle
chmod +x install_offline.sh
PYTHON3=python3.9 ./install_offline.sh
```

### Verify

```bash
cd /opt/offline_bundle/license-utilization-automation
python3.9 check_env.py
```

**Note:** Python 3.9+ must already exist on the server (from internal OS packages / ISO). Only the Python *libraries* come from the USB zip.

---

## 3. Quick start (recommended — DB mode, no UI export)

### One-time setup on server

```bash
cd /opt/offline_bundle/license-utilization-automation

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

## 4. Windows quick start

### DB mode

1. Edit `db_config.yaml` (network must reach `10.28.9.x`).
2. Ensure packages installed (`pip install -r requirements.txt` or offline wheels).
3. Run:

```bat
run_monthly_from_db.bat 2026-07
```

### CSV mode (UI exports)

1. Put Ameyo usage CSVs under `input\JULY LIC\<tenant folder>\*.csv`
2. Run:

```bat
python tmone_report.py -m 2026-07 -i "input/JULY LIC" -o output
```

**Note:** CSV mode only includes tenants that have CSV files. DB mode uses all tenants in `tenants.yaml`.

---

## 5. Optional filters

```bash
# One ARC only
python3.9 run_monthly_from_db.py -m 2026-07 --arc ARC-1

# Specific tenants
python3.9 run_monthly_from_db.py -m 2026-07 --tenants SAMB,TESCO,CCLITE

# Active contact centers only
python3.9 run_monthly_from_db.py -m 2026-07 --active-only
```

Login-only workbook:

```bash
python3.9 run_full_login.py -m 2026-07 --from-db
```

Utilization-only:

```bash
python3.9 tmone_report.py -m 2026-07 --from-db -o output
```

---

## 6. User types (Professional-Agent, Executive, Supervisor, Wallboard)

| CSV / DB `user_type` | License bucket | Shown on sheet (default) |
|----------------------|----------------|---------------------------|
| Professional-Agent | **agent** | Professional-Agent |
| Executive | **agent** | Professional-Agent |
| Ameyo-express | **agent** | Professional-Agent |
| Supervisor | **supervisor** | Supervisor |
| Wallboard / Wallboard-User | **wallboard** | Wallboard User |

Executive is counted as **agent** so tenants like **CCLite** / **SAMB** show agent peaks on Dashboard.

### Per-tenant agent rename

```yaml
PRUBSN:
  agent_display_label: "Ameyo Pro-Dialer"

BONUSLINK_209:
  agent_display_label: "Ameyo Pro-Dialer"

AIG_FMAD_132:
  agent_display_label: "Ameyo Pro-Dialer"
```

Logic file: `user_type_config.py`

---

## 7. Login count rules

1. Find **peak date** + **peak hour** (max concurrent users).
2. Query sessions that **overlap that hour** (not the whole day).
3. Write sheet: `user_id`, `login_time`, `logout_time`, `duration`.

---

## 8. CSV vs DB — why counts can differ

| | CSV mode | DB mode |
|--|----------|---------|
| Tenant list | Folders with CSV files only | All keys in `tenants.yaml` |
| Example | Fewer tenants if folders empty | Full configured inventory |

`Combined agent peak` = sum of each tenant's own monthly max (not one same-day total).

---

## 9. Important files

| File | Role |
|------|------|
| `run_monthly_from_db.py` | One-command utilization + login from both ARCs |
| `tmone_report.py` | Utilization workbook builder |
| `run_full_login.py` | Full login workbook |
| `tenants.yaml` | Tenant registry, ARC, IDs, display labels |
| `db_config.yaml` | DB credentials (do not commit secrets) |
| `user_type_config.py` | User-type classification + display labels |
| `prepare_usb_bundle.ps1` | Build offline wheel zip (needs Docker) |
| `install_offline.sh` | Install wheels on air-gapped server |
| `check_env.py` | Verify Python packages |
| `OFFLINE_INSTALL.md` | Offline / no-internet guide |

---

## 10. Monthly checklist

1. Set month: `-m YYYY-MM`
2. Confirm `db_config.yaml` user/password
3. Run: `python3.9 run_monthly_from_db.py -m YYYY-MM`
4. Open utilization Excel → check Dashboard
5. Open login Excel → spot-check a few sheets
6. Copy `output/*.xlsx` for delivery

---

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `role "YOUR_DB_USER" does not exist` | Edit `db_config.yaml` — real credentials |
| `source code string cannot contain null bytes` | Re-copy UTF-8 scripts / `fix_utf8_encoding.sh` |
| Use `python` / SyntaxError | Always use `python3.9` |
| Dashboard agent = 0 but sheet has Executive | Need `user_type_config.py` + Executive in agent types; re-run |
| `no wheels for cp36` | `PYTHON3=python3.9 ./install_offline.sh` |
| `not a supported wheel` | Rebuild USB zip with manylinux2014 (default in prepare script) |
| Missing packages | Run `install_offline.sh` from USB bundle; then `check_env.py` |

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

## 12. Deploy / clone from GitHub

```bash
git clone https://github.com/sagark-commits/License-Automation.git
cd License-Automation
```

For air-gapped servers, prefer the **USB offline zip** (includes wheels), not a bare git clone (clone has no wheels).

After copy, confirm:

```bash
python3.9 check_env.py
python3.9 -c "import user_type_config, tmone_report, run_monthly_from_db; print('OK')"
```

---

## 13. Flow diagram

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

## 14. Related docs

- `OFFLINE_INSTALL.md` — air-gapped install
- `HOWTO_DUAL_ARC_DB.txt` — dual ARC merge
- `SERVER_HANDOFF_README.txt` — server handoff
- `README.md` — project overview