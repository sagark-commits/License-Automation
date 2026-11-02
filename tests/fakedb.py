"""A fake PostgreSQL layer for tests: canned sessions answer the real report SQL.

No database is needed.  ``FakeWorld`` holds users / sessions per ARC; ``install(world)`` monkeypatches
pandas.read_sql and the connection helpers so the real engine code (queries, peaks, workbooks) runs
unchanged against synthetic data.  The 'SQL' is interpreted by recognising the query shape produced by
db_usage_queries / db_login_queries and evaluating the same rules in Python.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Any, Dict, List

import pandas as pd

HOURS = ["Count at %d hour" % h for h in range(1, 25)]


class FakeConn:
    def __init__(self, arc: str, world: "FakeWorld"):
        self.arc, self.world, self.closed = arc, world, False

    def close(self):
        self.closed = True


class FakeWorld:
    def __init__(self):
        # arc -> list of dict(user_id,user_type,cc,session_id,login,logout,campaigns=[...])
        self.sessions: Dict[str, List[dict]] = defaultdict(list)
        self.fail_login_for: set = set()    # sheet-agnostic: substring of SQL that should raise
        self.queries: List[str] = []

    def add(self, arc, user_id, user_type, cc, login, logout, campaigns=(), campaign_row=True):
        self.sessions[arc].append(dict(
            user_id=user_id, user_type=user_type, cc=int(cc), login=login, logout=logout,
            campaigns=[str(c) for c in campaigns], campaign_row=campaign_row,
            session_id=len(self.sessions[arc]) + 1))


def _norm(s):
    return re.sub(r"[\s_\-]+", "-", str(s or "").strip().lower())


def _wallboard_match(user_id, params):
    if params.get("wallboard_user_id"):
        return user_id == params["wallboard_user_id"]
    pat = params.get("wallboard_pattern", "%wallboard%").strip("%").lower()
    return pat in user_id.lower()


def _usage(world, arc, sql, p):
    start, end, cc = p["interval_start"], p["interval_end"], int(p["cc_id"])
    wb = "wallboard_user_id" in p
    rows = defaultdict(lambda: [set() for _ in range(24)])
    for s in world.sessions[arc]:
        if s["cc"] != cc or not (start <= s["login"] <= end):
            continue
        uid = s["user_id"]
        is_wb_user = "wallboard" in uid.lower()
        if wb:
            if not _wallboard_match(uid, p):
                continue
        elif "NOT ILIKE '%%wallboard%%'" in sql:           # simple query
            if is_wb_user:
                continue
        elif "campaign_ids" in p:
            if not s["campaign_row"] or not set(p["campaign_ids"]) & set(s["campaigns"]):
                continue
        elif "sup_types" in p:
            sup = _norm(s["user_type"]) in {_norm(x) for x in p["sup_types"]}
            if is_wb_user or not (s["campaign_row"] or sup):
                continue
        else:                                              # default campaign path
            if not s["campaign_row"]:
                continue
        d = s["login"].date()
        for h in range(24):
            lo = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=h)
            hi = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=h + 1)
            if s["logout"] >= lo and s["login"] <= hi:
                rows[(d, s["user_type"])][h].add(uid)
    out = [dict(Date=d, user_type=ut, **{HOURS[h]: len(v[h]) for h in range(24)}) for (d, ut), v in sorted(rows.items())]
    return pd.DataFrame(out, columns=["Date", "user_type"] + HOURS)


def _login(world, arc, sql, p):
    cc, d = int(p["cc_id"]), p["peak_date"]
    types = {_norm(x) for x in p.get("user_types", [])}
    out = []
    for s in world.sessions[arc]:
        if s["cc"] != cc or s["login"].date() != d:
            continue
        uid = s["user_id"]
        if "wallboard_pattern" in p and "wallboard_user_id" in p:      # wallboard branch
            if "campaign_user_working_history" in sql or not _wallboard_match(uid, p):
                continue
        else:
            if _norm(s["user_type"]) not in types:
                continue
            if "campaign_user_working_history" in sql:
                if not s["campaign_row"]:
                    continue
                if "campaign_ids" in p and not set(p["campaign_ids"]) & set(s["campaigns"]):
                    continue
            if "NOT ILIKE" in sql and "wallboard" in uid.lower():
                continue
        if "hour_start" in p:
            lo = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=p["hour_start"])
            hi = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=p["hour_end"])
            if not (s["logout"] >= lo and s["login"] <= hi):
                continue
        out.append(dict(user_id=uid, login_time=s["login"], logout_time=s["logout"], duration=s["logout"] - s["login"]))
    out.sort(key=lambda r: r["login_time"])
    return pd.DataFrame(out, columns=["user_id", "login_time", "logout_time", "duration"])


def install(world: FakeWorld, monkeypatch) -> None:
    import db_connections, tmone_db_mode, tmone_report

    def fake_read_sql(sql, conn, params=None):
        world.queries.append(sql)
        for frag in world.fail_login_for:
            if frag in sql:
                raise RuntimeError("simulated failure: " + frag)
        p = params or {}
        if "interval_start" in p:
            return _usage(world, conn.arc, sql, p)
        return _login(world, conn.arc, sql, p)

    monkeypatch.setattr(tmone_db_mode.pd, "read_sql", fake_read_sql)
    monkeypatch.setattr(tmone_report.pd, "read_sql", fake_read_sql)

    def fake_connect(args, script_dir, db_config_path=None, only_arcs=None):
        arcs = only_arcs if only_arcs is not None else ["ARC-1", "ARC-2"]
        return {"login": {a: FakeConn(a, world) for a in arcs}, "usage": {}}

    monkeypatch.setattr(db_connections, "connect_login_databases", fake_connect)
