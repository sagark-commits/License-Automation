import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if p not in sys.path:
        sys.path.insert(0, p)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_password_hashing(monkeypatch):
    """PBKDF2 at production strength makes the suite slow; the algorithm is identical."""
    from lic_web import auth

    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 2000)
    monkeypatch.setattr(auth, "_DUMMY_HASH", auth.hash_password("dummy-password-for-timing"))
