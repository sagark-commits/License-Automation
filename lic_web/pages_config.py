"""Tenant registry, global user-type defaults and database settings pages (mixin for App)."""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ui
from .auth import can
from .validation import LICENSE_KEYS, parse_lines, validate_db_arc, validate_defaults, validate_tenant

BUCKETS = LICENSE_KEYS
BUCKET_TITLE = {"agent": "Agent", "supervisor": "Supervisor", "wallboard": "Wallboard"}


def engine_config(registry: Dict[str, Any]) -> Dict[str, Any]:
    """The same dict tmone_report.load_config() builds - without needing the file or pandas."""
    d = registry.get("defaults", {})
    return {
        "defaults": d, "tenants": registry.get("tenants", {}),
        "agent_types": list(d.get("agent_user_types", [])),
        "supervisor_types": list(d.get("supervisor_user_types", [])),
        "wallboard_types": list(d.get("wallboard_user_types", [])),
        "ignored_types": list(d.get("ignored_user_types", ["Administrator"])),
        "license_labels": d.get("license_labels", {"agent": "Professional-Agent", "supervisor": "Supervisor", "wallboard": "Wallboard User"}),
        "tenant_agent_label": d.get("tenant_sheet_agent_label", "Professional-Agent"),
    }


def state_from_cfg(key: str, cfg: Dict[str, Any], reg: Dict[str, Any]) -> Dict[str, Any]:
    d = reg.get("defaults", {})
    ls = cfg.get("login_sheets") or {}
    st: Dict[str, Any] = {
        "key": key, "project_name": str(cfg.get("project_name", "")), "sheet_name": str(cfg.get("sheet_name", "")), "arc": cfg.get("arc", ""),
        "cc": "" if cfg.get("contact_center_id") is None else str(cfg.get("contact_center_id")), "enabled": cfg.get("enabled", True) is not False,
        "campaigns": ", ".join(str(c) for c in (cfg.get("campaign_ids") or [])), "query_mode": "simple" if cfg.get("use_simple_query") else "auto",
        "swc": bool(cfg.get("supervisors_without_campaign")), "wb_id": cfg.get("wallboard_user_id") or "", "wb_pat": cfg.get("wallboard_user_pattern") or "",
        "fetch_wb": cfg.get("fetch_wallboard", True) is not False, "label": cfg.get("agent_display_label") or "",
        "ls_agent": ls.get("agent", ""), "ls_supervisor": ls.get("supervisor", ""), "ls_wallboard": ls.get("wallboard", ""),
    }
    for b in BUCKETS:
        custom = cfg.get(b + "_user_types")
        st[b + "_mode"] = "custom" if custom else "default"
        st[b + "_types"] = "\n".join(custom or d.get(b + "_user_types", []))
    return st


def state_from_form(req) -> Dict[str, Any]:
    st: Dict[str, Any] = {
        "key": req.f("key").strip().upper(), "project_name": req.f("project_name").replace("\r\n", "\n").strip(), "sheet_name": req.f("sheet_name").strip(), "arc": req.f("arc"),
        "cc": req.f("contact_center_id").strip(), "enabled": req.f("enabled") == "1", "campaigns": req.f("campaign_ids").strip(),
        "query_mode": req.f("query_mode", "auto"), "swc": req.f("supervisors_without_campaign") == "1", "wb_id": req.f("wallboard_user_id").strip(),
        "wb_pat": req.f("wallboard_user_pattern").strip(), "fetch_wb": req.f("fetch_wallboard") == "1", "label": req.f("agent_display_label").strip(),
        "ls_agent": req.f("ls_agent").strip(), "ls_supervisor": req.f("ls_supervisor").strip(), "ls_wallboard": req.f("ls_wallboard").strip(),
    }
    for b in BUCKETS:
        st[b + "_mode"] = req.f(b + "_mode", "default")
        st[b + "_types"] = req.f(b + "_user_types")
    return st


def cfg_from_state(st: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    errs: List[str] = []
    cfg = copy.deepcopy(existing) if existing else {}
    cfg["arc"], cfg["project_name"], cfg["sheet_name"] = st["arc"], st["project_name"], st["sheet_name"]
    try:
        cfg["contact_center_id"] = int(st["cc"])
    except ValueError:
        errs.append("Contact center id must be a whole number.")
        cfg["contact_center_id"] = None

    def setorpop(k: str, v: Any) -> None:
        if v in (None, "", [], False):
            cfg.pop(k, None)
        else:
            cfg[k] = v

    setorpop("enabled", None if st["enabled"] else False)
    camps = parse_lines(st["campaigns"])
    setorpop("campaign_ids", camps)
    setorpop("use_simple_query", True if st["query_mode"] == "simple" else None)
    setorpop("supervisors_without_campaign", True if st["swc"] else None)
    setorpop("wallboard_user_id", st["wb_id"])
    setorpop("wallboard_user_pattern", st["wb_pat"])
    setorpop("fetch_wallboard", None if st["fetch_wb"] else False)
    setorpop("agent_display_label", st["label"])
    ls = {k: v for k, v in (cfg.get("login_sheets") or {}).items() if k not in BUCKETS}
    for b in BUCKETS:
        if st["ls_" + b]:
            ls[b] = st["ls_" + b]
    setorpop("login_sheets", ls)
    for b in BUCKETS:
        if st[b + "_mode"] == "custom":
            items = parse_lines(st[b + "_types"])
            if not items:
                errs.append("%s: you chose a custom list but left it empty. Choose 'global default' or enter at least one user type." % BUCKET_TITLE[b])
            cfg[b + "_user_types"] = items
        else:
            cfg.pop(b + "_user_types", None)
    cfg.setdefault("csv_patterns", [st["sheet_name"] or st["key"]])
    cfg.setdefault("folder_patterns", [])
    return cfg, errs


class ConfigPages:
    """Mixed into App: needs self.data, self.jobs, self.csrf(), self.page(), req.flash()."""

    def config_routes(self) -> List[Tuple[str, str, Callable, Optional[str]]]:
        return [
            ("GET", r"^/tenants$", self.h_tenants, "view"),
            ("GET", r"^/tenants/new$", self.h_tenant_new, "tenants"),
            ("POST", r"^/tenants/new$", self.h_tenant_save_new, "tenants"),
            ("POST", r"^/tenants/discover$", self.h_tenant_discover, "tenants"),
            ("GET", r"^/tenants/(?P<key>[A-Z][A-Z0-9_]{1,39})$", self.h_tenant_edit, "view"),
            ("POST", r"^/tenants/(?P<key>[A-Z][A-Z0-9_]{1,39})$", self.h_tenant_save, "tenants"),
            ("POST", r"^/tenants/(?P<key>[A-Z][A-Z0-9_]{1,39})/toggle$", self.h_tenant_toggle, "tenants"),
            ("POST", r"^/tenants/(?P<key>[A-Z][A-Z0-9_]{1,39})/delete$", self.h_tenant_delete, "users"),
            ("GET", r"^/tenants/(?P<key>[A-Z][A-Z0-9_]{1,39})/sql$", self.h_tenant_sql, "view"),
            ("GET", r"^/settings$", self.h_settings, "settings"),
            ("POST", r"^/settings$", self.h_settings_save, "settings"),
            ("POST", r"^/settings/restore$", self.h_settings_restore, "users"),
            ("GET", r"^/settings/db$", self.h_db, "db"),
            ("POST", r"^/settings/db$", self.h_db_save, "db"),
            ("POST", r"^/settings/db/test$", self.h_db_test, "db"),
        ]

    # ------------------------------------------------------------------ tenants list
    def h_tenants(self, req) -> "Any":
        reg = self.data.load_registry()
        last = (self.jobs.last_done() or {}).get("result") or {}
        peaks = {t["key"]: t for t in last.get("tenants", [])}
        warn_keys = {t["key"] for t in last.get("tenants", []) if t.get("warnings") or t.get("errors")}
        editor = can(req.user["role"], "tenants")
        rows = []
        for k, c in reg["tenants"].items():
            custom = [b[:3] for b in BUCKETS if c.get(b + "_user_types")]
            ag = (peaks.get(k, {}).get("peaks", {}).get("agent") or {}).get("count")
            enabled = c.get("enabled", True) is not False
            actions = ""
            if editor:
                actions = ('<a class="btn sm sec" href="/tenants/%s">Edit</a> <form class="inl" method="post" action="/tenants/%s/toggle">%s<button class="sm sec">%s</button></form>') % (
                    ui.esc(k), ui.esc(k), self.csrf(req), "Disable" if enabled else "Enable")
            else:
                actions = '<a class="btn sm sec" href="/tenants/%s">View</a>' % ui.esc(k)
            rows.append([
                '<b>%s</b>%s' % (ui.esc(k), ' <span title="warnings in the latest report" class="badge b-warn">!</span>' if k in warn_keys else ""),
                ui.esc(str(c.get("project_name", "")).replace("\n", " ")), ui.esc(c.get("arc")), ui.esc(c.get("contact_center_id")),
                ui.esc("campaigns " + ", ".join(map(str, c["campaign_ids"]))) if c.get("campaign_ids") else '<span class="mute">whole contact center</span>',
                ("custom: " + ", ".join(custom)) if custom else '<span class="mute">global default</span>',
                ui.badge("on", "ok") if enabled else ui.badge("off", "off"), ui.esc(ag) if ag is not None else '<span class="mute">-</span>', actions])
        add = '<a class="btn" href="/tenants/new">Add tenant</a>' if editor else ""
        body = ('<div class="row"><div><h1>Tenants</h1><p class="sub">Every contact center / campaign that appears in the monthly report. %d tenants.</p></div><div class="right">%s</div></div>'
                '<div class="card"><div class="f"><input type="text" id="tfilter" placeholder="Filter tenants..."></div>%s</div>') % (
            len(reg["tenants"]), add, ui.table(["Key", "Project", "ARC", "#CC id", "Scope", "Ameyo user types", "In reports", "#Agent peak (last)", ""], rows, table_id="ttable"))
        return self.page(req, "Tenants", body, "tenants")

    # ------------------------------------------------------------------ tenant form
    def _tenant_form_html(self, req, st: Dict[str, Any], reg: Dict[str, Any], *, is_new: bool, errors: List[str], warnings: List[str]) -> str:
        arcs = self.data.arcs()
        d = reg.get("defaults", {})
        editor = can(req.user["role"], "tenants")
        dis = "" if editor else " disabled"
        key = st["key"]
        notes = ""
        last = (self.jobs.last_done() or {}).get("result") or {}
        t_last = next((t for t in last.get("tenants", []) if t["key"] == key), None)
        if t_last and (t_last.get("warnings") or t_last.get("errors")):
            notes = ui.errors_box(t_last.get("errors", []) + t_last.get("warnings", []), "warn", "From the latest report (%s):" % last.get("month"))
        cols = ""
        for b in BUCKETS:
            default_txt = ", ".join(d.get(b + "_user_types", [])) or "(none)"
            cols += ('<div class="f"><label>%s user types</label><select name="%s_mode" data-types="%s_user_types"%s><option value="default"%s>Use the global default (%s)</option>'
                     '<option value="custom"%s>Custom list for this tenant</option></select><textarea id="%s_user_types" name="%s_user_types" spellcheck="false"%s>%s</textarea></div>') % (
                BUCKET_TITLE[b], b, b, dis, " selected" if st[b + "_mode"] == "default" else "", ui.esc(default_txt),
                " selected" if st[b + "_mode"] == "custom" else "", b, b, dis, ui.esc(st[b + "_types"]))
        key_field = ui.field("Key", "key", key, hint="Short unique id, capital letters/digits/underscore (e.g. ACME_ARCH1). Cannot be changed later.", extra="" if is_new else "readonly", cls="s") if is_new else \
            '<div class="f s"><label>Key</label><b>%s</b></div>' % ui.esc(key)
        disc = ""
        if editor:
            disc = ('<button type="button" id="discover-btn" class="sec">Find from database</button> <span class="hint">Reads the contact center from Ameyo (read-only): its name, the user types it really uses, campaigns and wallboard logins.</span>'
                    '<div class="disc" id="disc"></div>')
        form = ('<form method="post" action="%s" autocomplete="off">%s'
                '<div class="card"><h2>1. Which contact center?</h2><div class="row">%s%s%s</div><div class="row">%s%s%s</div>%s</div>'
                '<div class="card"><h2>2. Which Ameyo user types count as which licence?</h2><p class="hint">These are the exact values of <code>user_type</code> in Ameyo\'s <code>users</code> table. '
                'Upper/lower case and space/hyphen/underscore are ignored when matching. A custom list <b>replaces</b> the global default for this tenant only. '
                'A user type that matches none of the three lists is <b>not counted</b>, and the report warns about it.</p><div class="row">%s</div></div>'
                '<div class="card"><h2>3. Scope and counting rules</h2><div class="row">%s%s</div>'
                '<label class="check"><input type="checkbox" id="supervisors_without_campaign" name="supervisors_without_campaign" value="1"%s%s> Count supervisors even when they have no campaign</label>'
                '<div class="hint mb">Off (default) = only supervisors working on a campaign count, like the old reports. Only possible when the tenant has no campaign ids '
                '(otherwise two tenants on the same contact center would count the same supervisors).</div>'
                '<div class="row">%s%s</div><label class="check"><input type="checkbox" name="fetch_wallboard" value="1"%s%s> Look for wallboard logins</label></div>'
                '<div class="card"><h2>4. Names in the workbooks</h2><div class="row">%s</div>'
                '<p class="hint">Login-evidence sheet names. Leave blank to have them generated automatically (e.g. Supervisor_&lt;tenant sheet&gt;). Max 31 characters.</p><div class="row">%s%s%s</div></div>'
                '<div class="card"><label class="check"><input type="checkbox" name="enabled" value="1"%s%s> Include this tenant in monthly reports</label></div>%s</form>') % (
            "/tenants/new" if is_new else "/tenants/" + ui.esc(key), self.csrf(req),
            key_field,
            '<div class="f"><label for="project_name">Project name (shown in the Summary)</label><textarea id="project_name" name="project_name" rows="2" class="plain"%s>%s</textarea>'
            '<div class="hint">Optional second line, e.g. the ARC label (as in existing reports).</div></div>' % (dis, ui.esc(st["project_name"])),
            ui.field("Tenant sheet name", "sheet_name", st["sheet_name"], hint='<span id="sheet_count"></span> characters; no [ ] : * ? / \\', extra=dis),
            ui.select("ARC (which Ameyo database)", "arc", arcs, st["arc"], cls="s", extra=dis),
            ui.field("Contact center id", "contact_center_id", st["cc"], hint="From Ameyo (contact_center.id)", cls="s", extra=dis),
            ui.field("Agent display label", "agent_display_label", st["label"], hint="Optional: how the agent licence is named in this tenant's rows (e.g. Ameyo Pro-Dialer). Blank = default.", extra=dis),
            disc, cols,
            ui.field("Campaign ids", "campaign_ids", st["campaigns"], hint="Comma separated. Blank = the whole contact center. Use this when several tenants share one contact center.", extra=dis),
            ui.select("Which sessions count", "query_mode", [("auto", "Auto - only sessions linked to a campaign (default)"), ("simple", "Simple - every session of the contact center")], st["query_mode"], extra=dis),
            " checked" if st["swc"] else "", dis,
            ui.field("Wallboard user id (exact)", "wallboard_user_id", st["wb_id"], hint="Only if the wallboard login has an unusual name.", extra=dis),
            ui.field("or wallboard pattern", "wallboard_user_pattern", st["wb_pat"], hint="Default %wallboard% (any user id containing 'wallboard').", extra=dis),
            " checked" if st["fetch_wb"] else "", dis,
            "",
            ui.field("Agent sheet", "ls_agent", st["ls_agent"], extra=dis), ui.field("Supervisor sheet", "ls_supervisor", st["ls_supervisor"], extra=dis), ui.field("Wallboard sheet", "ls_wallboard", st["ls_wallboard"], extra=dis),
            " checked" if st["enabled"] else "", dis,
            '<div class="row"><button type="submit">%s</button> <a class="btn sec" href="/tenants">Cancel</a></div>' % ("Add tenant" if is_new else "Save changes") if editor else
            '<a class="btn sec" href="/tenants">Back</a>')
        return notes + ui.errors_box(errors) + ui.errors_box(warnings, "warn", "Please note:") + form

    def h_tenant_new(self, req) -> "Any":
        reg = self.data.load_registry()
        st = state_from_cfg("", {"arc": self.data.arcs()[0], "login_sheets": {}}, reg)
        st["cc"] = ""
        body = '<h1>Add a tenant</h1><p class="sub">A new contact center or campaign for the monthly report.</p>' + self._tenant_form_html(req, st, reg, is_new=True, errors=[], warnings=[])
        return self.page(req, "Add tenant", body, "tenants")

    def h_tenant_save_new(self, req) -> "Any":
        reg = self.data.load_registry()
        st = state_from_form(req)
        cfg, errs = cfg_from_state(st, None)
        verrs, warns = validate_tenant(st["key"], cfg, reg, is_new=True, arcs=self.data.arcs())
        errs += [e for e in verrs if e not in errs]
        if errs:
            body = '<h1>Add a tenant</h1>' + self._tenant_form_html(req, st, reg, is_new=True, errors=errs, warnings=warns)
            return self.page(req, "Add tenant", body, "tenants", status=400)
        reg["tenants"][st["key"]] = cfg
        self.data.save_registry(reg, req.user["username"], "added tenant %s" % st["key"])
        req.flash("ok", "Tenant %s added. It will be included in the next report." % st["key"])
        return _redirect("/tenants/" + st["key"])

    def _get_tenant(self, key: str):
        reg = self.data.load_registry()
        if key not in reg["tenants"]:
            from .app import HttpError
            raise HttpError(404, "No such tenant.")
        return reg, reg["tenants"][key]

    def h_tenant_edit(self, req, key: str) -> "Any":
        reg, cfg = self._get_tenant(key)
        st = state_from_cfg(key, cfg, reg)
        _, warns = validate_tenant(key, cfg, reg, is_new=False, arcs=self.data.arcs())
        sql_link = '<a class="btn sm sec" href="/tenants/%s/sql">Show the SQL for this tenant</a>' % ui.esc(key)
        delete = ""
        if can(req.user["role"], "users"):
            delete = ('<form class="inl" method="post" action="/tenants/%s/delete" data-confirm="1">%s<button class="bad sm" data-confirm="1">Delete tenant</button></form>') % (ui.esc(key), self.csrf(req))
        body = '<div class="row"><div><h1>%s</h1><p class="sub">%s</p></div><div class="right">%s %s</div></div>%s' % (
            ui.esc(key), ui.esc(str(cfg.get("project_name", "")).replace("\n", " ")), sql_link, delete,
            self._tenant_form_html(req, st, reg, is_new=False, errors=[], warnings=warns))
        return self.page(req, "Tenant " + key, body, "tenants")

    def h_tenant_save(self, req, key: str) -> "Any":
        reg, existing = self._get_tenant(key)
        st = state_from_form(req)
        st["key"] = key
        cfg, errs = cfg_from_state(st, existing)
        verrs, warns = validate_tenant(key, cfg, reg, is_new=False, arcs=self.data.arcs())
        errs += [e for e in verrs if e not in errs]
        if errs:
            body = '<h1>%s</h1>' % ui.esc(key) + self._tenant_form_html(req, st, reg, is_new=False, errors=errs, warnings=warns)
            return self.page(req, "Tenant " + key, body, "tenants", status=400)
        changed = sorted(k for k in set(cfg) | set(existing) if cfg.get(k) != existing.get(k))
        reg["tenants"][key] = cfg
        self.data.save_registry(reg, req.user["username"], "edited tenant %s (%s)" % (key, ", ".join(changed) or "no change"))
        req.flash("ok", "Saved %s." % key)
        return _redirect("/tenants/" + key)

    def h_tenant_toggle(self, req, key: str) -> "Any":
        reg, cfg = self._get_tenant(key)
        if cfg.get("enabled", True) is False:
            cfg.pop("enabled", None)
        else:
            cfg["enabled"] = False
        self.data.save_registry(reg, req.user["username"], "%s tenant %s" % ("disabled" if cfg.get("enabled") is False else "enabled", key))
        req.flash("ok", "%s is now %s." % (key, "excluded from reports" if cfg.get("enabled") is False else "included in reports"))
        return _redirect("/tenants")

    def h_tenant_delete(self, req, key: str) -> "Any":
        reg, _ = self._get_tenant(key)
        del reg["tenants"][key]
        self.data.save_registry(reg, req.user["username"], "deleted tenant %s" % key)
        req.flash("ok", "Tenant %s deleted (a backup of the previous registry is under Settings)." % key)
        return _redirect("/tenants")

    def h_tenant_sql(self, req, key: str) -> "Any":
        from .sqlpreview import preview_for_tenant
        reg, cfg = self._get_tenant(key)
        month = req.q("month") or __import__("datetime").date.today().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            month = __import__("datetime").date.today().strftime("%Y-%m")
        blocks = "".join('<div class="card"><h2>%s</h2><pre class="log full">%s</pre></div>' % (ui.esc(t), ui.esc(s)) for t, s in preview_for_tenant(key, cfg, engine_config(reg), month))
        body = '<h1>SQL for %s</h1><p class="sub">Exactly what the report runs against the Ameyo <code>oneproduct</code> database (read-only). <a href="/tenants/%s">Back to the tenant</a></p>%s' % (ui.esc(key), ui.esc(key), blocks)
        return self.page(req, "SQL " + key, body, "tenants")

    def h_tenant_discover(self, req) -> "Any":
        from .app import json_response
        from . import discovery
        data = req.json_body()
        arc, cc = data.get("arc"), data.get("cc")
        if arc not in self.data.arcs() or not isinstance(cc, int) or isinstance(cc, bool) or cc < 0:
            return json_response({"error": "Choose an ARC and enter a whole-number contact center id."}, 400)
        try:
            settings = self.data.db_settings_for_connect(arc)
            from db_connections import connect_database
            conn = connect_database(settings)
        except Exception as exc:
            return json_response({"error": "Cannot connect to %s: %s" % (arc, (str(exc).strip().splitlines() or ["error"])[0])})
        try:
            result = discovery.discover_contact_center(conn, cc, engine_config(self.data.load_registry()))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.data.audit(req.user["username"], "tenant.discover", "%s cc=%s" % (arc, cc), req.ip)
        return json_response(result)

    # ------------------------------------------------------------------ global settings
    def h_settings(self, req) -> "Any":
        reg = self.data.load_registry()
        d = reg["defaults"]
        labels = d.get("license_labels", {})
        last = (self.jobs.last_done() or {}).get("result") or {}
        unc: Dict[str, List[str]] = {}
        for t in last.get("tenants", []):
            for ut in t.get("unclassified", {}):
                unc.setdefault(ut, []).append(t["key"])
        unc_html = ""
        if unc:
            unc_html = ui.alert("warn", "<b>User types seen in the latest report that count for nothing:</b><ul>%s</ul>"
                                "Add them below (or to a tenant's own list) if they should be licensed, or add them to 'Ignored' if they never are." % "".join(
                                    "<li><code>%s</code> in %s</li>" % (ui.esc(u), ui.esc(", ".join(sorted(set(ks))))) for u, ks in sorted(unc.items())))
        form = ('<form method="post" action="/settings">%s<div class="card"><h2>Ameyo user types - global defaults</h2><p class="hint">One per line. Used by every tenant that does not have its own list. '
                'Matching ignores case and treats space/hyphen/underscore the same.</p><div class="row">'
                '<div class="f"><label>Agent</label><textarea name="agent_user_types">%s</textarea></div>'
                '<div class="f"><label>Supervisor</label><textarea name="supervisor_user_types">%s</textarea></div>'
                '<div class="f"><label>Wallboard</label><textarea name="wallboard_user_types">%s</textarea></div>'
                '<div class="f"><label>Ignored (never licensed)</label><textarea name="ignored_user_types">%s</textarea><div class="hint">No warning is raised for these.</div></div></div></div>'
                '<div class="card"><h2>Names used in the workbooks</h2><div class="row">%s%s%s</div></div>'
                '<div class="card"><h2>Report history</h2><div class="row">%s</div></div><button type="submit">Save settings</button></form>') % (
            self.csrf(req), ui.esc("\n".join(d.get("agent_user_types", []))), ui.esc("\n".join(d.get("supervisor_user_types", []))),
            ui.esc("\n".join(d.get("wallboard_user_types", []))), ui.esc("\n".join(d.get("ignored_user_types", ["Administrator"]))),
            ui.field("Agent licence label", "label_agent", labels.get("agent", "Professional-Agent")), ui.field("Supervisor label", "label_supervisor", labels.get("supervisor", "Supervisor")),
            ui.field("Wallboard label", "label_wallboard", labels.get("wallboard", "Wallboard User")),
            ui.field("How many finished reports to keep", "keep_runs", str(self.data.get_settings().get("keep_runs", 24)), type_="number", extra='min="3" max="200"', cls="s"))
        hist = self.data.list_history()
        hist_html = ""
        if can(req.user["role"], "users") and hist:
            hist_html = ('<div class="card"><h2>Restore an earlier tenant registry</h2><p class="hint">A copy is saved before every change. Restoring replaces tenants and defaults (itself saved as a new backup).</p>'
                         '<form method="post" action="/settings/restore">%s<div class="row">%s<button class="sec" data-confirm="1">Restore</button></div></form></div>') % (
                self.csrf(req), ui.select("Backup", "name", hist[:30], hist[0]))
        body = '<h1>Settings</h1><p class="sub">Rules that apply to all tenants unless a tenant overrides them.</p>%s%s%s' % (unc_html, form, hist_html)
        return self.page(req, "Settings", body, "settings")

    def h_settings_save(self, req) -> "Any":
        reg = self.data.load_registry()
        d = reg["defaults"]
        for b in BUCKETS:
            d[b + "_user_types"] = parse_lines(req.f(b + "_user_types"))
        d["ignored_user_types"] = parse_lines(req.f("ignored_user_types"))
        d["license_labels"] = {"agent": req.f("label_agent").strip(), "supervisor": req.f("label_supervisor").strip(), "wallboard": req.f("label_wallboard").strip()}
        d["tenant_sheet_agent_label"] = d["license_labels"]["agent"]
        errs = validate_defaults(d)
        try:
            keep = int(req.f("keep_runs", "24"))
            if not 3 <= keep <= 200:
                raise ValueError
        except ValueError:
            errs.append("Keep between 3 and 200 reports.")
            keep = 24
        # a changed default must not create a conflict inside a tenant's own lists
        from user_type_config import type_conflicts
        cfg_engine = engine_config(reg)
        for k, c in reg["tenants"].items():
            for problem in type_conflicts(c, cfg_engine):
                errs.append("Tenant %s: %s" % (k, problem))
        if errs:
            req.flash("bad", "Not saved: " + " ".join(errs))
            return _redirect("/settings")
        self.data.save_registry(reg, req.user["username"], "edited global defaults")
        self.data.save_settings({"keep_runs": keep}, req.user["username"])
        self.jobs.keep_runs = keep
        req.flash("ok", "Settings saved.")
        return _redirect("/settings")

    def h_settings_restore(self, req) -> "Any":
        try:
            self.data.restore_history(req.f("name"), req.user["username"])
            req.flash("ok", "Registry restored from %s." % req.f("name"))
        except ValueError as e:
            req.flash("bad", str(e))
        return _redirect("/settings")

    # ------------------------------------------------------------------ database settings (admin)
    def h_db(self, req) -> "Any":
        view = self.data.db_view()
        cards = ""
        for arc in self.data.arcs():
            v = view.get(arc, {"host": "", "port": 5432, "name": "oneproduct", "user": "", "has_password": False, "sslmode": "", "statement_timeout_ms": 600000})
            ready = v["host"] and v["user"] and v["has_password"]
            cards += ('<div class="card"><div class="row"><h2>%s</h2> %s</div><form method="post" action="/settings/db">%s<input type="hidden" name="arc" value="%s">'
                      '<div class="row">%s%s%s</div><div class="row">%s%s%s%s</div>'
                      '<div class="row"><button type="submit">Save %s</button> <button type="button" class="sec" data-test-arc="%s">Test saved connection</button> <span class="small" data-test-out="%s"></span></div></form></div>') % (
                ui.esc(arc), ui.badge("ready", "ok") if ready else ui.badge("incomplete", "warn"), self.csrf(req), ui.esc(arc),
                ui.field("Host", "host", v["host"], hint="Database server of this ARC"), ui.field("Port", "port", str(v["port"]), type_="number", cls="s"),
                ui.field("Database", "name", v["name"], cls="s"),
                ui.field("User", "user", v["user"], hint="A read-only role is recommended."),
                ui.field("Password", "password", "", type_="password", extra='autocomplete="new-password" placeholder="%s"' % ("(unchanged)" if v["has_password"] else "(not set)"), hint="Stored in config/db_config.yaml (mode 600). Leave blank to keep."),
                ui.select("SSL", "sslmode", [("", "default"), ("disable", "disable"), ("prefer", "prefer"), ("require", "require")], v["sslmode"], cls="s"),
                ui.field("Query time limit (s)", "timeout_s", str(v["statement_timeout_ms"] // 1000), type_="number", cls="s"),
                ui.esc(arc), ui.esc(arc), ui.esc(arc))
        body = ('<h1>Databases</h1><p class="sub">Where the report reads Ameyo data (read-only connections, one per ARC). Save first, then test - the test uses the saved values.</p>%s') % cards
        return self.page(req, "Databases", body, "db")

    def h_db_save(self, req) -> "Any":
        arc = req.f("arc")
        if arc not in self.data.arcs():
            from .app import HttpError
            raise HttpError(400, "Unknown ARC")
        try:
            port = int(req.f("port"))
            timeout = int(req.f("timeout_s", "600")) * 1000
        except ValueError:
            req.flash("bad", "Port and time limit must be numbers.")
            return _redirect("/settings/db")
        s = {"host": req.f("host").strip(), "port": port, "name": req.f("name").strip(), "user": req.f("user").strip(),
             "password": req.f("password"), "sslmode": req.f("sslmode"), "statement_timeout_ms": timeout}
        errs = validate_db_arc(arc, s)
        if errs:
            req.flash("bad", " ".join(errs))
            return _redirect("/settings/db")
        self.data.save_db(arc, s, req.user["username"])
        req.flash("ok", "%s saved. Use 'Test saved connection' to check it." % arc)
        return _redirect("/settings/db")

    def h_db_test(self, req) -> "Any":
        from .app import json_response
        from . import discovery
        arc = req.json_body().get("arc")
        if arc not in self.data.arcs():
            return json_response({"ok": False, "message": "Unknown ARC"}, 400)
        try:
            settings = self.data.db_settings_for_connect(arc)
        except KeyError:
            return json_response({"ok": False, "message": "Not configured yet - save the settings first."})
        res = discovery.test_connection(settings)
        self.data.audit(req.user["username"], "db.test", "%s ok=%s" % (arc, res["ok"]), req.ip)
        return json_response(res)


def _redirect(url: str):
    from .app import redirect
    return redirect(url)
