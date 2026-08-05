"""Load agent/supervisor login session CSV exports (optional, no database)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from login_export import prepare_login_dataframe


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _classify_login_filename(name: str) -> str | None:
    low = name.lower()
    if "wallboard" in low or "wall_board" in low:
        return "wallboard"
    if "super" in low or "sup_" in low or "_sup" in low:
        return "supervisor"
    if "agent" in low or "express" in low or "dialer" in low or "executive" in low:
        return "agent"
    if "executive" in low or "excutive" in low:
        return "executive"
    return None


def _tenant_score(path: Path, cfg: dict[str, Any]) -> int:
    folder = path.parent.name.lower()
    filename = path.name.lower()
    score = 0
    hit = False
    for pat in cfg.get("csv_patterns", []):
        p = str(pat).lower()
        if p in filename:
            score += len(p) * 3
            hit = True
        elif p in folder:
            score += len(p) * 2
            hit = True
    for pat in cfg.get("folder_patterns", []):
        if str(pat).lower() in folder:
            score += 50
    return score if hit else 0


def _build_sheet_index(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for key, cfg in config["tenants"].items():
        for lic, sheet in cfg.get("login_sheets", {}).items():
            index[_normalize_name(sheet)] = (key, lic)
    return index


def discover_login_files(login_dir: Path, config: dict[str, Any]) -> dict[str, dict[str, list[Path]]]:
    """Return {tenant_key: {license_key: [paths]}}."""
    out: dict[str, dict[str, list[Path]]] = {}
    if not login_dir.is_dir():
        return out

    sheet_index = _build_sheet_index(config)
    for csv_path in sorted(login_dir.rglob("*")):
        if csv_path.suffix.lower() not in {".csv", ".tsv", ".txt"}:
            continue
        stem_norm = _normalize_name(csv_path.stem)
        matched = False
        for sheet_norm, (tenant_key, license_key) in sheet_index.items():
            if sheet_norm == stem_norm or sheet_norm in stem_norm or stem_norm in sheet_norm:
                out.setdefault(tenant_key, {}).setdefault(license_key, []).append(csv_path)
                matched = True
                break
        if matched:
            continue

        best_key, best_score = None, 0
        for key, cfg in config["tenants"].items():
            s = _tenant_score(csv_path, cfg)
            if s > best_score:
                best_key, best_score = key, s
        if not best_key:
            continue
        lic = _classify_login_filename(csv_path.name)
        if not lic:
            continue
        out.setdefault(best_key, {}).setdefault(lic, []).append(csv_path)
    return out


def read_login_csv(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    if lines and "|" in lines[0] and "user_id" in lines[0].lower():
        header = [c.strip() for c in lines[0].split("|") if c.strip() or True]
        header = [c.strip() for c in lines[0].split("|")]
        rows = []
        for ln in lines[1:]:
            if set(ln.strip()) <= set("-+| "):
                continue
            parts = [c.strip() for c in ln.split("|")]
            if len(parts) >= len(header):
                rows.append(parts[: len(header)])
        df = pd.DataFrame(rows, columns=header)
    else:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        df = pd.read_csv(path, sep=sep)
    return prepare_login_dataframe(df)


def sessions_on_peak(paths: list[Path], peak_date: date) -> pd.DataFrame:
    frames = []
    for p in paths:
        try:
            df = read_login_csv(p)
            if "login_time" not in df.columns:
                raise ValueError(f"login_time column missing in {p.name}")
            df = df[pd.to_datetime(df["login_time"], errors="coerce").dt.date == peak_date]
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            print(f"WARN: login csv {p.name}: {exc}")
    if not frames:
        return pd.DataFrame(columns=["user_id", "login_time", "logout_time", "duration"])
    return prepare_login_dataframe(pd.concat(frames, ignore_index=True))


def attach_login_csvs(tenants_data, login_index: dict[str, dict[str, list[Path]]], config: dict[str, Any]) -> None:
    for td in tenants_data:
        files = login_index.get(td.key, {})
        for license_key, sheet_name in td.cfg.get("login_sheets", {}).items():
            peak = td.peaks.get(license_key)
            if not peak or not peak.peak_date:
                continue
            paths = files.get(license_key, [])
            if not paths:
                continue
            df = sessions_on_peak(paths, peak.peak_date)
            if not df.empty:
                td.login_sessions[sheet_name] = df
                print(
                    f"  Login CSV: {sheet_name} | peak {peak.peak_date} | {len(df)} sessions"
                )


def build_peak_summary_workbook(tenants_data, config: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for td in tenants_data:
        project = td.cfg.get("project_name", td.key).split("\n")[0]
        arc = td.cfg.get("arc", "")
        for lic, sheet in td.cfg.get("login_sheets", {}).items():
            peak = td.peaks.get(lic)
            if not peak or not peak.peak_date:
                continue
            rows.append(
                {
                    "Tenant": project,
                    "ARC": arc,
                    "License Type": config["license_labels"].get(lic, lic),
                    "Peak Date": peak.peak_date,
                    "Peak Count": peak.peak_count,
                    "Peak Hour": peak.peak_hour or "",
                    "Login Sheet": sheet,
                    "Sessions Loaded": len(td.login_sessions.get(sheet, [])),
                }
            )
    return pd.DataFrame(rows)