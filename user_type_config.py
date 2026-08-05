"""Tenant-aware user-type classification and display labels.

CSV/DB user_type values are classified into license buckets:
  agent      <- Professional-Agent, Executive, Ameyo-express (+ tenant overrides)
  supervisor <- Supervisor (+ tenant overrides)
  wallboard  <- Wallboard*, Wallboard User (+ tenant overrides)

Display names on Excel sheets are configurable:
  defaults.tenant_sheet_agent_label   -> default agent label on sheets
  tenants.<KEY>.agent_display_label   -> per-tenant agent rename
  tenants.<KEY>.display_labels        -> optional map for agent/supervisor/wallboard
  tenants.<KEY>.agent_user_types      -> optional override of which CSV types count as agent

Example (Ameyo Pro-Dialer tenants):
  PRUBSN:
    agent_display_label: "Ameyo Pro-Dialer"
"""
from __future__ import annotations

from typing import Any

# Built-in aliases always treated as agent when present in CSV.
_AGENT_NAME_ALIASES = {
    "ameyo-express",
    "ameyo express",
    "professional-agent",
    "professional agent",
    "executive",
}


def resolve_agent_types(tenant_cfg: dict[str, Any] | None, config: dict[str, Any]) -> list[str]:
    """CSV/DB user_type values that count toward the agent license peak."""
    if tenant_cfg and tenant_cfg.get("agent_user_types"):
        return [str(x) for x in tenant_cfg["agent_user_types"]]
    return list(config.get("agent_types") or [])


def resolve_supervisor_types(tenant_cfg: dict[str, Any] | None, config: dict[str, Any]) -> list[str]:
    if tenant_cfg and tenant_cfg.get("supervisor_user_types"):
        return [str(x) for x in tenant_cfg["supervisor_user_types"]]
    return list(config.get("supervisor_types") or [])


def resolve_wallboard_types(tenant_cfg: dict[str, Any] | None, config: dict[str, Any]) -> list[str]:
    if tenant_cfg and tenant_cfg.get("wallboard_user_types"):
        return [str(x) for x in tenant_cfg["wallboard_user_types"]]
    return list(config.get("wallboard_types") or [])


def resolve_display_label(
    license_key: str,
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None = None,
) -> str:
    """Label written on Summary / tenant sheets for a license bucket."""
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
    user_type: str,
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None = None,
) -> str | None:
    """Map a raw CSV/DB user_type string to agent|supervisor|wallboard."""
    ut = str(user_type).strip()
    if not ut:
        return None
    low = ut.lower()

    agent_types = resolve_agent_types(tenant_cfg, config)
    if ut in agent_types or low in {str(x).lower() for x in agent_types} or low in _AGENT_NAME_ALIASES:
        return "agent"

    supervisor_types = resolve_supervisor_types(tenant_cfg, config)
    if ut in supervisor_types or low in {str(x).lower() for x in supervisor_types}:
        return "supervisor"

    wallboard_types = resolve_wallboard_types(tenant_cfg, config)
    if ut in wallboard_types or low in {str(x).lower() for x in wallboard_types} or "wallboard" in low:
        return "wallboard"

    return None


def apply_peak_display_labels(
    peaks: dict[str, Any],
    config: dict[str, Any],
    tenant_cfg: dict[str, Any] | None,
) -> None:
    """Update LicensePeak.license_label from tenant/global display config."""
    for key, peak in (peaks or {}).items():
        if peak is None:
            continue
        peak.license_label = resolve_display_label(key, config, tenant_cfg)
