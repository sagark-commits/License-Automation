"""Login-sheet naming.

Every licence bucket (agent / supervisor / wallboard) that has a peak in the month must get a
login-evidence sheet in the login workbook.  Sheets named in ``tenants.<KEY>.login_sheets`` are
used as-is; for a licence that has a peak but no configured sheet a deterministic name is
generated, so a newly added tenant can never silently produce a report with a missing sheet.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Set

LICENSE_KEYS = ("agent", "supervisor", "wallboard")
MAX_SHEET_LEN = 31
_BAD_CHARS = re.compile(r"[\[\]:*?/\\]")
_PREFIX = {"agent": "Ameyo-Express", "supervisor": "Supervisor", "wallboard": "Wallboard", "executive": "Executive"}


def clean_sheet_name(name: str) -> str:
    """Make ``name`` a valid Excel sheet name (<= 31 chars, no []:*?/\\)."""
    out = _BAD_CHARS.sub("_", str(name or "")).replace("\n", " ").strip("'")
    return out[:MAX_SHEET_LEN]


def default_sheet_name(cfg: Dict[str, Any], license_key: str, tenant_key: str = "") -> str:
    base = str(cfg.get("sheet_name") or tenant_key or "tenant")
    return clean_sheet_name("%s_%s" % (_PREFIX.get(license_key, license_key.title()), base))


def _unique(name: str, taken: Set[str]) -> str:
    cand, n = name, 1
    while cand.lower() in taken:
        n += 1
        suffix = "_%d" % n
        cand = name[: MAX_SHEET_LEN - len(suffix)] + suffix
    return cand


def configured_sheet_names(tenants: Dict[str, Dict[str, Any]]) -> Set[str]:
    """Lower-cased set of every sheet name explicitly configured for any tenant."""
    out: Set[str] = set()
    for cfg in (tenants or {}).values():
        for name in (cfg.get("login_sheets") or {}).values():
            out.add(str(name).lower())
    return out


def effective_login_sheets(
    cfg: Dict[str, Any],
    peaks: Optional[Dict[str, Any]] = None,
    taken: Optional[Set[str]] = None,
    tenant_key: str = "",
) -> Dict[str, str]:
    """Configured login sheets plus generated ones for licences that have a peak but no sheet.

    ``taken`` (lower-case names) is updated in place so names stay unique across tenants.
    """
    taken = taken if taken is not None else set()
    sheets: Dict[str, str] = {str(k): str(v) for k, v in (cfg.get("login_sheets") or {}).items()}
    taken.update(v.lower() for v in sheets.values())
    for lic in LICENSE_KEYS:
        if lic in sheets:
            continue
        peak = (peaks or {}).get(lic)
        if peak is not None and getattr(peak, "peak_count", 0) > 0:
            name = _unique(default_sheet_name(cfg, lic, tenant_key), taken)
            taken.add(name.lower())
            sheets[lic] = name
    return sheets


def assign_login_sheets(tenants_data: Iterable[Any], config: Dict[str, Any]) -> None:
    """Set ``td.login_sheet_map`` for every tenant (call once peaks are known)."""
    taken = configured_sheet_names(config.get("tenants", {}))
    for td in tenants_data:
        td.login_sheet_map = effective_login_sheets(td.cfg, td.peaks, taken, td.key)


def sheets_for(td: Any) -> Dict[str, str]:
    """Login sheets for a tenant: the assigned map if present, else the configured ones."""
    m = getattr(td, "login_sheet_map", None)
    return dict(m) if m else dict(td.cfg.get("login_sheets") or {})
