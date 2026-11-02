"""Users, password hashing, sessions, CSRF tokens and login throttling (standard library only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .store import atomic_write

ROLES = ("admin", "operator", "viewer")
PBKDF2_ITERATIONS = 240000
MIN_PASSWORD_LEN = 10
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{2,31}$")

# what each role may do
PERMISSIONS = {
    "viewer": {"view", "download"},
    "operator": {"view", "download", "generate", "tenants", "settings"},
    "admin": {"view", "download", "generate", "tenants", "settings", "db", "users", "audit", "delete_runs"},
}


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    iterations = iterations or PBKDF2_ITERATIONS          # looked up at call time (tests lower it)
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def check_password_policy(username: str, password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD_LEN:
        return "Password must be at least %d characters." % MIN_PASSWORD_LEN
    if password.lower() == username.lower():
        return "Password must not be the same as the username."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 2:
        return "Use at least two kinds of characters (letters, digits, symbols)."
    return None


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _save(self, users: Dict[str, Dict[str, Any]]) -> None:
        atomic_write(self.path, json.dumps(users, indent=2, sort_keys=True).encode("utf-8"))

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"username": u, "role": d["role"], "disabled": bool(d.get("disabled")), "created": d.get("created", ""),
                     "last_login": d.get("last_login", "")} for u, d in sorted(self._load().items())]

    def count(self) -> int:
        with self._lock:
            return len(self._load())

    def create(self, username: str, password: str, role: str) -> None:
        if not USERNAME_RE.match(username):
            raise ValueError("Username must be 3-32 characters: letters, digits, . _ - and start with a letter.")
        if role not in ROLES:
            raise ValueError("Unknown role.")
        problem = check_password_policy(username, password)
        if problem:
            raise ValueError(problem)
        with self._lock:
            users = self._load()
            if username.lower() in {u.lower() for u in users}:
                raise ValueError("User already exists.")
            users[username] = {"hash": hash_password(password), "role": role, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save(users)

    def set_password(self, username: str, password: str) -> None:
        problem = check_password_policy(username, password)
        if problem:
            raise ValueError(problem)
        with self._lock:
            users = self._load()
            if username not in users:
                raise ValueError("No such user.")
            users[username]["hash"] = hash_password(password)
            self._save(users)

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError("Unknown role.")
        with self._lock:
            users = self._load()
            if username not in users:
                raise ValueError("No such user.")
            if users[username]["role"] == "admin" and role != "admin" and self._admin_count(users) <= 1:
                raise ValueError("Cannot demote the last administrator.")
            users[username]["role"] = role
            self._save(users)

    def set_disabled(self, username: str, disabled: bool) -> None:
        with self._lock:
            users = self._load()
            if username not in users:
                raise ValueError("No such user.")
            if disabled and users[username]["role"] == "admin" and self._admin_count(users) <= 1:
                raise ValueError("Cannot disable the last administrator.")
            users[username]["disabled"] = disabled
            self._save(users)

    def delete(self, username: str) -> None:
        with self._lock:
            users = self._load()
            if username not in users:
                raise ValueError("No such user.")
            if users[username]["role"] == "admin" and self._admin_count(users) <= 1:
                raise ValueError("Cannot delete the last administrator.")
            del users[username]
            self._save(users)

    @staticmethod
    def _admin_count(users: Dict[str, Dict[str, Any]]) -> int:
        return sum(1 for d in users.values() if d["role"] == "admin" and not d.get("disabled"))

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Return {'username','role'} on success. Always does one PBKDF2 so timing does not reveal valid names."""
        with self._lock:
            users = self._load()
        rec = users.get(username)
        ok = verify_password(password, rec["hash"] if rec else _DUMMY_HASH)
        if not rec or not ok or rec.get("disabled"):
            return None
        with self._lock:
            users = self._load()
            if username in users:
                users[username]["last_login"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save(users)
        return {"username": username, "role": rec["role"]}

    def get(self, username: str) -> Optional[Dict[str, Any]]:
        rec = self._load().get(username)
        if not rec or rec.get("disabled"):
            return None
        return {"username": username, "role": rec["role"]}


class Sessions:
    """Server-side sessions (in memory; a restart logs everybody out - acceptable for an internal tool)."""

    def __init__(self, idle_seconds: int = 30 * 60, absolute_seconds: int = 12 * 3600):
        self.idle, self.absolute = idle_seconds, absolute_seconds
        self._s: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, user: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        sess = {"user": user["username"], "role": user["role"], "csrf": secrets.token_urlsafe(24), "created": now, "seen": now}
        with self._lock:
            self._purge(now)
            self._s[sid] = sess
        return sid, sess

    def get(self, sid: Optional[str]) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            sess = self._s.get(sid)
            if not sess:
                return None
            if now - sess["seen"] > self.idle or now - sess["created"] > self.absolute:
                del self._s[sid]
                return None
            sess["seen"] = now
            return sess

    def destroy(self, sid: Optional[str]) -> None:
        with self._lock:
            self._s.pop(sid or "", None)

    def destroy_user(self, username: str) -> None:
        with self._lock:
            for sid in [k for k, v in self._s.items() if v["user"] == username]:
                del self._s[sid]

    def _purge(self, now: float) -> None:
        for sid in [k for k, v in self._s.items() if now - v["seen"] > self.idle or now - v["created"] > self.absolute]:
            del self._s[sid]


class LoginThrottle:
    """5 failures within 10 minutes lock that username+IP for 5 minutes."""

    def __init__(self, max_failures: int = 5, window: int = 600, lockout: int = 300):
        self.max, self.window, self.lockout = max_failures, window, lockout
        self._f: Dict[str, List[float]] = {}
        self._locked: Dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(ip: str, username: str) -> str:
        return "%s|%s" % (ip, username.lower())

    def blocked_for(self, ip: str, username: str) -> int:
        with self._lock:
            until = self._locked.get(self._key(ip, username), 0)
            return max(0, int(until - time.time()))

    def failure(self, ip: str, username: str) -> None:
        k, now = self._key(ip, username), time.time()
        with self._lock:
            fails = [t for t in self._f.get(k, []) if now - t < self.window] + [now]
            self._f[k] = fails
            if len(fails) >= self.max:
                self._locked[k] = now + self.lockout
                self._f[k] = []
            if len(self._f) > 5000:      # evict only expired entries so a flood cannot reset live counters
                self._f = {k: v for k, v in self._f.items() if v and now - v[-1] < self.window}

    def success(self, ip: str, username: str) -> None:
        with self._lock:
            self._f.pop(self._key(ip, username), None)
            self._locked.pop(self._key(ip, username), None)


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())


def load_or_create_secret(path: Path) -> bytes:
    path = Path(path)
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    data = secrets.token_bytes(48)
    atomic_write(path, data)
    return data


def sign(secret: bytes, value: str) -> str:
    mac = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return "%s.%s" % (value, mac)


def unsign(secret: bytes, signed: str) -> Optional[str]:
    if not signed or "." not in signed:
        return None
    value, _, mac = signed.rpartition(".")
    good = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(mac, good) else None
