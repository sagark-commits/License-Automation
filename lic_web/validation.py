"""Validation of tenant records and global settings (everything the UI lets a person edit)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from login_sheets import MAX_SHEET_LEN

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
RESERVED_SHEETS = {"dashboard", "summary", "history"}
LICENSE_KEYS = ("agent", "supervisor", "wallboard")
TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,59}$")


def parse_lines(text: Any) -> List[str]:
    """Split a textarea / comma list into unique, trimmed, non-empty items (order kept)."""
    if isinstance(text, (list, tuple)):
        raw = [str(x) for x in text]
    else:
        raw = re.split(r"[\r\n,;]+", str(text or ""))
    out: List[str] = []
    seen = set()
    for item in raw:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def check_sheet_name(name: str, what: str) -> Optional[str]:
    if not name or not name.strip():
        return "%s: sheet name is empty" % what
    if len(name) > MAX_SHEET_LEN:
        return "%s: sheet name '%s' is longer than %d characters" % (what, name, MAX_SHEET_LEN)
    if BAD_SHEET_CHARS.search(name):
        return "%s: sheet name '%s' contains one of []:*?/\\" % (what, name)
    if name.lower() in RESERVED_SHEETS:
        return "%s: '%s' is a reserved sheet name" % (what, name)
    if name.startswith("'") or name.endswith("'"):
        return "%s: sheet name must not start or end with an apostrophe" % what
    return None


def check_type_list(items: List[str], what: str) -> List[str]:
    errs = []
    for t in items:
        if not TYPE_RE.match(t):
            errs.append("%s: '%s' is not a valid Ameyo user type (letters, digits, space, - _ . only, max 60)" % (what, t))
    return errs


def validate_tenant(
    key: str,
    cfg: Dict[str, Any],
    registry: Dict[str, Any],
    *,
    is_new: bool,
    arcs: List[str],
) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). Errors block saving; warnings are shown but allowed."""
    errors: List[str] = []
    warnings: List[str] = []
    tenants: Dict[str, Dict[str, Any]] = registry.get("tenants", {})

    if not KEY_RE.match(key or ""):
        errors.append("Key must be 2-40 characters: capital letters, digits and underscore, starting with a letter (e.g. ACME_ARCH1).")
    elif is_new and key in tenants:
        errors.append("A tenant with key %s already exists." % key)

    arc = cfg.get("arc")
    if arc not in arcs:
        errors.append("ARC must be one of: %s." % ", ".join(arcs))

    project = str(cfg.get("project_name", "")).strip()
    if not project:
        errors.append("Project name is required.")
    elif len(project) > 80:
        errors.append("Project name is longer than 80 characters.")

    sheet = str(cfg.get("sheet_name", "")).strip()
    msg = check_sheet_name(sheet, "Tenant sheet")
    if msg:
        errors.append(msg)
    else:
        for k, c in tenants.items():
            if k != key and str(c.get("sheet_name", k)).strip().lower() == sheet.lower():
                errors.append("Sheet name '%s' is already used by tenant %s." % (sheet, k))

    cc = cfg.get("contact_center_id")
    if not isinstance(cc, int) or isinstance(cc, bool) or cc < 0:
        errors.append("Contact center id must be a whole number (the id in the Ameyo contact_center table).")

    campaigns = cfg.get("campaign_ids") or []
    for c in campaigns:
        if not re.fullmatch(r"\d{1,9}", str(c)):
            errors.append("Campaign id '%s' is not a number." % c)

    for bucket in LICENSE_KEYS:
        items = cfg.get("%s_user_types" % bucket) or []
        errors.extend(check_type_list(items, "%s user types" % bucket.title()))
    seen: Dict[str, str] = {}
    for bucket in LICENSE_KEYS:
        for t in cfg.get("%s_user_types" % bucket) or []:
            k = re.sub(r"[\s_\-]+", "-", t.strip().lower())
            if k in seen and seen[k] != bucket:
                errors.append("User type '%s' is listed as both %s and %s - a type can count in one licence only." % (t, seen[k], bucket))
            seen.setdefault(k, bucket)

    wb_id = cfg.get("wallboard_user_id")
    if wb_id and not re.fullmatch(r"[A-Za-z0-9_.\-@]{1,80}", str(wb_id)):
        errors.append("Wallboard user id may contain letters, digits and _ . - @ only.")
    wb_pat = cfg.get("wallboard_user_pattern")
    if wb_pat and not re.fullmatch(r"[A-Za-z0-9_.\-@%]{1,80}", str(wb_pat)):
        errors.append("Wallboard pattern may contain letters, digits, _ . - @ and % only.")

    label = cfg.get("agent_display_label")
    if label and len(str(label)) > 40:
        errors.append("Agent display label is longer than 40 characters.")

    if cfg.get("supervisors_without_campaign") and campaigns:
        errors.append("'Count supervisors without a campaign' cannot be used together with campaign ids: "
                      "tenants that share a contact center would count the same supervisors twice.")

    # login sheets: valid + unique across every tenant
    used: Dict[str, str] = {}
    for k, c in tenants.items():
        if k == key:
            continue
        for lic, name in (c.get("login_sheets") or {}).items():
            used[str(name).lower()] = k
        used.setdefault(str(c.get("sheet_name", k)).lower(), k)
    own: Dict[str, str] = {}
    for lic, name in (cfg.get("login_sheets") or {}).items():
        if lic not in LICENSE_KEYS + ("executive",):
            errors.append("Unknown login sheet type '%s'." % lic)
            continue
        msg = check_sheet_name(str(name), "%s login sheet" % lic.title())
        if msg:
            errors.append(msg)
            continue
        low = str(name).lower()
        if low in used:
            errors.append("Login sheet name '%s' is already used by %s." % (name, used[low]))
        if low in own:
            errors.append("Login sheet name '%s' is used twice in this tenant." % name)
        if low == sheet.lower():
            errors.append("Login sheet '%s' has the same name as the tenant sheet." % name)
        own[low] = lic

    # overlapping scope on the same contact center (double counting)
    if isinstance(cc, int) and arc in arcs:
        for k, c in tenants.items():
            if k == key or c.get("arc") != arc or c.get("contact_center_id") != cc or c.get("enabled", True) is False:
                continue
            other = c.get("campaign_ids") or []
            if not campaigns and not other:
                errors.append("Tenant %s already covers the whole contact center %s on %s - this would count the same users twice." % (k, cc, arc))
            elif not campaigns or not other:
                warnings.append("Tenant %s is on the same contact center (%s); one of you covers the whole contact center, so the same users can be counted in both." % (k, cc))
            elif set(map(str, campaigns)) & set(map(str, other)):
                warnings.append("Tenant %s shares campaign id(s) %s with this tenant." % (k, ", ".join(sorted(set(map(str, campaigns)) & set(map(str, other))))))
    return errors, warnings


def validate_defaults(defaults: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for bucket in LICENSE_KEYS:
        items = defaults.get("%s_user_types" % bucket) or []
        if not items:
            errors.append("Default %s user types must not be empty." % bucket)
        errors.extend(check_type_list(items, "Default %s user types" % bucket))
    errors.extend(check_type_list(defaults.get("ignored_user_types") or [], "Ignored user types"))
    seen: Dict[str, str] = {}
    for bucket in LICENSE_KEYS:
        for t in defaults.get("%s_user_types" % bucket) or []:
            k = re.sub(r"[\s_\-]+", "-", t.strip().lower())
            if k in seen and seen[k] != bucket:
                errors.append("User type '%s' is listed as both %s and %s." % (t, seen[k], bucket))
            seen.setdefault(k, bucket)
    for lic, lab in (defaults.get("license_labels") or {}).items():
        if not str(lab).strip() or len(str(lab)) > 40:
            errors.append("The %s label must be 1-40 characters." % lic)
    return errors


def validate_db_arc(name: str, s: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,255}", str(s.get("host", ""))):
        errors.append("%s: host must be a host name or IP address." % name)
    port = s.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append("%s: port must be 1-65535." % name)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,63}", str(s.get("name", ""))):
        errors.append("%s: database name is invalid." % name)
    if not re.fullmatch(r"[A-Za-z0-9_\-.@]{1,63}", str(s.get("user", ""))):
        errors.append("%s: database user is invalid." % name)
    if s.get("sslmode") and s["sslmode"] not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        errors.append("%s: unknown sslmode." % name)
    t = s.get("statement_timeout_ms")
    if t is not None and (not isinstance(t, int) or not (1000 <= t <= 3600000)):
        errors.append("%s: statement timeout must be between 1 and 3600 seconds." % name)
    return errors
