"""PostgreSQL connection helpers — dual DB: reportsdb (usage) + oneproduct (login)."""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any
import yaml
try:
    import psycopg2
except ImportError:
    psycopg2 = None
LOGIN_DB_DEFAULT = "oneproduct"
USAGE_DB_DEFAULT = "reportsdb"

def load_db_config(script_dir: Path) -> dict[str, Any]:
    path = script_dir / "db_config.yaml"
    if not path.exists():
        return {"enabled": False, "database": {}, "login_database": {}, "usage_database": {}, "arc_databases": {}}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {
        "enabled": bool(data.get("enabled", False)),
        "database": data.get("database", {}) or {},
        "login_database": data.get("login_database", {}) or {},
        "usage_database": data.get("usage_database", {}) or {},
        "arc_databases": data.get("arc_databases", {}) or {},
    }

def _merge_db_settings(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update({k: v for k, v in overrides.items() if v not in (None, "")})
    merged.setdefault("host", "localhost")
    merged.setdefault("port", 5432)
    merged.setdefault("name", "")
    merged.setdefault("user", "")
    merged.setdefault("password", "")
    return merged

def _apply_cli_overrides(settings: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    out = dict(settings)
    if getattr(args, "db_host", None): out["host"] = args.db_host
    if getattr(args, "db_port", None): out["port"] = args.db_port
    if getattr(args, "db_user", None): out["user"] = args.db_user
    if getattr(args, "db_password", None): out["password"] = args.db_password
    if getattr(args, "db_name", None): out["name"] = args.db_name
    return out

def resolve_db_settings(args: argparse.Namespace, script_dir: Path) -> tuple[bool, dict[str, Any]]:
    file_cfg = load_db_config(script_dir)
    verify = bool(getattr(args, "verify_db", False) or getattr(args, "from_db", False) or file_cfg.get("enabled", False))
    shared = _merge_db_settings(file_cfg.get("database", {}), {})
    login_db = _merge_db_settings(shared, file_cfg.get("login_database", {}))
    login_db.setdefault("name", shared.get("name") or LOGIN_DB_DEFAULT)
    login_db = _apply_cli_overrides(login_db, args)
    if getattr(args, "login_db_name", None): login_db["name"] = args.login_db_name
    return verify, login_db

def connect_database(settings: dict[str, Any]):
    if psycopg2 is None:
        raise RuntimeError("psycopg2 required")
    if not settings.get("name") or not settings.get("user"):
        raise RuntimeError("database name and user required in db_config.yaml")
    return psycopg2.connect(host=settings["host"], port=settings["port"], dbname=settings["name"], user=settings["user"], password=settings.get("password", ""), connect_timeout=15)

def _login_and_usage_settings(file_cfg: dict[str, Any], args: argparse.Namespace):
    shared = _merge_db_settings(file_cfg.get("database", {}), {})
    login_db = _merge_db_settings(shared, file_cfg.get("login_database", {}))
    usage_db = _merge_db_settings(shared, file_cfg.get("usage_database", {}))
    login_db.setdefault("name", shared.get("name") or LOGIN_DB_DEFAULT)
    usage_db.setdefault("name", USAGE_DB_DEFAULT)
    login_db = _apply_cli_overrides(login_db, args)
    usage_db = _apply_cli_overrides(usage_db, args)
    if getattr(args, "login_db_name", None): login_db["name"] = args.login_db_name
    if getattr(args, "usage_db_name", None): usage_db["name"] = args.usage_db_name
    return login_db, usage_db

def connect_login_databases(args: argparse.Namespace, script_dir: Path) -> dict[str, dict[str, Any]]:
    """Connect only to oneproduct (peak dates + login sessions). No reportsdb required."""
    file_cfg = load_db_config(script_dir)
    login_base, _ = _login_and_usage_settings(file_cfg, args)
    arc_settings = file_cfg.get("arc_databases") or {}
    login_conns: dict[str, Any] = {}
    if arc_settings:
        for arc, pools in arc_settings.items():
            pools = pools or {}
            login_cfg = pools.get("login", pools)
            login_conns[arc] = connect_database(_merge_db_settings(login_base, login_cfg))
    else:
        login_conns["default"] = connect_database(login_base)
    return {"login": login_conns, "usage": {}}


def connect_dual_databases(
    args: argparse.Namespace,
    script_dir: Path,
    *,
    connect_usage: bool = True,
) -> dict[str, dict[str, Any]]:
    file_cfg = load_db_config(script_dir)
    login_base, usage_base = _login_and_usage_settings(file_cfg, args)
    arc_settings = file_cfg.get("arc_databases") or {}
    usage_conns, login_conns = {}, {}
    if arc_settings:
        for arc, pools in arc_settings.items():
            pools = pools or {}
            login_cfg = pools.get("login", pools)
            usage_cfg = pools.get("usage", pools)
            login_conns[arc] = connect_database(_merge_db_settings(login_base, login_cfg))
            if connect_usage:
                usage_conns[arc] = connect_database(_merge_db_settings(usage_base, usage_cfg))
    else:
        login_conns["default"] = connect_database(login_base)
        if connect_usage:
            usage_conns["default"] = connect_database(usage_base)
    return {"usage": usage_conns, "login": login_conns}

def connect_arc_databases(args: argparse.Namespace, script_dir: Path) -> dict[str, Any]:
    return connect_login_databases(args, script_dir)["login"]

def connection_for_arc(connections: dict[str, Any], arc: str):
    if arc in connections: return connections[arc]
    if "default" in connections: return connections["default"]
    if len(connections) == 1: return next(iter(connections.values()))
    raise KeyError(f"No database connection configured for {arc}")

def close_connections(connections: dict[str, Any]) -> None:
    seen=set()
    for conn in connections.values():
        if id(conn) in seen: continue
        seen.add(id(conn))
        try: conn.close()
        except Exception: pass

def close_connection_pools(pools: dict[str, dict[str, Any]]) -> None:
    for pool in pools.values(): close_connections(pool)