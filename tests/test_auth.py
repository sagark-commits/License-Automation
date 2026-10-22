import time

import pytest

from lic_web import auth


def test_password_hash_roundtrip_and_salting():
    h1, h2 = auth.hash_password("S3cure-passw0rd"), auth.hash_password("S3cure-passw0rd")
    assert h1 != h2                                         # random salt
    assert auth.verify_password("S3cure-passw0rd", h1)
    assert not auth.verify_password("wrong", h1)
    assert not auth.verify_password("x", "garbage")


@pytest.mark.parametrize("pw,ok", [("short1A", False), ("alllowercaseletters", False), ("Longenough1", True), ("12345678901", False)])
def test_password_policy(pw, ok):
    assert (auth.check_password_policy("someone", pw) is None) == ok


def test_policy_rejects_username_as_password():
    assert auth.check_password_policy("Administrator1", "administrator1") is not None


def test_user_lifecycle_and_last_admin_protection(tmp_path):
    us = auth.UserStore(tmp_path / "u.json")
    us.create("alice", "Alice-pass-123", "admin")
    us.create("bob", "Bob-pass-12345", "viewer")
    assert us.authenticate("alice", "Alice-pass-123")["role"] == "admin"
    assert us.authenticate("alice", "nope") is None
    assert us.authenticate("ghost", "whatever-1234") is None
    with pytest.raises(ValueError):
        us.create("ALICE", "Another-pass-123", "viewer")          # case-insensitive duplicate
    with pytest.raises(ValueError):
        us.create("x", "Another-pass-123", "viewer")              # bad username
    with pytest.raises(ValueError):
        us.delete("alice")                                        # last admin
    with pytest.raises(ValueError):
        us.set_role("alice", "viewer")
    with pytest.raises(ValueError):
        us.set_disabled("alice", True)
    us.set_disabled("bob", True)
    assert us.authenticate("bob", "Bob-pass-12345") is None       # disabled cannot sign in
    us.set_disabled("bob", False)
    us.set_password("bob", "Brand-new-pass-1")
    assert us.authenticate("bob", "Brand-new-pass-1")
    us.delete("bob")
    assert [u["username"] for u in us.list()] == ["alice"]


def test_user_file_never_contains_plaintext(tmp_path):
    us = auth.UserStore(tmp_path / "u.json")
    us.create("alice", "Plain-secret-9", "admin")
    assert "Plain-secret-9" not in (tmp_path / "u.json").read_text()


def test_sessions_expire_and_can_be_destroyed():
    s = auth.Sessions(idle_seconds=1, absolute_seconds=100)
    sid, sess = s.create({"username": "a", "role": "admin"})
    assert s.get(sid)["user"] == "a"
    time.sleep(1.2)
    assert s.get(sid) is None
    sid, _ = s.create({"username": "a", "role": "admin"})
    s.destroy(sid)
    assert s.get(sid) is None
    assert s.get(None) is None and s.get("nonsense") is None


def test_login_throttle_locks_after_repeated_failures():
    t = auth.LoginThrottle(max_failures=3, window=60, lockout=60)
    for _ in range(2):
        t.failure("1.1.1.1", "alice")
    assert t.blocked_for("1.1.1.1", "alice") == 0
    t.failure("1.1.1.1", "alice")
    assert t.blocked_for("1.1.1.1", "alice") > 0
    assert t.blocked_for("2.2.2.2", "alice") == 0                 # per IP
    t.success("1.1.1.1", "alice")
    assert t.blocked_for("1.1.1.1", "alice") == 0


def test_signed_values_detect_tampering():
    secret = b"k" * 32
    s = auth.sign(secret, "abc")
    assert auth.unsign(secret, s) == "abc"
    assert auth.unsign(secret, s[:-1] + ("0" if s[-1] != "0" else "1")) is None
    assert auth.unsign(b"other" * 8, s) is None


def test_role_permissions():
    assert auth.can("admin", "db") and auth.can("operator", "generate") and not auth.can("operator", "db")
    assert auth.can("viewer", "download") and not auth.can("viewer", "generate") and not auth.can("nobody", "view")
