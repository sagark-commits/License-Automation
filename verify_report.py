#!/usr/bin/env python3
"""Self-check for a generated utilization + login workbook pair.

Re-reads the two .xlsx files (not the in-memory data) and reconciles them against each other and
against the tenant registry:

  utilization workbook : tenant sheets <-> Summary <-> Dashboard
  login workbook       : every licence with a peak has its login sheet, the sheet lists exactly
                         as many distinct users as the reported peak, and every session belongs
                         to the peak date / peak hour.

Usage:
    python verify_report.py "Tmone license-Utilaztion_2026_09.xlsx" "Tmone--Login Count Tmone_Sep_26.xlsx"
Exit code 1 when at least one ERROR finding exists.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SEV_ERROR, SEV_WARN, SEV_INFO = "error", "warn", "info"


def _finding(sev: str, code: str, message: str, tenant: Optional[str] = None) -> Dict[str, Any]:
    return {"severity": sev, "code": code, "tenant": tenant, "message": message}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _to_date(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if _is_num(v):
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=int(v))).date()
    return None


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\n", " ")).strip().lower()


def _bucket(label: Any, cfg: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> str:
    """Licence bucket of a sheet row label: the tenant's display labels, else its user-type lists.

    Labels that match nothing are raw, unclassified Ameyo user types -> 'other' (never counted).
    Without a registry (``config`` None) fall back to keyword matching.
    """
    s = str(label or "")
    if config is not None:
        from user_type_config import classify_user_type, norm_type, resolve_display_label

        for b in ("agent", "supervisor", "wallboard"):
            if norm_type(resolve_display_label(b, config, cfg)) == norm_type(s):
                return b
        return classify_user_type(s, config, cfg) or "other"
    low = s.lower()
    if "sup" in low:
        return "supervisor"
    if "wall" in low:
        return "wallboard"
    return "agent"


def _sheet_rows(ws) -> Dict[int, Dict[int, Any]]:
    rows: Dict[int, Dict[int, Any]] = {}
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = {c + 1: v for c, v in enumerate(row) if v is not None and v != ""}
        if cells:
            rows[r_idx] = cells
    return rows


# ----------------------------------------------------------------------------- parsing

def _parse_tenant_sheet(rows: Dict[int, Dict[int, Any]]) -> Dict[str, Any]:
    top: List[Tuple[Optional[dt.date], str, int]] = []
    hdr = None
    for r in sorted(rows):
        a, c = rows[r].get(1), rows[r].get(3)
        if a == "Date" and c == "Max_count":
            top_hdr = r
            r2 = r + 1
            while r2 in rows and _to_date(rows[r2].get(1)) and _is_num(rows[r2].get(3)):
                top.append((_to_date(rows[r2][1]), str(rows[r2].get(2)), int(rows[r2][3])))
                r2 += 1
        if a == "Date" and c == "Max count":
            hdr = r
    daily: List[Tuple[dt.date, str, int, List[int]]] = []
    if hdr:
        r = hdr + 1
        while r in rows and _to_date(rows[r].get(1)):
            hours = [int(rows[r].get(c, 0)) if _is_num(rows[r].get(c, 0)) else 0 for c in range(4, 28)]
            daily.append((_to_date(rows[r][1]), str(rows[r].get(2)), int(rows[r].get(3, 0) or 0), hours))
            r += 1
    return {"top": top, "daily": daily, "header_cells": [rows[hdr].get(c) for c in range(4, 28)] if hdr else []}


def _parse_summary(rows: Dict[int, Dict[int, Any]], ndays: int) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    cur = None
    for r in sorted(rows):
        if r < 4:
            continue
        row = rows[r]
        if row.get(1) == "Legend:":
            break
        if row.get(1):
            cur = {"project": str(row[1]), "lic": []}
            blocks.append(cur)
        if row.get(2) and cur is not None:
            days = [int(row.get(4 + i, 0)) if _is_num(row.get(4 + i, 0)) else 0 for i in range(ndays)]
            cur["lic"].append({"label": str(row[2]), "bucket": None, "max": int(row.get(3, 0) or 0), "days": days})
    return blocks


def _parse_dashboard(rows: Dict[int, Dict[int, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"rows": {}, "kpi": {}, "daily": {}}
    kpi_row = next((r for r in sorted(rows) if r <= 8 and _is_num(rows[r].get(1))), None)
    if kpi_row:
        k = rows[kpi_row]
        out["kpi"] = {"tenants": k.get(1), "combined": k.get(4), "highest": k.get(7), "arc": k.get(10)}
    hdr = next((r for r in sorted(rows) if rows[r].get(1) == "Tenant"), None)
    if hdr:
        r = hdr + 1
        while r in rows and rows[r].get(1):
            row = rows[r]
            out["rows"][str(row[1])] = {"arc": row.get(2), "agent": row.get(3), "date": _to_date(row.get(4)), "sup": row.get(5)}
            if _to_date(row.get(17)) and _is_num(row.get(18)):
                out["daily"][_to_date(row[17])] = int(row[18])
            r += 1
        while r in rows:      # the daily-total table can be longer than the tenant table
            row = rows[r]
            if _to_date(row.get(17)) and _is_num(row.get(18)):
                out["daily"][_to_date(row[17])] = int(row[18])
            r += 1
    return out


# ----------------------------------------------------------------------------- main checks

def verify_workbooks(
    util_path: Path,
    login_path: Path,
    config: Dict[str, Any],
    *,
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a list of findings (dicts with severity/code/tenant/message)."""
    from openpyxl import load_workbook

    from login_sheets import configured_sheet_names, default_sheet_name

    F: List[Dict[str, Any]] = []
    tenants_cfg: Dict[str, Dict[str, Any]] = config.get("tenants", {})
    by_sheet = {str(c.get("sheet_name", k))[:31]: k for k, c in tenants_cfg.items()}
    by_project = {_norm(c.get("project_name", k)): k for k, c in tenants_cfg.items()}

    wb = load_workbook(util_path, data_only=True)
    lwb = load_workbook(login_path, data_only=True)
    for need in ("Dashboard", "Summary"):
        if need not in wb.sheetnames:
            F.append(_finding(SEV_ERROR, "U00", "utilization workbook has no '%s' sheet" % need))
    if F:
        return F

    tenant_sheets = [n for n in wb.sheetnames if n not in ("Dashboard", "Summary")]
    T: Dict[str, Dict[str, Any]] = {}
    for name in tenant_sheets:
        key = by_sheet.get(name)
        if key is None:
            F.append(_finding(SEV_WARN, "U10", "sheet '%s' is not in the tenant registry" % name))
            continue
        T[key] = _parse_tenant_sheet(_sheet_rows(wb[name]))
        T[key]["sheet"] = name

    # month / number of days
    if not month:
        first = next((t["top"][0][0] for t in T.values() if t["top"]), None) or next(
            (t["daily"][0][0] for t in T.values() if t["daily"]), None)
        month = first.strftime("%Y-%m") if first else None
    if month:
        y, m = map(int, month.split("-"))
        ndays = calendar.monthrange(y, m)[1]
        day0 = dt.date(y, m, 1)
    else:
        ndays, day0 = 30, None

    # ---- tenant sheets internal consistency
    for key, t in T.items():
        for d, ut, mx, hours in t["daily"]:
            if max(hours) != mx:
                F.append(_finding(SEV_ERROR, "U01", "%s: 'Max count' %s but highest hourly value is %s (%s %s)" % (t["sheet"], mx, max(hours), d, ut), key))
        tcfg = tenants_cfg[key]
        bk = lambda label, tcfg=tcfg: _bucket(label, tcfg, config)  # noqa: E731
        t["bk"] = bk
        buckets: Dict[Tuple[str, dt.date], List[int]] = {}
        for d, ut, mx, hours in t["daily"]:
            cur = buckets.setdefault((bk(ut), d), [0] * 24)
            buckets[(bk(ut), d)] = [a + b for a, b in zip(cur, hours)]
        t["bucket_day"] = {k: max(v) for k, v in buckets.items()}
        for d, ut, peak in t["top"]:
            b = bk(ut)
            if b == "wallboard":
                continue
            days = {dd: v for (bb, dd), v in t["bucket_day"].items() if bb == b}
            if not days:
                F.append(_finding(SEV_ERROR, "U02", "%s: top block lists %s peak %s but there are no daily rows" % (t["sheet"], ut, peak), key))
                continue
            if max(days.values()) != peak:
                F.append(_finding(SEV_ERROR, "U02", "%s: %s peak %s does not equal the highest daily value %s" % (t["sheet"], ut, peak, max(days.values())), key))
            elif days.get(d) != peak:
                F.append(_finding(SEV_ERROR, "U03", "%s: %s peak date %s is not a day where the peak was reached" % (t["sheet"], ut, d), key))

    # ---- Summary vs tenant sheets
    summary = _parse_summary(_sheet_rows(wb["Summary"]), ndays)
    S: Dict[str, Dict[str, Any]] = {}
    order = list(T.keys())
    for i, blk in enumerate(summary):
        key = by_project.get(_norm(blk["project"])) or (order[i] if i < len(order) else None)
        if key is None or key not in T:
            F.append(_finding(SEV_ERROR, "U05", "Summary tenant '%s' has no matching tenant sheet" % blk["project"].replace("\n", " ")))
            continue
        S[key] = blk
    for key in T:
        if key not in S:
            F.append(_finding(SEV_ERROR, "U05", "tenant sheet '%s' is missing from the Summary" % T[key]["sheet"], key))
    for key, blk in S.items():
        t = T[key]
        for lic in blk["lic"]:
            lic["bucket"] = t["bk"](lic["label"])
            b = lic["bucket"]
            if max(lic["days"]) != lic["max"]:
                F.append(_finding(SEV_ERROR, "U04", "%s %s: 'Summary of Max Login' %s but highest daily value %s" % (t["sheet"], lic["label"], lic["max"], max(lic["days"])), key))
            top = [x for x in t["top"] if t["bk"](x[1]) == b]
            if top and top[0][2] != lic["max"]:
                F.append(_finding(SEV_ERROR, "U04", "%s %s: Summary max %s vs tenant sheet peak %s" % (t["sheet"], lic["label"], lic["max"], top[0][2]), key))
            if not top and lic["max"] > 0:
                F.append(_finding(SEV_ERROR, "U04", "%s %s: Summary shows %s but the tenant sheet has no peak" % (t["sheet"], lic["label"], lic["max"]), key))
            if b in ("agent", "supervisor") and day0:
                diffs = []
                for i, v in enumerate(lic["days"]):
                    exp = t["bucket_day"].get((b, day0 + dt.timedelta(days=i)), 0)
                    if exp != v:
                        diffs.append("%s: %s vs %s" % ((day0 + dt.timedelta(days=i)).strftime("%d-%b"), v, exp))
                if diffs:
                    F.append(_finding(SEV_ERROR, "U04", "%s %s: %d day(s) differ between Summary and the tenant sheet (%s)" % (t["sheet"], lic["label"], len(diffs), "; ".join(diffs[:3])), key))

    # ---- Dashboard vs Summary
    dash = _parse_dashboard(_sheet_rows(wb["Dashboard"]))
    agent_peak: Dict[str, int] = {}
    sup_peak: Dict[str, int] = {}
    for key, blk in S.items():
        a = [x for x in blk["lic"] if x["bucket"] == "agent"]
        s_ = [x for x in blk["lic"] if x["bucket"] == "supervisor"]
        agent_peak[key] = a[0]["max"] if a else 0
        sup_peak[key] = s_[0]["max"] if s_ else 0
        row = dash["rows"].get(T[key]["sheet"])
        if row is None:
            F.append(_finding(SEV_ERROR, "D01", "Dashboard has no row for %s" % T[key]["sheet"], key))
            continue
        if row["agent"] != agent_peak[key] or row["sup"] != sup_peak[key]:
            F.append(_finding(SEV_ERROR, "D01", "%s: Dashboard agent/supervisor %s/%s vs Summary %s/%s" % (T[key]["sheet"], row["agent"], row["sup"], agent_peak[key], sup_peak[key]), key))
        tt = [x for x in T[key]["top"] if T[key]["bk"](x[1]) == "agent"]
        if tt and row["agent"] and row["date"] != tt[0][0]:
            F.append(_finding(SEV_ERROR, "D01", "%s: Dashboard peak date %s vs tenant sheet %s" % (T[key]["sheet"], row["date"], tt[0][0]), key))
    k = dash["kpi"]
    if k:
        if k.get("tenants") != len(T):
            F.append(_finding(SEV_ERROR, "D02", "Dashboard tenant count %s vs %d tenant sheets" % (k.get("tenants"), len(T))))
        if k.get("combined") != sum(agent_peak.values()):
            F.append(_finding(SEV_ERROR, "D02", "Dashboard combined agent peak %s vs sum of tenant peaks %s" % (k.get("combined"), sum(agent_peak.values()))))
    if day0:
        for i in range(ndays):
            d = day0 + dt.timedelta(days=i)
            exp = sum(([x for x in blk["lic"] if x["bucket"] == "agent"] or [{"days": [0] * ndays}])[0]["days"][i] for blk in S.values())
            if dash["daily"].get(d, 0) != exp:
                F.append(_finding(SEV_ERROR, "D03", "Dashboard daily total agents %s is %s but the tenants add up to %s" % (d, dash["daily"].get(d, 0), exp)))
                break

    # ---- login workbook
    taken = configured_sheet_names(tenants_cfg)
    known_sheets = set()
    login_names = set(lwb.sheetnames)
    for key, blk in S.items():
        cfg = tenants_cfg[key]
        t = T[key]
        for lic in blk["lic"]:
            b = lic["bucket"]
            peak = lic["max"]
            top = [x for x in t["top"] if t["bk"](x[1]) == b]
            sheet = (cfg.get("login_sheets") or {}).get(b) or (default_sheet_name(cfg, b, key) if peak > 0 else None)
            if sheet:
                known_sheets.add(sheet)
            if peak <= 0:
                continue
            if sheet not in login_names:
                F.append(_finding(SEV_ERROR, "L01", "%s: no login sheet for %s (peak %s)%s" % (t["sheet"], lic["label"], peak, "" if sheet else ""), key))
                continue
            rows = _sheet_rows(lwb[sheet])
            sess = []
            for r in sorted(rows):
                if r == 1:
                    continue
                row = rows[r]
                sess.append((row.get(1), row.get(2), row.get(3), row.get(4)))
            users = {s[0] for s in sess}
            if len(users) != peak:
                F.append(_finding(SEV_ERROR, "L02", "%s: sheet '%s' lists %d distinct user(s) but the %s peak is %s" % (t["sheet"], sheet, len(users), lic["label"], peak), key))
            if not top:
                continue
            pdate = top[0][0]
            win = None
            if b != "wallboard":
                dr = [x for x in t["daily"] if x[0] == pdate and t["bk"](x[1]) == b]
                if dr:
                    summed = [sum(x[3][h] for x in dr) for h in range(24)]
                    hour = summed.index(max(summed)) + 1
                    base = dt.datetime.combine(pdate, dt.time())
                    win = (base + dt.timedelta(hours=hour - 1), base + dt.timedelta(hours=hour))
            bad_window = bad_date = bad_order = bad_dur = open_ = 0
            for u, a, bb, dur in sess:
                if not isinstance(a, dt.datetime):
                    continue
                if a.date() != pdate:
                    bad_date += 1
                if not isinstance(bb, dt.datetime):
                    open_ += 1
                    continue
                if bb < a:
                    bad_order += 1
                if win and not (a <= win[1] and bb >= win[0]):
                    bad_window += 1
                if isinstance(dur, str):
                    m = re.match(r"^(\d+):(\d\d):(\d\d(?:\.\d+)?)$", dur.strip())
                    if m:
                        secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                        if abs(secs - (bb - a).total_seconds()) > 1.0:
                            bad_dur += 1
            if bad_date:
                F.append(_finding(SEV_ERROR, "L04", "%s: sheet '%s' has %d session(s) that did not start on the peak date %s" % (t["sheet"], sheet, bad_date, pdate), key))
            if bad_window:
                F.append(_finding(SEV_ERROR, "L03", "%s: sheet '%s' has %d session(s) outside the peak hour %s" % (t["sheet"], sheet, bad_window, win[0].strftime("%d-%b %H:00") if win else ""), key))
            if bad_order:
                F.append(_finding(SEV_WARN, "L05", "%s: sheet '%s' has %d session(s) with logout before login" % (t["sheet"], sheet, bad_order), key))
            if open_:
                F.append(_finding(SEV_WARN, "L05", "%s: sheet '%s' has %d session(s) without a logout time" % (t["sheet"], sheet, open_), key))
            if bad_dur:
                F.append(_finding(SEV_WARN, "L06", "%s: sheet '%s' has %d duration value(s) that do not match logout-login" % (t["sheet"], sheet, bad_dur), key))
    # all configured sheets for known tenants are fine even when empty (zero peak); flag strangers
    for key, cfg in tenants_cfg.items():
        for name in (cfg.get("login_sheets") or {}).values():
            known_sheets.add(name)
    for name in lwb.sheetnames:
        if name not in known_sheets:
            F.append(_finding(SEV_WARN, "L07", "login sheet '%s' does not belong to any known tenant/licence" % name))
    return F


def summarize(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {SEV_ERROR: 0, SEV_WARN: 0, SEV_INFO: 0}
    for f in findings:
        out[f["severity"]] = out.get(f["severity"], 0) + 1
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a TMONE utilization + login workbook pair")
    ap.add_argument("utilization", type=Path)
    ap.add_argument("login", type=Path)
    ap.add_argument("-c", "--config", type=Path, default=Path(__file__).resolve().parent / "tenants.yaml")
    ap.add_argument("-m", "--month", help="YYYY-MM (default: inferred)")
    args = ap.parse_args(argv)
    from tmone_report import load_config

    findings = verify_workbooks(args.utilization, args.login, load_config(args.config), month=args.month)
    for f in findings:
        print("%-5s %-4s %s" % (f["severity"].upper(), f["code"], f["message"]))
    s = summarize(findings)
    print("-- %d error(s), %d warning(s)" % (s[SEV_ERROR], s[SEV_WARN]))
    return 1 if s[SEV_ERROR] else 0


if __name__ == "__main__":
    sys.exit(main())
