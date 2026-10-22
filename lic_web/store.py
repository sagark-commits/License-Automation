"""Persistent state of the web UI: tenant registry, database settings, audit log.

Everything lives in one data directory (default ``<repo>/data``, never in git):

  config/tenants.yaml      tenant registry + global user-type rules (same format the CLI scripts read)
  config/db_config.yaml    database connections per ARC (contains passwords -> mode 600)
  config/history/          a timestamped copy of tenants.yaml before every save (one-click rollback)
  auth/users.json          UI users (PBKDF2 password hashes)
  auth/secret.key          session-signing secret
  runs/<run-id>/           generated workbooks + manifest + log of every report run
  audit.log                who changed / ran / downloaded what
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

PLACEHOLDERS = {"your_db_user", "your_db_password", ""}
DEFAULT_ARCS = ["ARC-1", "ARC-2"]
HISTORY_KEEP = 100


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same directory + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _safe_actor(actor: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", actor or "system")[:32]


class DataDir:
    def __init__(self, root: Path, repo_dir: Path):
        self.root = Path(root).resolve()
        self.repo_dir = Path(repo_dir).resolve()
        self.config_dir = self.root / "config"
        self.history_dir = self.config_dir / "history"
        self.tenants_path = self.config_dir / "tenants.yaml"
        self.db_config_path = self.config_dir / "db_config.yaml"
        self.auth_dir = self.root / "auth"
        self.users_path = self.auth_dir / "users.json"
        self.secret_path = self.auth_dir / "secret.key"
        self.runs_dir = self.root / "runs"
        self.audit_path = self.root / "audit.log"
        self.settings_path = self.config_dir / "settings.json"
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ setup
    def init(self) -> None:
        for d in (self.root, self.config_dir, self.history_dir, self.auth_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        if not self.tenants_path.exists():
            seed = self.repo_dir / "tenants.yaml"
            if seed.exists():
                atomic_write(self.tenants_path, seed.read_bytes().lstrip(b"\xef\xbb\xbf"), 0o640)
            else:
                atomic_write(self.tenants_path, yaml.safe_dump({"defaults": {}, "tenants": {}}).encode("utf-8"), 0o640)
        if not self.db_config_path.exists():
            skeleton = {
                "enabled": True,
                "arc_databases": {arc: {"login": {"host": "", "port": 5432, "name": "oneproduct", "user": "", "password": ""}}
                                  for arc in DEFAULT_ARCS},
            }
            atomic_write(self.db_config_path, yaml.safe_dump(skeleton, sort_keys=False).encode("utf-8"))

    # ------------------------------------------------------------------ registry
    def load_registry(self) -> Dict[str, Any]:
        with self._lock:
            with self.tenants_path.open(encoding="utf-8-sig") as fh:
                data = yaml.safe_load(fh) or {}
        data.setdefault("defaults", {})
        data["tenants"] = data.get("tenants") or {}
        return data

    def save_registry(self, registry: Dict[str, Any], actor: str, note: str = "") -> None:
        text = yaml.safe_dump(
            {"defaults": registry.get("defaults", {}), "tenants": registry.get("tenants", {})},
            sort_keys=False, allow_unicode=True, default_flow_style=False, width=120,
        )
        with self._lock:
            if self.tenants_path.exists():
                stamp = time.strftime("%Y%m%d-%H%M%S") + "%03d" % (int(time.time() * 1000) % 1000)   # ms: saves in one second still sort in order
                shutil.copy2(self.tenants_path, self.history_dir / ("tenants-%s-%s.yaml" % (stamp, _safe_actor(actor))))
                backups = sorted(self.history_dir.glob("tenants-*.yaml"))
                for old in backups[:-HISTORY_KEEP]:
                    old.unlink()
            atomic_write(self.tenants_path, ("# Managed by the License Utilization web UI - saved by %s\n" % _safe_actor(actor) + text).encode("utf-8"), 0o640)
        self.audit(actor, "registry.save", note)

    def list_history(self) -> List[str]:
        return [p.name for p in sorted(self.history_dir.glob("tenants-*.yaml"), reverse=True)]

    def restore_history(self, name: str, actor: str) -> None:
        if not re.fullmatch(r"tenants-\d{8}-\d{6,9}-[A-Za-z0-9_.-]{1,32}\.yaml", name):
            raise ValueError("invalid backup name")
        src = self.history_dir / name
        if not src.is_file():
            raise ValueError("backup not found")
        registry = yaml.safe_load(src.read_text(encoding="utf-8-sig")) or {}
        self.save_registry(registry, actor, "restored %s" % name)

    # ------------------------------------------------------------------ database settings
    def _load_db_raw(self) -> Dict[str, Any]:
        with self._lock:
            if not self.db_config_path.exists():
                return {}
            with self.db_config_path.open(encoding="utf-8-sig") as fh:
                return yaml.safe_load(fh) or {}

    def arcs(self) -> List[str]:
        raw = self._load_db_raw()
        arcs = list((raw.get("arc_databases") or {}).keys()) or list(DEFAULT_ARCS)
        for cfg in self.load_registry()["tenants"].values():
            a = cfg.get("arc")
            if a and a not in arcs:
                arcs.append(a)
        return arcs

    def db_view(self) -> Dict[str, Dict[str, Any]]:
        """Effective per-ARC connection settings WITHOUT passwords (safe to render)."""
        raw = self._load_db_raw()
        shared = dict(raw.get("database") or {})
        login_shared = dict(raw.get("login_database") or {})
        out: Dict[str, Dict[str, Any]] = {}
        for arc, pools in (raw.get("arc_databases") or {}).items():
            pools = pools or {}
            login = dict(pools.get("login", pools) or {})
            merged = {**shared, **login_shared, **{k: v for k, v in login.items() if v not in (None, "")}}
            password = merged.get("password") or ""
            user = merged.get("user") or ""
            out[arc] = {
                "host": merged.get("host", ""),
                "port": int(merged.get("port") or 5432),
                "name": merged.get("name") or "oneproduct",
                "user": "" if user in PLACEHOLDERS else user,
                "has_password": password not in PLACEHOLDERS,
                "sslmode": merged.get("sslmode", ""),
                "statement_timeout_ms": int(merged.get("statement_timeout_ms") or 600000),
            }
        return out

    def db_settings_for_connect(self, arc: str) -> Dict[str, Any]:
        """Full settings incl. password, for the connection test / discovery (server side only)."""
        raw = self._load_db_raw()
        shared = dict(raw.get("database") or {})
        login_shared = dict(raw.get("login_database") or {})
        pools = (raw.get("arc_databases") or {}).get(arc)
        if pools is None:
            raise KeyError("ARC %s is not configured" % arc)
        login = dict(pools.get("login", pools) or {})
        merged = {**shared, **login_shared, **{k: v for k, v in login.items() if v not in (None, "")}}
        merged.setdefault("port", 5432)
        merged.setdefault("name", "oneproduct")
        return merged

    def db_configured(self) -> bool:
        view = self.db_view()
        return bool(view) and all(v["host"] and v["user"] and v["has_password"] for v in view.values())

    def save_db(self, arc: str, settings: Dict[str, Any], actor: str) -> None:
        """Update one ARC. A blank password keeps the stored one."""
        with self._lock:
            raw = self._load_db_raw()
            arcs = raw.setdefault("arc_databases", {})
            existing = dict((arcs.get(arc) or {}).get("login", arcs.get(arc)) or {})
            shared_pw = (raw.get("database") or {}).get("password", "")
            password = settings.get("password") or existing.get("password") or shared_pw or ""
            entry = {
                "host": settings["host"], "port": int(settings["port"]), "name": settings["name"],
                "user": settings["user"], "password": password,
            }
            if settings.get("sslmode"):
                entry["sslmode"] = settings["sslmode"]
            if settings.get("statement_timeout_ms"):
                entry["statement_timeout_ms"] = int(settings["statement_timeout_ms"])
            arcs[arc] = {"login": dict(entry), "usage": {k: entry[k] for k in ("host", "name")}}
            raw["enabled"] = True
            atomic_write(self.db_config_path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8"))
        self.audit(actor, "db.save", "%s host=%s db=%s user=%s password=%s" % (
            arc, settings["host"], settings["name"], settings["user"], "changed" if settings.get("password") else "kept"))

    # ------------------------------------------------------------------ ui settings
    def get_settings(self) -> Dict[str, Any]:
        data = {"keep_runs": 24}
        if self.settings_path.exists():
            try:
                data.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
            except ValueError:
                pass
        return data

    def save_settings(self, data: Dict[str, Any], actor: str) -> None:
        cur = self.get_settings()
        cur.update(data)
        atomic_write(self.settings_path, json.dumps(cur, indent=2).encode("utf-8"), 0o640)
        self.audit(actor, "settings.save", json.dumps(data))

    # ------------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, detail: str = "", ip: Optional[str] = None) -> None:
        line = json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": _safe_actor(actor), "action": action,
            "detail": str(detail)[:500], "ip": ip or "",
        }, ensure_ascii=False)
        with self._lock:
            try:
                if self.audit_path.exists() and self.audit_path.stat().st_size > 5 * 1024 * 1024:
                    os.replace(self.audit_path, self.audit_path.with_name("audit.log.1"))
                with self.audit_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def read_audit(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        with self._lock:
            lines = self.audit_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        out = []
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
