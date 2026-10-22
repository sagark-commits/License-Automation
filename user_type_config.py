"""Tenant-aware user-type classification and display labels.

Ameyo ``users.user_type`` values are classified into licence buckets:

  agent      <- Professional-Agent, Executive, Ameyo-express   (global default)
  supervisor <- Supervisor                                     (global default)
  wallboard  <- Wallboard*, Wallboard User                     (global default)

A tenant can REPLACE any of these lists with its own (the override is exclusive,
nothing is added behind the scenes):

  tenants.<KEY>.agent_user_types       -> which user_type values count as AGENT
  tenants.<KEY>.supervisor_user_types  -> which user_type values count as SUPERVISOR
  tenants.<KEY>.wallboard_user_types   -> which user_type values count as WALLBOARD

Matching is case-insensitive and treats spaces, hyphens and underscores alike
("Ameyo Express" == "ameyo-express" == "Ameyo_Express").  Any user_type that matches no
bucket is *unclassified*: it is not counted, and the report run warns about it.

Display names on Excel sheets are configurable:
  defaults.tenant_sheet_agent_label   -> default agent label on sheets
  tenants.<KEY>.agent_display_label   -> per-tenant agent rename
  tenants.<KEY>.display_labels        -> optional map for agent/supervisor/wallboard
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

BUCKETS = ("agent", "supervisor", "wallboard")

# Used only when neither the tenant nor tenants.yaml defines a list at all.
FALLBACK_TYPES = {
    "agent": ["Professional-Agent", "Ameyo-express", "Executive"],
    "supervisor": ["Supervisor"],
    "wallboard": ["Wallboard User", "Wallboard", "Wallboard-User"],
}


def norm_type(value: Any) -> str:
    """Normalise a user_type for comparison (case, spaces, hyphens, underscores)."""
    return re.sub(r"[\s_\-]+", "-", str(value or "").strip().lower())


def sql_type_variants(types: Iterable[str]) -> List[str]:
    """Lower-case spelling variants of ``types`` for ``lower(trim(user_type)) = ANY(...)``.

    Mirrors :func:`norm_type` so SQL filters and Python classification agree.
    """
    out = set()
    for t in types:
        base = str(t or "").strip().lower()
        if not base:
            continue
        core = norm_type(base)
        out.update({base, core, core.replace("-", " "), core.replace("-", "_")})
    return sorted(out)


def _list(tenant_cfg: Optional[Dict[str, Any]], tenant_key: str, config: Dict[str, Any], config_key: str, bucket: str) -> List[str]:
    if tenant_cfg and tenant_cfg.get(tenant_key):
        return [str(x) for x in tenant_cfg[tenant_key]]
    if config.get(config_key):
        return [str(x) for x in config[config_key]]
    return list(FALLBACK_TYPES[bucket])


def resolve_agent_types(tenant_cfg: Optional[Dict[str, Any]], config: Dict[str, Any]) -> List[str]:
    """user_type values that count toward the AGENT licence peak."""
    return _list(tenant_cfg, "agent_user_types", config, "agent_types", "agent")


def resolve_supervisor_types(tenant_cfg: Optional[Dict[str, Any]], config: Dict[str, Any]) -> List[str]:
    return _list(tenant_cfg, "supervisor_user_types", config, "supervisor_types", "supervisor")


def resolve_wallboard_types(tenant_cfg: Optional[Dict[str, Any]], config: Dict[str, Any]) -> List[str]:
    return _list(tenant_cfg, "wallboard_user_types", config, "wallboard_types", "wallboard")


def types_for_bucket(bucket: str, tenant_cfg: Optional[Dict[str, Any]], config: Dict[str, Any]) -> List[str]:
    if bucket in ("agent", "executive"):
        return resolve_agent_types(tenant_cfg, config)
    if bucket == "supervisor":
        return resolve_supervisor_types(tenant_cfg, config)
    return resolve_wallboard_types(tenant_cfg, config)


def resolve_display_label(
    license_key: str,
    config: Dict[str, Any],
    tenant_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Label written on Summary / tenant sheets for a licence bucket."""
    tenant_cfg = tenant_cfg or {}
    custom = tenant_cfg.get("display_labels") or {}
    if license_key in custom and custom[license_key]:
        return str(custom[license_key])

    if license_key in ("agent", "executive"):
        if tenant_cfg.get("agent_display_label"):
            return str(tenant_cfg["agent_display_label"])
        return str(
            config.get("tenant_agent_label")
            or (config.get("license_labels") or {}).get("agent")
            or "Professional-Agent"
        )
    if license_key == "supervisor":
        return str((config.get("license_labels") or {}).get("supervisor") or "Supervisor")
    if license_key == "wallboard":
        return str((config.get("license_labels") or {}).get("wallboard") or "Wallboard User")
    return license_key


def classify_user_type(
    user_type: Any,
    config: Dict[str, Any],
    tenant_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Map a raw user_type string to ``agent`` | ``supervisor`` | ``wallboard`` | None.

    Only the resolved lists (tenant override, else tenants.yaml defaults) are used - there are
    no hidden aliases - so a per-tenant override always means exactly what it says.
    """
    ut = norm_type(user_type)
    if not ut:
        return None
    for bucket, resolver in (
        ("agent", resolve_agent_types),
        ("supervisor", resolve_supervisor_types),
        ("wallboard", resolve_wallboard_types),
    ):
        if ut in {norm_type(x) for x in resolver(tenant_cfg, config)}:
            return bucket
    if "wallboard" in ut:
        return "wallboard"
    return None


def type_conflicts(tenant_cfg: Optional[Dict[str, Any]], config: Dict[str, Any]) -> List[str]:
    """user_type values that appear in more than one bucket for this tenant (a config error)."""
    seen: Dict[str, str] = {}
    problems: List[str] = []
    for bucket in BUCKETS:
        for t in types_for_bucket(bucket, tenant_cfg, config):
            k = norm_type(t)
            if k in seen and seen[k] != bucket:
                problems.append("%r is listed as both %s and %s" % (t, seen[k], bucket))
            seen.setdefault(k, bucket)
    return problems


def apply_peak_display_labels(
    peaks: Dict[str, Any],
    config: Dict[str, Any],
    tenant_cfg: Optional[Dict[str, Any]],
) -> None:
    """Update LicensePeak.license_label from tenant/global display config."""
    for key, peak in (peaks or {}).items():
        if peak is None:
            continue
        peak.license_label = resolve_display_label(key, config, tenant_cfg)
