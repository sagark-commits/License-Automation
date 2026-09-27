"""Business logic for the TMONE dashboard, kept free of any Streamlit import.

Deliberately separate from dashboard.py so it can be:
  - unit tested without a running Streamlit script context or a real DB
    (see tests/test_dashboard_core.py)
  - reasoned about independently of UI concerns

Covers:
  - tenants.yaml read/write: locked, atomic (temp file + os.replace), with a
    rolling backup before every mutation and a round-trip validation pass
    before anything touches disk.
  - run_history.json read/append: same lock + atomic-write treatment.
  - a generic stale-aware file lock (FileLock) used for both of the above
    and for a long-lived "a report run is in progress" lock.
  - DB-touching helpers (new-tenant scan, login-count verification, campaign
    discovery) with per-connection statement timeouts and rollback-on-error
    so one bad query can't cascade into failures for every tenant after it.
  - a bounded, cancellable subprocess runner for run_monthly_from_db.py.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from argparse import Namespace
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

from db_connections import close_connection_pools, connect_login_databases, connection_for_arc
from tmone_db_mode import build_tenant_data_from_db, iter_tenants_for_db
from tmone_report import (
    LicensePeak,
    TenantData,
    compute_peaks,
    fetch_login_sessions,
    normalize_month,
)
from tmone_report import load_config as load_tmone_config

SCRIPT_DIR = Path(__file__).resolve().parent
TENANTS_PATH = SCRIPT_DIR / "tenants.yaml"
TENANTS_BACKUP_DIR = SCRIPT_DIR / "tenants_backups"
TENANTS_LOCK_PATH = SCRIPT_DIR / ".tenants.yaml.lock"
OUTPUT_DIR = SCRIPT_DIR / "output"
DB_CONFIG_PATH = SCRIPT_DIR / "db_config.yaml"
RUN_HISTORY_PATH = OUTPUT_DIR / "run_history.json"
RUN_HISTORY_LOCK_PATH = OUTPUT_DIR / ".run_history.json.lock"
REPORT_LOCK_PATH = OUTPUT_DIR / ".report.lock"

ARC_CHOICES = ["ARC-1", "ARC-2"]

MAX_TENANT_BACKUPS = 30
TENANTS_LOCK_STALE_SECONDS = 30          # short critical section: add/remove/campaign edit
REPORT_LOCK_STALE_SECONDS = 3 * 3600 + 300  # must exceed MAX_REPORT_RUNTIME_SECONDS below
MAX_REPORT_RUNTIME_SECONDS = int(os.environ.get("TMONE_DASHBOARD_MAX_RUNTIME_SECONDS", 3 * 3600))
DEFAULT_STATEMENT_TIMEOUT_MS = 60_000  # per-query cap for ad-hoc dashboard SQL only

_ryaml = YAML()
_ryaml.preserve_quotes = True
_ryaml.indent(mapping=2, sequence=4, offset=2)
_ryaml.width = 4096


class TenantValidationError(ValueError):
    """A tenants.yaml mutation was rejected before touching disk."""


class LockTimeoutError(RuntimeError):
    """Could not acquire a file lock within the allotted wait time."""


class ReportAlreadyRunningError(RuntimeError):
    """A monthly report run is already in progress (see .report.lock)."""


# ---------------------------------------------------------------------------
# generic stale-aware file lock
# ---------------------------------------------------------------------------
class FileLock:
    """Advisory, cross-platform lock using an exclusively-created file.

    Not a substitute for real DB-level locking, but enough to stop two
    dashboard sessions (same process, different Streamlit script-run threads,
    or two separate `streamlit run` processes) from interleaving writes to
    the same YAML/JSON file. A lock older than `stale_after` seconds is
    assumed to belong to a crashed holder and is stolen automatically.
    """

    def __init__(self, path: Path, stale_after: float, wait_timeout: float = 5.0, poll_interval: float = 0.1):
        self.path = path
        self.stale_after = stale_after
        self.wait_timeout = wait_timeout
        self.poll_interval = poll_interval
        self._held = False

    def _read_meta(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _is_stale(self) -> bool:
        meta = self._read_meta()
        if not meta or "acquired_at" not in meta:
            # Unreadable / foreign lock file — treat conservatively as stale
            # only if it's old by mtime, so we don't fight a lock mid-write.
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return True
            return age > self.stale_after
        return (time.time() - meta["acquired_at"]) > self.stale_after

    def acquire(self) -> None:
        deadline = time.monotonic() + self.wait_timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(
                        fd,
                        json.dumps(
                            {"pid": os.getpid(), "host": socket.gethostname(), "acquired_at": time.time()}
                        ).encode("utf-8"),
                    )
                finally:
                    os.close(fd)
                self._held = True
                return
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"Could not acquire lock {self.path} — another dashboard action is using it. "
                        "Try again in a few seconds."
                    )
                time.sleep(self.poll_interval)

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# atomic file writes
# ---------------------------------------------------------------------------
def _atomic_write_text(path: Path, content: str) -> None:
    """Write-temp-then-rename so a crash mid-write never truncates the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# tenants.yaml — read
# ---------------------------------------------------------------------------
def load_tenants_raw() -> dict[str, Any]:
    with TENANTS_PATH.open("r", encoding="utf-8") as fh:
        return _ryaml.load(fh) or {}


def load_tenants_plain() -> dict[str, Any]:
    """Plain-dict view (safe_load) for read-only display — no comments/anchors."""
    with TENANTS_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def registered_contact_centers(plain_cfg: dict[str, Any]) -> dict[tuple[str, int], list[str]]:
    registry: dict[tuple[str, int], list[str]] = {}
    for key, cfg in (plain_cfg.get("tenants", {}) or {}).items():
        arc = cfg.get("arc")
        cc = cfg.get("contact_center_id")
        if arc is not None and cc is not None:
            registry.setdefault((arc, int(cc)), []).append(key)
    return registry


def find_cc_siblings(raw_or_plain: dict[str, Any], arc: str, cc_id: int, exclude_key: str | None = None) -> list[dict[str, Any]]:
    """Other tenants sharing (arc, contact_center_id) — informational only.

    Full-tenant + campaign-scoped entries legitimately coexist on the same
    contact center in this registry (e.g. AIG_FM is the full-tenant view of
    cc 14, while IGLOO_247 / BONUSLINK_209 / BONUSLINK_226 / AIG_FMAD_132
    are specific campaigns on that same cc). This is surfaced as a heads-up
    for the operator to double check, never a hard validation failure.
    """
    siblings = []
    for key, cfg in (raw_or_plain.get("tenants", {}) or {}).items():
        if key == exclude_key:
            continue
        if cfg.get("arc") == arc and cfg.get("contact_center_id") is not None and int(cfg["contact_center_id"]) == int(cc_id):
            siblings.append({"key": key, "campaign_ids": list(cfg.get("campaign_ids", []) or [])})
    return siblings


# ---------------------------------------------------------------------------
# tenants.yaml — write (locked, backed up, atomic)
# ---------------------------------------------------------------------------
def _backup_tenants_file() -> None:
    if not TENANTS_PATH.exists():
        return
    TENANTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = TENANTS_BACKUP_DIR / f"tenants_{stamp}.yaml"
    backup_path.write_bytes(TENANTS_PATH.read_bytes())
    _prune_old_backups()


def _prune_old_backups() -> None:
    backups = sorted(TENANTS_BACKUP_DIR.glob("tenants_*.yaml"), key=lambda p: p.name)
    for stale in backups[:-MAX_TENANT_BACKUPS]:
        try:
            stale.unlink()
        except OSError:
            pass


def _dump_yaml_to_string(data: dict[str, Any]) -> str:
    from io import StringIO

    buf = StringIO()
    _ryaml.dump(data, buf)
    return buf.getvalue()


def mutate_tenants(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Load tenants.yaml under lock, apply `mutator` in place, validate, back
    up, and atomically write. Returns the new raw (ruamel) document.

    `mutator` should raise TenantValidationError to abort — no backup or
    write happens if it raises.
    """
    with FileLock(TENANTS_LOCK_PATH, stale_after=TENANTS_LOCK_STALE_SECONDS, wait_timeout=5.0):
        raw = load_tenants_raw()
        mutator(raw)
        rendered = _dump_yaml_to_string(raw)
        # Round-trip check: fail loudly here rather than write something the
        # CLI scripts (plain yaml.safe_load) can't parse.
        reparsed = yaml.safe_load(rendered)
        if not isinstance(reparsed, dict) or "tenants" not in reparsed:
            raise TenantValidationError("Refusing to save — result would not be valid tenants.yaml.")
        _backup_tenants_file()
        _atomic_write_text(TENANTS_PATH, rendered)
        return raw


def validate_new_tenant_key(raw: dict[str, Any], key: str) -> None:
    if not key or not key.strip():
        raise TenantValidationError("Tenant key is required.")
    if any(ch.isspace() for ch in key):
        raise TenantValidationError("Tenant key can't contain whitespace.")
    if key in (raw.get("tenants", {}) or {}):
        raise TenantValidationError(f"Tenant key '{key}' already exists.")


def add_tenant(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    def _mutate(raw: dict[str, Any]) -> None:
        validate_new_tenant_key(raw, key)
        raw.setdefault("tenants", {})[key] = entry

    return mutate_tenants(_mutate)


def remove_tenant(key: str) -> dict[str, Any]:
    def _mutate(raw: dict[str, Any]) -> None:
        if key not in (raw.get("tenants", {}) or {}):
            raise TenantValidationError(f"'{key}' not found — already removed?")
        del raw["tenants"][key]

    return mutate_tenants(_mutate)


def set_tenant_campaigns(key: str, campaign_ids: list[str] | None) -> dict[str, Any]:
    """campaign_ids=None (or empty) means 'full tenant, no campaign filter'."""

    def _mutate(raw: dict[str, Any]) -> None:
        tenants = raw.get("tenants", {}) or {}
        if key not in tenants:
            raise TenantValidationError(f"'{key}' not found — was it removed by someone else?")
        cfg = tenants[key]
        if campaign_ids:
            seq = CommentedSeq([str(c) for c in campaign_ids])
            seq.fa.set_flow_style()
            cfg["campaign_ids"] = seq
        else:
            cfg.pop("campaign_ids", None)

    return mutate_tenants(_mutate)


# ---------------------------------------------------------------------------
# run history — read/append (locked, atomic)
# ---------------------------------------------------------------------------
def load_run_history() -> list[dict[str, Any]]:
    if not RUN_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(RUN_HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def append_run_history(entry: dict[str, Any]) -> None:
    with FileLock(RUN_HISTORY_LOCK_PATH, stale_after=TENANTS_LOCK_STALE_SECONDS, wait_timeout=5.0):
        history = load_run_history()
        history.append(entry)
        _atomic_write_text(RUN_HISTORY_PATH, json.dumps(history, indent=2))


# ---------------------------------------------------------------------------
# tenant selection preview (no DB — mirrors run_monthly_from_db.py's filter)
# ---------------------------------------------------------------------------
def tenants_matching_run(arc_choice: str, active_only: bool) -> list[str]:
    config = load_tmone_config(TENANTS_PATH)
    arc_filter = None if arc_choice == "Both" else arc_choice
    return [key for key, _ in iter_tenants_for_db(config, None, arc_filter, active_only=active_only)]


# ---------------------------------------------------------------------------
# DB helpers — connection hygiene shared by scan / verify / campaigns
# ---------------------------------------------------------------------------
def _login_args() -> Namespace:
    return Namespace(
        db_host=None, db_port=None, db_user=None, db_password=None,
        db_name=None, login_db_name=None,
    )


def _apply_statement_timeout(conn: Any, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> None:
    """Best-effort per-connection query cap so one slow/blocked query can't
    hang the whole dashboard action. Failure to set it is non-fatal."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (timeout_ms,))
        conn.commit()
    except Exception:
        _safe_rollback(conn)


def _safe_rollback(conn: Any) -> None:
    """psycopg2 aborts the whole transaction after any failed statement —
    every subsequent query on that connection fails until rolled back. Call
    this after any query that might have failed, even ones we handled, so
    tenant N's error can't silently break tenants N+1..end on the same ARC."""
    try:
        conn.rollback()
    except Exception:
        pass


def scan_new_contact_centers() -> list[dict[str, Any]]:
    """Contact centers active in the last 30 days, per ARC, not yet in tenants.yaml."""
    plain = load_tenants_plain()
    registry = registered_contact_centers(plain)

    pools = connect_login_databases(_login_args(), SCRIPT_DIR)
    login_conns = pools.get("login", {})
    query = """
        SELECT DISTINCT bc.contact_center_id
        FROM user_session_history mc
        JOIN users bc ON mc.user_id = bc.user_id
        WHERE mc.login_time >= now() - interval '30 days'
        ORDER BY 1
    """
    results: list[dict[str, Any]] = []
    try:
        for arc, conn in login_conns.items():
            _apply_statement_timeout(conn)
            try:
                with conn.cursor() as cur:
                    cur.execute(query)
                    cc_ids = [r[0] for r in cur.fetchall()]
            except Exception as exc:
                _safe_rollback(conn)
                results.append(
                    {"arc": arc, "contact_center_id": None, "registered_as": "", "status": "error", "note": str(exc)}
                )
                continue
            for cc in cc_ids:
                tenant_keys = registry.get((arc, int(cc)), [])
                results.append(
                    {
                        "arc": arc,
                        "contact_center_id": int(cc),
                        "registered_as": ", ".join(tenant_keys) if tenant_keys else "",
                        "status": "registered" if tenant_keys else "new",
                        "note": "",
                    }
                )
    finally:
        close_connection_pools(pools)
    return results


def verify_login_counts(month_str: str, arc_choice: str, active_only: bool) -> list[dict[str, Any]]:
    """Recompute peaks + peak-hour login sessions and compare distinct user
    counts. peak_count (hourly usage query) and the distinct user_id count in
    the peak-hour login session query describe the same concurrent-login
    moment and must match; a mismatch means the two workbooks disagree,
    which is exactly what would produce a wrong billing number."""
    config = load_tmone_config(TENANTS_PATH)
    arc_filter = None if arc_choice == "Both" else arc_choice
    month = normalize_month(month_str)

    pools = connect_login_databases(_login_args(), SCRIPT_DIR)
    login_conns = pools.get("login", {})
    for conn in login_conns.values():
        _apply_statement_timeout(conn)

    rows: list[dict[str, Any]] = []
    try:
        for key, cfg in iter_tenants_for_db(config, None, arc_filter, active_only=active_only):
            arc = cfg.get("arc", "ARC-1")
            try:
                conn = connection_for_arc(login_conns, arc)
            except KeyError:
                continue

            td = build_tenant_data_from_db(
                key, cfg, conn, config, month,
                TenantData=TenantData, compute_peaks=compute_peaks,
                LicensePeak=LicensePeak, normalize_month=normalize_month,
            )
            _safe_rollback(conn)  # defensive: build_tenant_data_from_db swallows its own errors
            if td is None or not td.peaks:
                continue

            try:
                fetch_login_sessions(conn, td, config)
            except Exception as exc:
                _safe_rollback(conn)
                rows.append(
                    {
                        "tenant": key, "arc": arc, "license": "-", "sheet": "-",
                        "peak_date": None, "peak_hour": None,
                        "utilization_peak_count": None, "login_session_users": None,
                        "match": False, "note": f"login query failed: {exc}",
                    }
                )
                continue
            _safe_rollback(conn)

            for license_key, sheet_name in (cfg.get("login_sheets", {}) or {}).items():
                peak_key = "agent" if license_key == "executive" else license_key
                peak = td.peaks.get(peak_key) or td.peaks.get(license_key)
                if not peak or not peak.peak_date or peak.peak_count <= 0:
                    continue
                session_df = td.login_sessions.get(sheet_name)
                distinct_users = int(session_df["user_id"].nunique()) if session_df is not None and not session_df.empty else 0
                rows.append(
                    {
                        "tenant": key,
                        "arc": arc,
                        "license": license_key,
                        "sheet": sheet_name,
                        "peak_date": str(peak.peak_date),
                        "peak_hour": peak.peak_hour,
                        "utilization_peak_count": peak.peak_count,
                        "login_session_users": distinct_users,
                        "match": distinct_users == peak.peak_count,
                        "note": "",
                    }
                )
    finally:
        close_connection_pools(pools)
    return rows


def fetch_campaigns_for_cc(arc: str, cc_id: int) -> list[dict[str, Any]]:
    """List every campaign configured for a contact center, and which of them
    had login activity in the last 30 days.

    campaign_context normally has id/name; if that column layout differs on a
    given ARC, fall back to the distinct campaign_ids actually used in
    campaign_user_working_history so the tenant still gets *a* usable list.
    """
    pools = connect_login_databases(_login_args(), SCRIPT_DIR)
    login_conns = pools.get("login", {})
    rows: dict[str, dict[str, Any]] = {}
    try:
        conn = connection_for_arc(login_conns, arc)
        _apply_statement_timeout(conn)

        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name FROM campaign_context WHERE contact_center_id = %(cc)s ORDER BY id",
                    {"cc": cc_id},
                )
                for cid, name in cur.fetchall():
                    rows[str(cid)] = {"campaign_id": str(cid), "name": name or "", "active_last_30d": False}
        except Exception:
            _safe_rollback(conn)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT campaign_id FROM campaign_user_working_history "
                        "WHERE contact_center_id = %(cc)s ORDER BY 1",
                        {"cc": cc_id},
                    )
                    for (cid,) in cur.fetchall():
                        rows[str(cid)] = {"campaign_id": str(cid), "name": "", "active_last_30d": False}
            except Exception:
                _safe_rollback(conn)

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT c.campaign_id
                    FROM campaign_user_working_history c
                    JOIN user_session_history u ON c.session_id = u.session_id
                    WHERE c.contact_center_id = %(cc)s
                      AND u.login_time >= now() - interval '30 days'
                    """,
                    {"cc": cc_id},
                )
                for (cid,) in cur.fetchall():
                    rows.setdefault(str(cid), {"campaign_id": str(cid), "name": "", "active_last_30d": False})
                    rows[str(cid)]["active_last_30d"] = True
        except Exception:
            _safe_rollback(conn)
    finally:
        close_connection_pools(pools)
    return sorted(rows.values(), key=lambda r: (len(r["campaign_id"]), r["campaign_id"]))


# ---------------------------------------------------------------------------
# report-run lock + bounded subprocess runner
# ---------------------------------------------------------------------------
def report_lock_status() -> dict[str, Any] | None:
    """Current holder of the report-run lock, or None if free (or stale)."""
    lock = FileLock(REPORT_LOCK_PATH, stale_after=REPORT_LOCK_STALE_SECONDS, wait_timeout=0)
    if not REPORT_LOCK_PATH.exists():
        return None
    if lock._is_stale():
        return None
    return lock._read_meta()


def force_clear_report_lock() -> None:
    """Manual escape hatch for ops: the process died without releasing the
    lock (e.g. `kill -9`, server reboot) and the stale-after window hasn't
    elapsed yet. Only meant to be exposed behind an explicit confirmation."""
    try:
        REPORT_LOCK_PATH.unlink()
    except OSError:
        pass


@dataclass
class SubprocessResult:
    returncode: int | None
    timed_out: bool
    lines: list[str]


def list_output_files() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(OUTPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)


def run_report_subprocess(
    cmd: list[str],
    cwd: Path,
    on_line: Callable[[str], None] | None = None,
    timeout_seconds: int = MAX_REPORT_RUNTIME_SECONDS,
    lock_meta: dict[str, Any] | None = None,
) -> SubprocessResult:
    """Run run_monthly_from_db.py under the report lock, streaming lines to
    `on_line`, with a hard wall-clock cap so a stuck DB connection can't hang
    the dashboard forever. Raises ReportAlreadyRunningError if another run
    holds the lock."""
    import subprocess

    existing = report_lock_status()
    if existing is not None:
        raise ReportAlreadyRunningError(
            f"A report run is already in progress (pid {existing.get('pid')} on "
            f"{existing.get('host')}, started {existing.get('acquired_at')})."
        )

    lock = FileLock(REPORT_LOCK_PATH, stale_after=REPORT_LOCK_STALE_SECONDS, wait_timeout=0)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lock.acquire()
    if lock_meta:
        try:
            REPORT_LOCK_PATH.write_text(
                json.dumps({**json.loads(REPORT_LOCK_PATH.read_text(encoding="utf-8")), **lock_meta}),
                encoding="utf-8",
            )
        except OSError:
            pass

    lines: list[str] = []
    timed_out = False
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    start = time.monotonic()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if on_line:
                on_line(lines[-1])
            if time.monotonic() - start > timeout_seconds:
                timed_out = True
                proc.kill()
                lines.append(f"ERROR: run exceeded {timeout_seconds}s timeout — killed.")
                if on_line:
                    on_line(lines[-1])
                break
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        lock.release()
    return SubprocessResult(returncode=proc.returncode, timed_out=timed_out, lines=lines)


def check_db_config_permissions() -> str | None:
    """Warn if db_config.yaml (real DB credentials) is group/world readable.
    POSIX only — a no-op (returns None) on Windows, where st_mode bits don't
    carry the same meaning."""
    if os.name != "posix" or not DB_CONFIG_PATH.exists():
        return None
    try:
        mode = DB_CONFIG_PATH.stat().st_mode
    except OSError:
        return None
    if mode & 0o077:
        return (
            f"{DB_CONFIG_PATH.name} is readable by group/other (mode {oct(mode)[-3:]}). "
            f"Run: chmod 600 {DB_CONFIG_PATH}"
        )
    return None


def month_is_incomplete_or_future(month_str: str) -> bool:
    """True if the selected month hasn't fully elapsed yet (partial billing data)."""
    month = normalize_month(month_str)
    y, m = (int(x) for x in month.split("-"))
    today = date.today()
    return (y, m) >= (today.year, today.month)
