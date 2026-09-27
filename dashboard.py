#!/usr/bin/env python3
"""TMONE License Utilization — web dashboard.

Wraps the existing CLI scripts (run_monthly_from_db.py, tenants.yaml) in a
Streamlit UI so reports can be triggered, tenants added, and DB activity
checked from a browser instead of the command line.

Run on the same server as the CLI tools (it needs db_config.yaml + network
access to the ARC-1 / ARC-2 Postgres hosts):

    streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

No login is enforced — keep this bound to the internal network only.
"""
from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import streamlit as st
import yaml
from ruamel.yaml import YAML

from db_connections import close_connection_pools, connect_login_databases

SCRIPT_DIR = Path(__file__).resolve().parent
TENANTS_PATH = SCRIPT_DIR / "tenants.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
DB_CONFIG_PATH = SCRIPT_DIR / "db_config.yaml"
ARC_CHOICES = ["ARC-1", "ARC-2"]

_ryaml = YAML()
_ryaml.preserve_quotes = True
_ryaml.indent(mapping=2, sequence=4, offset=2)
_ryaml.width = 4096


def load_tenants_raw() -> dict[str, Any]:
    with TENANTS_PATH.open("r", encoding="utf-8") as fh:
        return _ryaml.load(fh) or {}


def save_tenants_raw(data: dict[str, Any]) -> None:
    with TENANTS_PATH.open("w", encoding="utf-8") as fh:
        _ryaml.dump(data, fh)


def load_tenants_plain() -> dict[str, Any]:
    with TENANTS_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def python_bin() -> str:
    return st.session_state.get("python_bin", "python3.9")


def run_streaming(cmd: list[str], log_box) -> int:
    proc = subprocess.Popen(
        cmd,
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line.rstrip("\n"))
        log_box.code("\n".join(lines[-400:]), language="text")
    proc.wait()
    return proc.returncode


def list_output_files() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(OUTPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)


st.set_page_config(page_title="TMONE License Dashboard", layout="wide")
st.title("TMONE License Utilization Dashboard")

with st.sidebar:
    st.text_input("Server Python binary", value="python3.9", key="python_bin")
    if not DB_CONFIG_PATH.exists():
        st.error("db_config.yaml not found — copy db_config.yaml.example and set credentials.")
    else:
        st.success("db_config.yaml found")

tab_reports, tab_tenants, tab_live = st.tabs(
    ["Monthly reports", "Tenants", "Live tenants (last 30 days)"]
)

# ---------------------------------------------------------------------------
# Tab 1: run + download monthly reports
# ---------------------------------------------------------------------------
with tab_reports:
    st.subheader("Run a monthly report")
    col1, col2, col3 = st.columns(3)
    with col1:
        month_value = st.date_input("Month", value=date.today().replace(day=1))
        month_str = f"{month_value.year:04d}-{month_value.month:02d}"
    with col2:
        arc_choice = st.selectbox("ARC", ["Both", "ARC-1", "ARC-2"])
    with col3:
        active_only = st.checkbox("Active tenants only", value=False)

    if st.button("Run report", type="primary"):
        cmd = [python_bin(), "run_monthly_from_db.py", "-m", month_str]
        if arc_choice != "Both":
            cmd += ["--arc", arc_choice]
        if active_only:
            cmd += ["--active-only"]
        st.write(f"Running: `{' '.join(cmd)}`")
        log_box = st.empty()
        with st.spinner("Fetching from ARC-1 / ARC-2 and building workbooks..."):
            rc = run_streaming(cmd, log_box)
        if rc == 0:
            st.success("Report finished.")
        else:
            st.error(f"Report failed (exit code {rc}). See log above.")

    st.divider()
    st.subheader("Existing reports")
    files = list_output_files()
    if not files:
        st.info("No report files in output/ yet.")
    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{f.name}**  \nmodified {mtime}")
        c2.download_button("Download", data=f.read_bytes(), file_name=f.name, key=f"dl-{f.name}")

# ---------------------------------------------------------------------------
# Tab 2: view + add tenants
# ---------------------------------------------------------------------------
with tab_tenants:
    st.subheader("Registered tenants")
    plain = load_tenants_plain()
    tenants = plain.get("tenants", {}) or {}
    rows = [
        {
            "key": key,
            "arc": cfg.get("arc"),
            "contact_center_id": cfg.get("contact_center_id"),
            "campaign_ids": ", ".join(cfg.get("campaign_ids", []) or []),
            "project_name": str(cfg.get("project_name", "")).replace("\n", " "),
            "sheet_name": cfg.get("sheet_name"),
        }
        for key, cfg in sorted(tenants.items())
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Add a tenant")
    st.caption("Writes a new entry into tenants.yaml, same file the CLI scripts read.")
    with st.form("add_tenant_form", clear_on_submit=False):
        key = st.text_input("Tenant key (unique, e.g. NEWCO_ARCH1)").strip().upper()
        c1, c2 = st.columns(2)
        with c1:
            arc = st.selectbox("ARC", ARC_CHOICES)
            contact_center_id = st.number_input("contact_center_id", min_value=0, step=1)
            campaign_ids = st.text_input("campaign_ids (comma-separated, optional)")
            use_simple_query = st.checkbox("use_simple_query", value=False)
        with c2:
            project_name = st.text_input("project_name (shown on dashboard sheet)")
            sheet_name = st.text_input("sheet_name (Excel tab name)")
            csv_patterns = st.text_input("csv_patterns (comma-separated, optional)")
            folder_patterns = st.text_input("folder_patterns (comma-separated, optional)")

        st.markdown("**Login sheet names** (leave blank to skip that license type)")
        c3, c4, c5 = st.columns(3)
        agent_sheet = c3.text_input("agent login sheet")
        supervisor_sheet = c4.text_input("supervisor login sheet")
        wallboard_sheet = c5.text_input("wallboard login sheet")
        agent_display_label = st.text_input("agent_display_label override (optional)")

        submitted = st.form_submit_button("Add tenant")

    if submitted:
        if not key:
            st.error("Tenant key is required.")
        elif key in tenants:
            st.error(f"Tenant key '{key}' already exists.")
        elif not sheet_name or not contact_center_id:
            st.error("sheet_name and contact_center_id are required.")
        else:
            raw = load_tenants_raw()
            entry: dict[str, Any] = {
                "arc": arc,
                "project_name": project_name or key,
                "sheet_name": sheet_name,
                "contact_center_id": int(contact_center_id),
            }
            if campaign_ids.strip():
                entry["campaign_ids"] = [c.strip() for c in campaign_ids.split(",") if c.strip()]
            if use_simple_query:
                entry["use_simple_query"] = True
            if csv_patterns.strip():
                entry["csv_patterns"] = [c.strip() for c in csv_patterns.split(",") if c.strip()]
            if folder_patterns.strip():
                entry["folder_patterns"] = [c.strip() for c in folder_patterns.split(",") if c.strip()]
            if agent_display_label.strip():
                entry["agent_display_label"] = agent_display_label.strip()

            login_sheets = {}
            if agent_sheet.strip():
                login_sheets["agent"] = agent_sheet.strip()
            if supervisor_sheet.strip():
                login_sheets["supervisor"] = supervisor_sheet.strip()
            if wallboard_sheet.strip():
                login_sheets["wallboard"] = wallboard_sheet.strip()
            if login_sheets:
                entry["login_sheets"] = login_sheets

            raw.setdefault("tenants", {})[key] = entry
            save_tenants_raw(raw)
            st.success(f"Added '{key}' to tenants.yaml. Reload the page to see it in the table above.")

# ---------------------------------------------------------------------------
# Tab 3: live tenants — DB activity vs tenants.yaml registry
# ---------------------------------------------------------------------------
with tab_live:
    st.subheader("Contact centers active in the last 30 days")
    st.caption(
        "Queries user_session_history / users on each ARC's oneproduct DB for "
        "distinct contact_center_id with a login in the last 30 days, and "
        "flags any that aren't yet in tenants.yaml."
    )

    if st.button("Check live tenants"):
        if not DB_CONFIG_PATH.exists():
            st.error("db_config.yaml not found — cannot connect to the DBs.")
        else:
            plain = load_tenants_plain()
            registry: dict[tuple[str, int], list[str]] = {}
            for key, cfg in (plain.get("tenants", {}) or {}).items():
                arc = cfg.get("arc")
                cc = cfg.get("contact_center_id")
                if arc is not None and cc is not None:
                    registry.setdefault((arc, int(cc)), []).append(key)

            args = Namespace(
                db_host=None, db_port=None, db_user=None, db_password=None,
                db_name=None, login_db_name=None,
            )
            try:
                pools = connect_login_databases(args, SCRIPT_DIR)
            except Exception as exc:
                st.error(f"Database connection failed: {exc}")
                pools = None

            if pools:
                login_conns = pools.get("login", {})
                results = []
                query = """
                    SELECT DISTINCT bc.contact_center_id
                    FROM user_session_history mc
                    JOIN users bc ON mc.user_id = bc.user_id
                    WHERE mc.login_time >= now() - interval '30 days'
                    ORDER BY 1
                """
                for arc, conn in login_conns.items():
                    try:
                        with conn.cursor() as cur:
                            cur.execute(query)
                            cc_ids = [r[0] for r in cur.fetchall()]
                    except Exception as exc:
                        st.warning(f"{arc}: query failed ({exc})")
                        continue
                    for cc in cc_ids:
                        tenant_keys = registry.get((arc, int(cc)), [])
                        results.append(
                            {
                                "arc": arc,
                                "contact_center_id": int(cc),
                                "registered_as": ", ".join(tenant_keys) if tenant_keys else "— NOT REGISTERED —",
                                "status": "registered" if tenant_keys else "new",
                            }
                        )
                close_connection_pools(pools)

                if results:
                    new_only = [r for r in results if r["status"] == "new"]
                    st.dataframe(results, use_container_width=True, hide_index=True)
                    if new_only:
                        st.warning(
                            f"{len(new_only)} active contact center(s) have no tenants.yaml entry yet: "
                            + ", ".join(f"{r['arc']}/{r['contact_center_id']}" for r in new_only)
                            + ". Add them from the Tenants tab."
                        )
                    else:
                        st.success("Every active contact center is already registered.")
                else:
                    st.info("No login activity found in the last 30 days.")
