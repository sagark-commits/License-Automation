"""Read-only look at an Ameyo database: connection test and 'what is in this contact center?'.

Used by the Settings -> Databases "Test connection" button and by the Add-tenant wizard, which asks the
database which user types a new contact center really uses so the right Agent/Supervisor/Wallboard
types can be chosen instead of guessed.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

REQUIRED_TABLES = ("user_session_history", "users", "campaign_user_working_history", "campaign_context", "contact_center")
NAME_COLUMNS = ("name", "campaign_name", "campaign_context_name", "description")


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _query(conn, sql: str, params: Optional[tuple] = None) -> List[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def test_connection(settings: Dict[str, Any], connect=None) -> Dict[str, Any]:
    """Connect with the saved settings and check that the tables the report needs are there."""
    if connect is None:
        from db_connections import connect_database as connect
    started = time.time()
    try:
        conn = connect(settings)
    except Exception as exc:
        return {"ok": False, "message": "Connection failed: %s" % (str(exc).strip().splitlines() or ["unknown error"])[0]}
    try:
        version = _query(conn, "SELECT version()")[0][0].split(",")[0]
        ro = _query(conn, "SELECT current_setting('transaction_read_only')")[0][0]
        missing = []
        for t in REQUIRED_TABLES:
            if _query(conn, "SELECT to_regclass(%s)", (t,))[0][0] is None:
                missing.append(t)
        ms = int((time.time() - started) * 1000)
        if missing:
            return {"ok": False, "version": version, "ms": ms,
                    "message": "Connected, but these tables were not found: %s. Is this the Ameyo 'oneproduct' database?" % ", ".join(missing)}
        return {"ok": True, "version": version, "ms": ms, "read_only": ro == "on",
                "message": "Connected in %d ms (%s). All report tables found." % (ms, version)}
    except Exception as exc:
        return {"ok": False, "message": "Connected, but a test query failed: %s" % (str(exc).strip().splitlines() or ["error"])[0]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def discover_contact_center(conn, cc_id: int, config: Dict[str, Any], days: int = 90) -> Dict[str, Any]:
    """What does this contact center look like? Every part is optional: a failing query only adds an error."""
    from user_type_config import classify_user_type, norm_type

    out: Dict[str, Any] = {"contact_center_id": cc_id, "name": None, "user_types": [], "campaigns": [], "wallboard_users": [], "errors": []}
    cc_text = str(int(cc_id))
    try:
        _query(conn, "SET statement_timeout = 30000")
    except Exception:
        pass

    try:
        rows = _query(conn, "SELECT name FROM contact_center WHERE id = %s", (int(cc_id),))
        out["name"] = _jsonable(rows[0][0]) if rows else None
        if not rows:
            out["errors"].append("No contact center with id %s was found in this database." % cc_id)
    except Exception as exc:
        out["errors"].append("contact center name: %s" % (str(exc).strip().splitlines() or ["error"])[0])

    try:
        rows = _query(
            conn,
            "SELECT d.user_type, count(*) AS sessions, count(DISTINCT u.user_id) AS users, max(u.login_time) AS last_login "
            "FROM user_session_history u JOIN users d ON d.user_id = u.user_id "
            "WHERE d.contact_center_id::text = %s AND u.login_time >= now() - (%s * interval '1 day') "
            "GROUP BY d.user_type ORDER BY 2 DESC",
            (cc_text, int(days)),
        )
        ignored = {norm_type(x) for x in config.get("ignored_types", [])}
        for ut, sessions, users, last in rows:
            bucket = classify_user_type(ut, config, None)
            if bucket is None and norm_type(ut) in ignored:
                bucket = "ignored"
            out["user_types"].append({"type": _jsonable(ut), "sessions": int(sessions), "users": int(users),
                                      "last_login": _jsonable(last), "bucket": bucket})
        if not rows:
            out["errors"].append("No sessions in the last %d days - the user types below cannot be suggested." % days)
    except Exception as exc:
        out["errors"].append("user types: %s" % (str(exc).strip().splitlines() or ["error"])[0])

    try:
        cols = {r[0] for r in _query(conn, "SELECT column_name FROM information_schema.columns WHERE table_name = 'campaign_context'")}
        name_col = next((c for c in NAME_COLUMNS if c in cols), None)
        sql = "SELECT id%s FROM campaign_context WHERE contact_center_id::text = %%s ORDER BY id" % (", %s" % name_col if name_col else "")
        for r in _query(conn, sql, (cc_text,))[:200]:
            out["campaigns"].append({"id": str(r[0]), "name": _jsonable(r[1]) if name_col else None})
    except Exception as exc:
        out["errors"].append("campaigns: %s" % (str(exc).strip().splitlines() or ["error"])[0])

    try:
        for uid, ut in _query(
            conn,
            "SELECT user_id, user_type FROM users WHERE contact_center_id::text = %s "
            "AND (user_id ILIKE '%%wallboard%%' OR user_type ILIKE '%%wallboard%%') ORDER BY user_id LIMIT 20",
            (cc_text,),
        ):
            out["wallboard_users"].append({"user_id": _jsonable(uid), "user_type": _jsonable(ut)})
    except Exception as exc:
        out["errors"].append("wallboard users: %s" % (str(exc).strip().splitlines() or ["error"])[0])
    return out
