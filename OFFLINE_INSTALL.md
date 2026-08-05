# Offline / Air-Gapped Install Guide

## Important reality check

This tool **cannot run with zero dependencies**.
It needs:

1. **Python 3.9+** installed on the server (system package / already present)
2. Python packages: `pandas`, `openpyxl`, `PyYAML`, `psycopg2-binary`

Most servers have **no internet**. Solution:
- Build a USB zip **once** on a machine that HAS internet (or Docker)
- Copy zip to the air-gapped server
- Install packages from local `wheels/` folder (no pip download)

You do **not** need internet on the production server.

---

## What the offline bundle contains

```
offline_bundle/
  wheels/                         # all .whl files (Linux manylinux2014)
  <app-folder>/                   # application scripts + configs
  install_offline.sh              # server installer (no internet)
  OFFLINE_INSTALL.txt             # this guide
  SERVER_HANDOFF_README.txt
```

---

## Step A — Build USB bundle (machine WITH internet)

### Requirements on build PC
- Windows + Docker Desktop (recommended), OR Linux with Docker
- Target Python version must match server (usually **3.9**)

### Build command (Windows PowerShell)

```powershell
cd <project-folder>
.\prepare_usb_bundle.ps1 -PythonVersion 3.9
```

Creates:
- `offline_bundle/`
- `*_offline_bundle_py3.9.zip`

Default wheels are **manylinux2014** (works on older RHEL/CentOS glibc).

### If server Python is 3.11

```powershell
.\prepare_usb_bundle.ps1 -PythonVersion 3.11
```

---

## Step B — Transfer to air-gapped server

1. Copy the ZIP to USB / SCP / shared drive
2. On server:

```bash
unzip -o *_offline_bundle_py3.9.zip -d /opt/
cd /opt/offline_bundle
chmod +x install_offline.sh
PYTHON3=python3.9 ./install_offline.sh
```

---

## Step C — Configure and run (still no internet)

```bash
cd /opt/offline_bundle/<app-folder>
cp db_config.yaml.example db_config.yaml
vi db_config.yaml          # set real DB user/password + hosts

python3.9 -c "import pandas, openpyxl, yaml, psycopg2; print('OK')"

python3.9 run_monthly_from_db.py -m 2026-07
```

Reports appear under `output/`.

---

## Server prerequisites (ops team)

| Item | Notes |
|------|------|
| Python 3.9+ | `python3.9 --version` |
| unzip | to extract bundle |
| Network to DB hosts only | e.g. 10.x PostgreSQL — NOT public internet |
| pip for that Python | usually `python3.9 -m pip` |

If Python is missing, install from **internal** RPM/ISO mirror (not public internet):

```bash
# example only — use your org's package process
yum install python39 python39-pip
# or
dnf install python3.9
```

---

## Why not "pure Python / no packages"?

| Need | Package |
|------|---------|
| Read/write Excel .xlsx | openpyxl (+ pandas) |
| Peak calculations / tables | pandas |
| PostgreSQL queries | psycopg2 |
| YAML config | PyYAML |

Rewriting without these would be fragile and much slower to maintain.
Bundled wheels are the production approach for air-gapped sites.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `no wheels for cp36` | Server used python3.6 — use `PYTHON3=python3.9` |
| `not a supported wheel on this platform` | Rebuild with `-LegacyServer` / manylinux2014 (default) |
| `No matching distribution` | Bundle Python version != server Python — rebuild matching version |
| `pip: command not found` | Use `python3.9 -m pip ...` |
| `role "YOUR_DB_USER" does not exist` | Edit `db_config.yaml` credentials |

Verify wheels before install:

```bash
ls wheels/*.whl | wc -l    # should be 10+
ls wheels | head
```

---

## Security

- Do **not** put real passwords into the zip before USB transfer if policy forbids it
- Prefer creating `db_config.yaml` only on the server
- Never commit `db_config.yaml` to git