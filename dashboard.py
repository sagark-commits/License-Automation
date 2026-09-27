#!/usr/bin/env python3
"""TMONE License Utilization — web dashboard (UI layer).

All business logic (tenants.yaml IO, locking, DB queries, subprocess
handling) lives in dashboard_core.py, which has no Streamlit dependency and
is unit tested independently (tests/test_dashboard_core.py). This file only
renders widgets and wires them to that module.

Workflow:
    1. New tenants  — check ARC-1 / ARC-2 for contact centers active in the
       last 30 days that aren't in tenants.yaml yet, and register them.
    2. Run report   — run run_monthly_from_db.py for a month, watch the log,
       download the merged utilization + login workbooks.
    3. Verify login counts — cross-check the two workbooks agree.
    4. Campaigns    — pick full-tenant vs. specific campaigns per tenant.
    5. Tenants      — full registry view, add/remove tenants.
    6. Run history  — every run this dashboard has triggered.

Run on the same server as the CLI tools (it needs db_config.yaml + network
access to the ARC-1 / ARC-2 Postgres hosts):

    streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

No login is enforced — keep this bound to the internal network only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import streamlit as st

import dashboard_core as core

st.set_page_config(page_title="TMONE License Dashboard", layout="wide")
st.title("TMONE License Utilization Dashboard")
st.caption("ARC-1 (Ameyo VPC) + ARC-2 (Ameyo BPO) — monthly license billing workflow")

with st.sidebar:
    st.text_input("Server Python binary", value="python3.9", key="python_bin")
    if not core.DB_CONFIG_PATH.exists():
        st.error("db_config.yaml not found — copy db_config.yaml.example and set credentials.")
    else:
        st.success("db_config.yaml found")
        perm_warning = core.check_db_config_permissions()
        if perm_warning:
            st.warning(perm_warning)

    lock = core.report_lock_status()
    if lock:
        st.warning(f"Report run in progress (pid {lock.get('pid')} on {lock.get('host')}).")

    if "new_tenants_scan" in st.session_state:
        pending = [r for r in st.session_state["new_tenants_scan"] if r["status"] == "new"]
        if pending:
            st.warning(f"{len(pending)} unregistered contact center(s) found in last scan.")


def python_bin() -> str:
    return (st.session_state.get("python_bin") or "").strip() or "python3.9"


tab_new, tab_reports, tab_verify, tab_campaigns, tab_tenants, tab_history = st.tabs(
    [
        "1. New tenants", "2. Run report", "3. Verify login counts",
        "4. Campaigns", "5. Tenants registry", "6. Run history",
    ]
)

# ---------------------------------------------------------------------------
# Tab 1: find tenants active in DB but not in tenants.yaml, register them
# ---------------------------------------------------------------------------
with tab_new:
    st.subheader("Check for new tenants before running the monthly report")
    st.caption(
        "Queries user_session_history / users on each ARC's oneproduct DB for "
        "distinct contact_center_id with a login in the last 30 days, and "
        "compares against tenants.yaml. Do this first each month — customers "
        "get added/removed on ARC-1 and ARC-2 independently."
    )

    if st.button("Scan ARC-1 + ARC-2 now", type="primary"):
        if not core.DB_CONFIG_PATH.exists():
            st.error("db_config.yaml not found — cannot connect to the DBs.")
        else:
            try:
                st.session_state["new_tenants_scan"] = core.scan_new_contact_centers()
            except Exception as exc:
                st.error(f"Database connection / query failed: {exc}")

    results = st.session_state.get("new_tenants_scan")
    if results is None:
        st.info("Not scanned yet this session.")
    else:
        error_rows = [r for r in results if r["status"] == "error"]
        for r in error_rows:
            st.error(f"{r['arc']}: {r.get('note')}")

        new_rows = [r for r in results if r["status"] == "new"]
        registered_rows = [r for r in results if r["status"] == "registered"]

        if new_rows:
            st.error(f"{len(new_rows)} contact center(s) are active but NOT in tenants.yaml:")
            st.dataframe(new_rows, width="stretch", hide_index=True)

            st.markdown("#### Quick-register a new tenant")
            options = [f"{r['arc']} / contact_center_id {r['contact_center_id']}" for r in new_rows]
            choice = st.selectbox("Pick a detected contact center", options, key="quick_pick")
            picked = new_rows[options.index(choice)]

            with st.form("quick_add_form"):
                c1, c2 = st.columns(2)
                with c1:
                    key = st.text_input("Tenant key (unique)", value=f"NEW_CC{picked['contact_center_id']}").strip().upper()
                    sheet_name = st.text_input("sheet_name (Excel tab name)", value=key)
                with c2:
                    project_name = st.text_input("project_name", value=key)
                    use_simple_query = st.checkbox("use_simple_query", value=True)
                agent_sheet = st.text_input("agent login sheet name (optional)")
                quick_submit = st.form_submit_button("Register this tenant")

            if quick_submit:
                entry: dict[str, Any] = {
                    "arc": picked["arc"],
                    "project_name": project_name or key,
                    "sheet_name": sheet_name or key,
                    "contact_center_id": picked["contact_center_id"],
                }
                if use_simple_query:
                    entry["use_simple_query"] = True
                if agent_sheet.strip():
                    entry["login_sheets"] = {"agent": agent_sheet.strip()}
                try:
                    core.add_tenant(key, entry)
                except core.TenantValidationError as exc:
                    st.error(str(exc))
                except core.LockTimeoutError as exc:
                    st.error(str(exc))
                else:
                    st.success(
                        f"Registered '{key}' ({picked['arc']} / cc {picked['contact_center_id']}). "
                        "It will be included the next time you run the report on tab 2."
                    )
                    st.session_state["new_tenants_scan"] = [
                        r if r is not picked else {**r, "status": "registered", "registered_as": key}
                        for r in st.session_state["new_tenants_scan"]
                    ]
        else:
            st.success("No unregistered contact centers found — tenants.yaml is up to date.")

        if registered_rows:
            with st.expander(f"Already-registered active contact centers ({len(registered_rows)})"):
                st.dataframe(registered_rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 2: run + download monthly reports
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

    if core.month_is_incomplete_or_future(month_str):
        st.warning(
            f"{month_str} hasn't fully elapsed yet — a report for it will be based on partial-month data. "
            "Usually you want last month for billing."
        )

    try:
        preview_keys = core.tenants_matching_run(arc_choice, active_only)
        st.caption(f"This run will include **{len(preview_keys)}** tenant(s): {', '.join(preview_keys) or '—'}")
    except Exception as exc:
        preview_keys = []
        st.warning(f"Could not preview tenant list: {exc}")

    lock = core.report_lock_status()
    run_disabled = lock is not None
    if lock:
        st.info(
            f"A report run is already in progress (pid {lock.get('pid')} on {lock.get('host')}, "
            f"started {datetime.fromtimestamp(lock['acquired_at'], tz=timezone.utc).isoformat(timespec='seconds')} UTC). "
            "The button below is disabled until it finishes."
        )
        with st.expander("Stuck? Force-clear the lock (only if the process actually died)"):
            confirm_clear = st.checkbox("I've confirmed no run_monthly_from_db.py process is really running", key="confirm_clear_lock")
            if st.button("Force-clear lock", disabled=not confirm_clear):
                core.force_clear_report_lock()
                st.success("Lock cleared. Reload the page.")

    if st.button("Run report", type="primary", disabled=run_disabled):
        cmd = [python_bin(), "run_monthly_from_db.py", "-m", month_str]
        if arc_choice != "Both":
            cmd += ["--arc", arc_choice]
        if active_only:
            cmd += ["--active-only"]
        st.write(f"Running: `{' '.join(cmd)}`")
        log_box = st.empty()
        lines: list[str] = []

        def _on_line(line: str) -> None:
            lines.append(line)
            log_box.code("\n".join(lines[-400:]), language="text")

        before = {p.name for p in core.list_output_files()}
        with st.spinner("Fetching from ARC-1 / ARC-2 and building workbooks..."):
            try:
                result = core.run_report_subprocess(
                    cmd,
                    cwd=core.SCRIPT_DIR,
                    on_line=_on_line,
                    lock_meta={"month": month_str, "arc": arc_choice, "active_only": active_only},
                )
            except core.ReportAlreadyRunningError as exc:
                st.error(str(exc))
                result = None

        if result is not None:
            if result.timed_out:
                st.error(f"Report run exceeded the timeout and was killed. Last log lines:\n\n" + "\n".join(result.lines[-20:]))
            elif result.returncode == 0:
                st.success("Report finished.")
                st.session_state["last_run_params"] = {
                    "month": month_value, "arc": arc_choice, "active_only": active_only,
                }
                after_files = core.list_output_files()
                new_files = [p.name for p in after_files if p.name not in before] or [p.name for p in after_files[:2]]
                core.append_run_history(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "month": month_str,
                        "arc": arc_choice,
                        "active_only": active_only,
                        "tenant_count": len(preview_keys),
                        "tenants": preview_keys,
                        "files": new_files,
                    }
                )
            else:
                st.error(f"Report failed (exit code {result.returncode}). See log above.")

    st.divider()
    st.subheader("Existing report files")
    if lock:
        st.caption("A run is in progress — this list may include a file still being written.")
    files = core.list_output_files()
    if not files:
        st.info("No report files in output/ yet.")
    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{f.name}**  \nmodified {mtime}")
        c2.download_button(
            "Download", data=f.read_bytes(), file_name=f.name, key=f"dl-{f.name}", disabled=bool(lock),
        )

# ---------------------------------------------------------------------------
# Tab 3: verify login counts vs utilization peak counts
# ---------------------------------------------------------------------------
with tab_verify:
    st.subheader("Cross-check login counts against the utilization report")
    st.caption(
        "For each tenant/license type, the utilization peak count (max concurrent "
        "logins in an hour) and the distinct user_id count in the peak-hour login "
        "session query should always be equal — they describe the same number. "
        "Run this right after generating a month's report to catch any mismatch "
        "before it goes into a bill."
    )

    last_params = st.session_state.get("last_run_params", {})
    vc1, vc2, vc3 = st.columns(3)
    with vc1:
        v_month_value = st.date_input(
            "Month", value=last_params.get("month", date.today().replace(day=1)), key="verify_month"
        )
        v_month_str = f"{v_month_value.year:04d}-{v_month_value.month:02d}"
    with vc2:
        v_arc_choice = st.selectbox(
            "ARC", ["Both", "ARC-1", "ARC-2"],
            index=["Both", "ARC-1", "ARC-2"].index(last_params.get("arc", "Both")),
            key="verify_arc",
        )
    with vc3:
        v_active_only = st.checkbox("Active tenants only", value=last_params.get("active_only", False), key="verify_active_only")

    if st.button("Run cross-check", type="primary"):
        if not core.DB_CONFIG_PATH.exists():
            st.error("db_config.yaml not found — cannot connect to the DBs.")
        else:
            with st.spinner("Recomputing peaks and peak-hour login sessions from ARC-1 / ARC-2..."):
                try:
                    st.session_state["verify_results"] = core.verify_login_counts(v_month_str, v_arc_choice, v_active_only)
                except Exception as exc:
                    st.error(f"Verification failed: {exc}")

    verify_results = st.session_state.get("verify_results")
    if verify_results is not None:
        mismatches = [r for r in verify_results if not r["match"]]
        if mismatches:
            st.error(f"{len(mismatches)} mismatch(es) found between utilization peak count and login session count:")
            st.dataframe(mismatches, width="stretch", hide_index=True)
        else:
            st.success(f"All {len(verify_results)} tenant/license rows match — login counts are consistent.")
        with st.expander(f"Full cross-check detail ({len(verify_results)} rows)"):
            st.dataframe(verify_results, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 4: per-tenant campaign selection — full tenant vs. specific campaigns
# ---------------------------------------------------------------------------
with tab_campaigns:
    st.subheader("Which campaigns should each tenant's report cover?")
    st.caption(
        "Some tenants bill on their whole contact center; others only on "
        "specific campaigns. Pick a tenant, fetch its live campaign list from "
        "the DB, and choose full-tenant or a specific campaign set — this "
        "writes straight into tenants.yaml's campaign_ids."
    )

    plain = core.load_tenants_plain()
    tenants = plain.get("tenants", {}) or {}
    if not tenants:
        st.info("No tenants registered yet.")
    else:
        tenant_key = st.selectbox("Tenant", sorted(tenants.keys()), key="campaign_tenant")
        cfg = tenants[tenant_key]
        arc = cfg.get("arc")
        cc_id = cfg.get("contact_center_id")
        current_ids = [str(c) for c in (cfg.get("campaign_ids") or [])]

        c1, c2, c3 = st.columns(3)
        c1.metric("ARC", arc)
        c2.metric("contact_center_id", cc_id)
        c3.metric("Current mode", "Specific campaigns" if current_ids else "Full tenant")

        if cfg.get("use_simple_query"):
            st.warning(
                "This tenant has use_simple_query: true — campaign_ids is ignored "
                "for it (the usage query already filters by contact_center_id only). "
                "Saving a campaign selection here will have no effect until "
                "use_simple_query is turned off."
            )
        if current_ids:
            st.write(f"Current campaign_ids: `{', '.join(current_ids)}`")

        if cc_id is not None:
            siblings = core.find_cc_siblings(plain, arc, int(cc_id), exclude_key=tenant_key)
            if siblings:
                desc = "; ".join(f"{s['key']} ({', '.join(s['campaign_ids']) or 'full tenant'})" for s in siblings)
                st.info(
                    f"{len(siblings)} other tenant(s) also use {arc} / cc {cc_id}: {desc}. "
                    "If more than one of these has no campaign filter, they may double-count the same usage — worth a sanity check."
                )

        if st.button("Fetch campaign list from DB", key="fetch_campaigns_btn"):
            if not core.DB_CONFIG_PATH.exists():
                st.error("db_config.yaml not found — cannot connect to the DBs.")
            elif cc_id is None:
                st.error("This tenant has no contact_center_id set.")
            else:
                try:
                    st.session_state["campaigns_for_tenant"] = (tenant_key, core.fetch_campaigns_for_cc(arc, int(cc_id)))
                except Exception as exc:
                    st.error(f"Campaign fetch failed: {exc}")

        cached = st.session_state.get("campaigns_for_tenant")
        if cached and cached[0] == tenant_key:
            campaigns = cached[1]
            if not campaigns:
                st.info(f"No campaigns found in the DB for contact_center_id {cc_id}.")
            else:
                st.dataframe(campaigns, width="stretch", hide_index=True)

                def _label(c: dict[str, Any]) -> str:
                    tag = " (active 30d)" if c["active_last_30d"] else ""
                    name = f" — {c['name']}" if c["name"] else ""
                    return f"{c['campaign_id']}{name}{tag}"

                labels_by_id = {c["campaign_id"]: _label(c) for c in campaigns}
                mode = st.radio(
                    "Report scope for this tenant",
                    ["Full tenant (no campaign filter)", "Specific campaigns"],
                    index=1 if current_ids else 0,
                    key="campaign_mode",
                    horizontal=True,
                )
                selected_ids: list[str] = []
                if mode == "Specific campaigns":
                    default_selection = [c["campaign_id"] for c in campaigns if c["campaign_id"] in current_ids]
                    selected_labels = st.multiselect(
                        "Campaigns to include",
                        options=list(labels_by_id.values()),
                        default=[labels_by_id[cid] for cid in default_selection],
                        key="campaign_multiselect",
                    )
                    label_to_id = {v: k for k, v in labels_by_id.items()}
                    selected_ids = [label_to_id[label] for label in selected_labels]

                if st.button("Save campaign selection", type="primary"):
                    if mode == "Specific campaigns" and not selected_ids:
                        st.error("Pick at least one campaign, or switch to 'Full tenant'.")
                    else:
                        try:
                            core.set_tenant_campaigns(
                                tenant_key, selected_ids if mode == "Specific campaigns" else None
                            )
                        except (core.TenantValidationError, core.LockTimeoutError) as exc:
                            st.error(str(exc))
                        else:
                            st.success(f"Saved. '{tenant_key}' will use: {mode}.")

# ---------------------------------------------------------------------------
# Tab 5: full tenant registry — add / remove
# ---------------------------------------------------------------------------
with tab_tenants:
    st.subheader("Registered tenants")
    plain = core.load_tenants_plain()
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
    st.dataframe(rows, width="stretch", hide_index=True)
    st.caption(f"{len(rows)} tenants registered across ARC-1 + ARC-2.")

    st.divider()
    st.subheader("Add a tenant (full form)")
    st.caption("Writes a new entry into tenants.yaml, same file the CLI scripts read.")
    with st.form("add_tenant_form", clear_on_submit=False):
        key = st.text_input("Tenant key (unique, e.g. NEWCO_ARCH1)").strip().upper()
        c1, c2 = st.columns(2)
        with c1:
            arc = st.selectbox("ARC", core.ARC_CHOICES)
            contact_center_id = st.number_input("contact_center_id", min_value=1, step=1)
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
        if not sheet_name:
            st.error("sheet_name is required.")
        else:
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

            siblings = core.find_cc_siblings(plain, arc, int(contact_center_id))
            if siblings:
                desc = "; ".join(f"{s['key']} ({', '.join(s['campaign_ids']) or 'full tenant'})" for s in siblings)
                st.info(f"Heads up: {arc} / cc {contact_center_id} is also used by: {desc}.")

            try:
                core.add_tenant(key, entry)
            except (core.TenantValidationError, core.LockTimeoutError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Added '{key}' to tenants.yaml. Reload the page to see it in the table above.")

    st.divider()
    st.subheader("Remove a tenant")
    st.caption("For customers who were deleted / offboarded — removes the entry from tenants.yaml.")
    if tenants:
        remove_key = st.selectbox("Tenant to remove", sorted(tenants.keys()), key="remove_key")
        confirm = st.checkbox(f"I'm sure I want to remove '{remove_key}'", key="remove_confirm")
        if st.button("Remove tenant", disabled=not confirm):
            try:
                core.remove_tenant(remove_key)
            except (core.TenantValidationError, core.LockTimeoutError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Removed '{remove_key}' from tenants.yaml. Reload the page to refresh the table.")
    else:
        st.info("No tenants registered.")

# ---------------------------------------------------------------------------
# Tab 6: run history
# ---------------------------------------------------------------------------
with tab_history:
    st.subheader("Past runs triggered from this dashboard")
    history = core.load_run_history()
    if not history:
        st.info("No runs recorded yet. Run a report from tab 2 to start building history.")
    else:
        display_rows = [
            {
                "timestamp (UTC)": entry.get("timestamp"),
                "month": entry.get("month"),
                "arc": entry.get("arc"),
                "active_only": entry.get("active_only"),
                "tenant_count": entry.get("tenant_count"),
                "files": ", ".join(entry.get("files", [])),
            }
            for entry in reversed(history)
        ]
        st.dataframe(display_rows, width="stretch", hide_index=True)

        st.divider()
        st.subheader("Tenant count trend")
        trend = [
            {"month": e.get("month"), "arc": e.get("arc"), "tenant_count": e.get("tenant_count")}
            for e in history
        ]
        st.dataframe(trend, width="stretch", hide_index=True)

        with st.expander("Inspect tenant list for a past run"):
            labels = [f"{e.get('timestamp')} — {e.get('month')} ({e.get('arc')})" for e in history]
            pick = st.selectbox("Run", list(reversed(labels)))
            entry = history[len(labels) - 1 - labels[::-1].index(pick)]
            st.write(entry.get("tenants", []))
