"""Shared synthetic scenario: a tiny registry + fake sessions that exercise the tricky paths."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from fakedb import FakeWorld


def D(day, hh, mm=0):
    return dt.datetime(2026, 9, day, hh, mm)


DEFAULTS = {
    "agent_user_types": ["Professional-Agent", "Ameyo-express", "Executive"],
    "supervisor_user_types": ["Supervisor"],
    "wallboard_user_types": ["Wallboard User", "Wallboard", "Wallboard-User"],
    "ignored_user_types": ["Administrator"],
    "license_labels": {"agent": "Professional-Agent", "supervisor": "Supervisor", "wallboard": "Wallboard User"},
    "tenant_sheet_agent_label": "Professional-Agent",
}

TENANTS = {
    # default (campaign) path; only the agent sheet is configured -> supervisor/wallboard sheets are auto-named
    "ALPHA": {"arc": "ARC-1", "project_name": "Alpha\nARCH 1", "sheet_name": "Alpha", "contact_center_id": 10,
              "login_sheets": {"agent": "Ameyo-Express_Alpha"}},
    # campaign-scoped tenant
    "GAMMA": {"arc": "ARC-1", "project_name": "Gamma\nARCH 1", "sheet_name": "Gamma", "contact_center_id": 30,
              "campaign_ids": ["5"], "login_sheets": {"agent": "Ameyo-Express_Gamma", "supervisor": "Supervisor_Gamma"}},
    # simple query, ARC-2
    "BETA": {"arc": "ARC-2", "project_name": "Beta\nARCH 2", "sheet_name": "Beta", "contact_center_id": 20,
             "use_simple_query": True,
             "login_sheets": {"agent": "Ameyo-Express_Beta", "supervisor": "Supervisor_Beta", "wallboard": "Wallboard_Beta"}},
    # nothing happens here in September
    "DELTA": {"arc": "ARC-2", "project_name": "Delta\nARCH 2", "sheet_name": "Delta", "contact_center_id": 40,
              "login_sheets": {"agent": "Ameyo-Express_Delta"}},
}


def build_world() -> FakeWorld:
    w = FakeWorld()
    # ---- ALPHA (ARC-1 cc10): 3 Professional-Agent + 2 Executive busy in the same hour on 2 Sep
    for i in (1, 2, 3):
        w.add("ARC-1", "alpha_a%d" % i, "Professional-Agent", 10, D(2, 9, 10), D(2, 10, 30), campaigns=[1])
    for i in (1, 2):
        w.add("ARC-1", "alpha_e%d" % i, "Executive", 10, D(2, 9, 20), D(2, 10, 0), campaigns=[1])
    w.add("ARC-1", "alpha_s1", "Supervisor", 10, D(2, 9, 0), D(2, 17, 0), campaigns=[1])
    w.add("ARC-1", "alpha_s2", "Supervisor", 10, D(3, 9, 0), D(3, 12, 0), campaign_row=False)   # no campaign link
    w.add("ARC-1", "ALPHA_wallboard", "Wallboard", 10, D(2, 8, 0), D(2, 20, 0), campaign_row=False)
    w.add("ARC-1", "alpha_admin", "Administrator", 10, D(2, 9, 0), D(2, 9, 30), campaign_row=False)
    # ---- GAMMA (campaign 5 only)
    w.add("ARC-1", "gamma_a1", "Professional-Agent", 30, D(5, 10, 0), D(5, 11, 0), campaigns=[5])
    w.add("ARC-1", "gamma_a2", "Professional-Agent", 30, D(5, 10, 5), D(5, 10, 55), campaigns=[5])
    w.add("ARC-1", "gamma_x1", "Professional-Agent", 30, D(5, 10, 5), D(5, 10, 55), campaigns=[9])   # other campaign
    w.add("ARC-1", "gamma_s1", "Supervisor", 30, D(5, 10, 0), D(5, 11, 30), campaigns=[5])
    # ---- BETA (ARC-2 simple query): includes a user type nobody mapped
    for i in (1, 2, 3, 4):
        w.add("ARC-2", "beta_a%d" % i, "Ameyo-express", 20, D(7, 9, 0), D(7, 17, 0))
    w.add("ARC-2", "beta_new1", "Ameyo-Pro", 20, D(7, 9, 0), D(7, 17, 0))                # unmapped type
    w.add("ARC-2", "beta_s1", "Supervisor", 20, D(7, 8, 30), D(7, 18, 0))
    w.add("ARC-2", "BETA_WALLBOARD", "Wallboard", 20, D(7, 8, 0), D(7, 19, 0))
    return w


def write_config(dirpath, tenants=None, defaults=None) -> Path:
    path = Path(dirpath) / "tenants.yaml"
    path.write_text(yaml.safe_dump({"defaults": defaults or DEFAULTS, "tenants": tenants or TENANTS}, sort_keys=False), encoding="utf-8")
    return path
