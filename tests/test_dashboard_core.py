"""Unit tests for dashboard_core.py — no Streamlit, no real DB.

DB-touching functions (scan_new_contact_centers, verify_login_counts,
fetch_campaigns_for_cc) are tested by monkeypatching the connection layer
with fakes, so these run offline and fast.

Run with:  pip install -r requirements-dev.txt && pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_core as core  # noqa: E402
from tmone_report import LicensePeak, TenantData  # noqa: E402

MINIMAL_TENANTS_YAML = """\
# TMONE License Utilization - tenant registry
defaults:
  agent_user_types: [Professional-Agent]
  supervisor_user_types: [Supervisor]
  wallboard_user_types: [Wallboard User]
  active_contact_centers:
    ARC-1: [7]
    ARC-2: [5]

tenants:
  TESCO:
    arc: ARC-2
    project_name: TESCO
    sheet_name: TESCO
    contact_center_id: 5
    login_sheets:
      agent: Ameyo-Express_TESCO

  MBSP:
    arc: ARC-1
    project_name: MBSP
    sheet_name: MBSP-ARC-1
    contact_center_id: 7
    login_sheets:
      agent: Ameyo-Express-MBSP-ARC-1

  PBAPP:
    arc: ARC-1
    project_name: PBAPP
    sheet_name: PBAPP-ARC-1
    contact_center_id: 20
    login_sheets:
      agent: Ameyo-Express-PBAPP
"""


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    """Point every dashboard_core path constant at a scratch directory."""
    tenants_path = tmp_path / "tenants.yaml"
    tenants_path.write_text(MINIMAL_TENANTS_YAML, encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    monkeypatch.setattr(core, "TENANTS_PATH", tenants_path)
    monkeypatch.setattr(core, "TENANTS_BACKUP_DIR", tmp_path / "tenants_backups")
    monkeypatch.setattr(core, "TENANTS_LOCK_PATH", tmp_path / ".tenants.yaml.lock")
    monkeypatch.setattr(core, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(core, "DB_CONFIG_PATH", tmp_path / "db_config.yaml")
    monkeypatch.setattr(core, "RUN_HISTORY_PATH", output_dir / "run_history.json")
    monkeypatch.setattr(core, "RUN_HISTORY_LOCK_PATH", output_dir / ".run_history.json.lock")
    monkeypatch.setattr(core, "REPORT_LOCK_PATH", output_dir / ".report.lock")
    return tmp_path


# ---------------------------------------------------------------------------
# atomic write
# ---------------------------------------------------------------------------
class TestAtomicWrite:
    def test_writes_content_and_leaves_no_tmp_files(self, tmp_path):
        target = tmp_path / "sub" / "file.txt"
        core._atomic_write_text(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"
        leftovers = list(tmp_path.rglob(".*.tmp"))
        assert leftovers == []

    def test_failure_cleans_up_tmp_and_preserves_original(self, tmp_path, monkeypatch):
        target = tmp_path / "file.txt"
        target.write_text("original", encoding="utf-8")

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(core.os, "replace", _boom)
        with pytest.raises(OSError):
            core._atomic_write_text(target, "new content")

        assert target.read_text(encoding="utf-8") == "original"
        assert list(tmp_path.glob(".*.tmp")) == []


# ---------------------------------------------------------------------------
# FileLock
# ---------------------------------------------------------------------------
class TestFileLock:
    def test_acquire_release_roundtrip(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        lock = core.FileLock(lock_path, stale_after=60, wait_timeout=1)
        with lock:
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_second_acquire_raises_when_held_and_fresh(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        holder = core.FileLock(lock_path, stale_after=60, wait_timeout=0)
        holder.acquire()
        try:
            contender = core.FileLock(lock_path, stale_after=60, wait_timeout=0)
            with pytest.raises(core.LockTimeoutError):
                contender.acquire()
        finally:
            holder.release()

    def test_stale_lock_is_stolen(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        lock_path.write_text(json.dumps({"pid": 999999, "acquired_at": time.time() - 3600}), encoding="utf-8")
        contender = core.FileLock(lock_path, stale_after=30, wait_timeout=0)
        contender.acquire()  # should steal, not raise
        contender.release()
        assert not lock_path.exists()

    def test_blocks_until_released_by_another_thread(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        holder = core.FileLock(lock_path, stale_after=60, wait_timeout=0)
        holder.acquire()

        def _release_soon():
            time.sleep(0.3)
            holder.release()

        threading.Thread(target=_release_soon).start()
        contender = core.FileLock(lock_path, stale_after=60, wait_timeout=2)
        contender.acquire()  # should succeed once the other thread releases
        contender.release()

    def test_stale_lock_that_cannot_be_unlinked_does_not_spin_forever(self, tmp_path, monkeypatch):
        """If a stale lock exists but unlink() keeps failing (permissions, an
        AV/indexer holding it open, an NFS quirk), acquire() must still hit
        its deadline and raise — not spin at 100% CPU forever re-checking the
        same un-removable file with no sleep between attempts."""
        lock_path = tmp_path / "x.lock"
        lock_path.write_text(json.dumps({"pid": 1, "acquired_at": time.time() - 3600}), encoding="utf-8")

        real_unlink = Path.unlink

        def _always_fail_unlink(self, *a, **k):
            if self == lock_path:
                raise OSError("permission denied")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", _always_fail_unlink)
        contender = core.FileLock(lock_path, stale_after=30, wait_timeout=1, poll_interval=0.05)
        start = time.monotonic()
        with pytest.raises(core.LockTimeoutError):
            contender.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 3, f"acquire() took {elapsed:.2f}s to give up — looks like it was spinning"


# ---------------------------------------------------------------------------
# tenants.yaml mutations
# ---------------------------------------------------------------------------
class TestTenantMutations:
    def test_add_tenant_persists_and_is_parseable(self, isolated_paths):
        core.add_tenant("NEWCO", {"arc": "ARC-1", "project_name": "NEWCO", "sheet_name": "NEWCO", "contact_center_id": 99})
        plain = core.load_tenants_plain()
        assert "NEWCO" in plain["tenants"]
        assert plain["tenants"]["NEWCO"]["contact_center_id"] == 99
        # comment header preserved
        assert core.TENANTS_PATH.read_text(encoding="utf-8").startswith("# TMONE")

    def test_add_tenant_creates_backup(self, isolated_paths):
        assert not core.TENANTS_BACKUP_DIR.exists()
        core.add_tenant("NEWCO", {"arc": "ARC-1", "project_name": "N", "sheet_name": "N", "contact_center_id": 99})
        backups = list(core.TENANTS_BACKUP_DIR.glob("tenants_*.yaml"))
        assert len(backups) == 1
        # backup holds the PRE-mutation content (no NEWCO yet)
        assert "NEWCO" not in backups[0].read_text(encoding="utf-8")

    def test_add_tenant_duplicate_key_raises_and_does_not_write(self, isolated_paths):
        before = core.TENANTS_PATH.read_text(encoding="utf-8")
        with pytest.raises(core.TenantValidationError):
            core.add_tenant("TESCO", {"arc": "ARC-2", "project_name": "x", "sheet_name": "x", "contact_center_id": 1})
        assert core.TENANTS_PATH.read_text(encoding="utf-8") == before
        assert not core.TENANTS_BACKUP_DIR.exists()  # no backup on a rejected mutation

    @pytest.mark.parametrize("bad_key", ["", "   ", "HAS SPACE", "TESCO"])
    def test_invalid_or_duplicate_keys_rejected(self, isolated_paths, bad_key):
        raw = core.load_tenants_raw()
        with pytest.raises(core.TenantValidationError):
            core.validate_new_tenant_key(raw, bad_key)

    def test_remove_tenant(self, isolated_paths):
        core.remove_tenant("TESCO")
        plain = core.load_tenants_plain()
        assert "TESCO" not in plain["tenants"]
        assert "MBSP" in plain["tenants"]  # untouched

    def test_remove_unknown_tenant_raises(self, isolated_paths):
        with pytest.raises(core.TenantValidationError):
            core.remove_tenant("DOES_NOT_EXIST")

    def test_set_tenant_campaigns_specific_then_full(self, isolated_paths):
        core.set_tenant_campaigns("MBSP", ["10", "20"])
        plain = core.load_tenants_plain()
        assert plain["tenants"]["MBSP"]["campaign_ids"] == ["10", "20"]

        core.set_tenant_campaigns("MBSP", None)
        plain = core.load_tenants_plain()
        assert "campaign_ids" not in plain["tenants"]["MBSP"]

    def test_set_tenant_campaigns_missing_tenant_raises(self, isolated_paths):
        with pytest.raises(core.TenantValidationError):
            core.set_tenant_campaigns("GHOST", ["1"])

    def test_concurrent_adds_do_not_lose_either_write(self, isolated_paths):
        """Two REAL threads racing to add different tenants at (as close to)
        the same instant as possible must both land — this is the lost-update
        race a naive load/modify/save would hit. A prior version of this test
        called add_tenant() twice back-to-back on one thread, which can never
        interleave and would pass even with locking removed entirely."""
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def _add(key: str, cc: int) -> None:
            barrier.wait(timeout=5)  # line both threads up before either mutates
            try:
                core.add_tenant(key, {"arc": "ARC-1", "project_name": key, "sheet_name": key, "contact_center_id": cc})
            except Exception as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        t1 = threading.Thread(target=_add, args=("ONE", 101))
        t2 = threading.Thread(target=_add, args=("TWO", 102))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"add_tenant raised under concurrency: {errors}"
        plain = core.load_tenants_plain()
        assert "ONE" in plain["tenants"] and "TWO" in plain["tenants"]

    def test_mutate_tenants_rejects_non_roundtripping_yaml_and_writes_nothing(self, isolated_paths, monkeypatch):
        """The round-trip guard (dump -> yaml.safe_load -> must be a dict with
        'tenants') is the last line of defense against writing something the
        CLI billing scripts can't parse. Force it to fail and confirm it
        actually raises AND leaves the file/backups untouched, rather than
        writing anyway."""
        monkeypatch.setattr(core, "_dump_yaml_to_string", lambda data: "not: [valid, {yaml")
        before = core.TENANTS_PATH.read_text(encoding="utf-8")

        with pytest.raises(core.TenantValidationError):
            core.add_tenant("NEWCO", {"arc": "ARC-1", "project_name": "N", "sheet_name": "N", "contact_center_id": 99})

        assert core.TENANTS_PATH.read_text(encoding="utf-8") == before
        assert not core.TENANTS_BACKUP_DIR.exists()

    def test_backup_pruning_caps_at_max_tenant_backups(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(core, "MAX_TENANT_BACKUPS", 3)
        for i in range(6):
            core.add_tenant(f"T{i}", {"arc": "ARC-1", "project_name": "x", "sheet_name": "x", "contact_center_id": 200 + i})
        backups = list(core.TENANTS_BACKUP_DIR.glob("tenants_*.yaml"))
        assert len(backups) == 3, f"expected pruning to cap backups at 3, found {len(backups)}"


# ---------------------------------------------------------------------------
# registry / selection helpers
# ---------------------------------------------------------------------------
class TestRegistryHelpers:
    def test_registered_contact_centers(self, isolated_paths):
        plain = core.load_tenants_plain()
        registry = core.registered_contact_centers(plain)
        assert registry[("ARC-2", 5)] == ["TESCO"]
        assert registry[("ARC-1", 7)] == ["MBSP"]

    def test_find_cc_siblings_excludes_self(self, isolated_paths):
        plain = core.load_tenants_plain()
        # MBSP and nothing else share (ARC-1, 7) in the fixture
        siblings = core.find_cc_siblings(plain, "ARC-1", 7, exclude_key="MBSP")
        assert siblings == []

    def test_find_cc_siblings_finds_shared_cc(self, isolated_paths):
        core.add_tenant("MBSP_CAMPAIGN_X", {"arc": "ARC-1", "project_name": "x", "sheet_name": "x", "contact_center_id": 7, "campaign_ids": ["50"]})
        plain = core.load_tenants_plain()
        siblings = core.find_cc_siblings(plain, "ARC-1", 7, exclude_key="MBSP")
        assert [s["key"] for s in siblings] == ["MBSP_CAMPAIGN_X"]
        assert siblings[0]["campaign_ids"] == ["50"]

    def test_tenants_matching_run_active_only_filters(self, isolated_paths):
        all_arc1 = core.tenants_matching_run("ARC-1", active_only=False)
        assert set(all_arc1) == {"MBSP", "PBAPP"}
        active_arc1 = core.tenants_matching_run("ARC-1", active_only=True)
        assert active_arc1 == ["MBSP"]  # PBAPP's cc 20 isn't in active_contact_centers

    def test_tenants_matching_run_both_arcs(self, isolated_paths):
        both = core.tenants_matching_run("Both", active_only=False)
        assert set(both) == {"TESCO", "MBSP", "PBAPP"}


class TestLoadTenantsPlainCache:
    def test_reflects_mutation_after_write(self, isolated_paths):
        before = core.load_tenants_plain()
        assert "NEWCO" not in before["tenants"]
        core.add_tenant("NEWCO", {"arc": "ARC-1", "project_name": "N", "sheet_name": "N", "contact_center_id": 55})
        after = core.load_tenants_plain()
        assert "NEWCO" in after["tenants"], "cache must invalidate once the file actually changed"

    def test_does_not_leak_between_different_paths_with_equal_mtime(self, tmp_path, monkeypatch):
        """The cache key must include the path, not just mtime -- two
        different tenants.yaml files (e.g. two tests' isolated tmp_path
        fixtures) could plausibly land on the same mtime, especially on
        filesystems with coarse timestamp resolution or fast successive
        writes. Force that exact collision here and confirm no leakage."""
        path_a = tmp_path / "a" / "tenants.yaml"
        path_b = tmp_path / "b" / "tenants.yaml"
        path_a.parent.mkdir(parents=True)
        path_b.parent.mkdir(parents=True)
        path_a.write_text("tenants:\n  A_TENANT:\n    arc: ARC-1\n    contact_center_id: 1\n", encoding="utf-8")
        path_b.write_text("tenants:\n  B_TENANT:\n    arc: ARC-1\n    contact_center_id: 2\n", encoding="utf-8")

        forced_mtime = path_a.stat().st_mtime
        os.utime(path_b, (forced_mtime, forced_mtime))
        assert path_a.stat().st_mtime == path_b.stat().st_mtime  # collision actually forced

        monkeypatch.setattr(core, "TENANTS_PATH", path_a)
        result_a = core.load_tenants_plain()
        monkeypatch.setattr(core, "TENANTS_PATH", path_b)
        result_b = core.load_tenants_plain()

        assert "A_TENANT" in result_a["tenants"]
        assert "B_TENANT" in result_b["tenants"]
        assert "A_TENANT" not in result_b["tenants"], "must not have served A's cached data for B's path"


# ---------------------------------------------------------------------------
# run history
# ---------------------------------------------------------------------------
class TestRunHistory:
    def test_empty_when_absent(self, isolated_paths):
        assert core.load_run_history() == []

    def test_append_and_load_roundtrip(self, isolated_paths):
        core.append_run_history({"month": "2026-07", "tenant_count": 3})
        core.append_run_history({"month": "2026-08", "tenant_count": 4})
        history = core.load_run_history()
        assert [h["month"] for h in history] == ["2026-07", "2026-08"]

    def test_corrupt_history_file_treated_as_empty(self, isolated_paths):
        core.RUN_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.RUN_HISTORY_PATH.write_text("{not valid json", encoding="utf-8")
        assert core.load_run_history() == []

    def test_valid_json_wrong_type_treated_as_empty_not_a_crash(self, isolated_paths):
        """{} and {"note": "..."} are both VALID json.loads() results, just
        not a list -- without this check, append_run_history's
        history.append(entry) blows up with an unhandled AttributeError on a
        dict, right after a report has already been successfully written."""
        core.RUN_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.RUN_HISTORY_PATH.write_text('{"note": "someone reset this by hand"}', encoding="utf-8")
        assert core.load_run_history() == []
        core.append_run_history({"month": "2026-09"})  # must not raise
        assert [h["month"] for h in core.load_run_history()] == ["2026-09"]


# ---------------------------------------------------------------------------
# report lock + subprocess runner
# ---------------------------------------------------------------------------
class TestReportLockAndSubprocess:
    def test_report_lock_status_none_when_absent(self, isolated_paths):
        assert core.report_lock_status() is None

    def test_report_lock_status_none_when_stale(self, isolated_paths):
        core.REPORT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.REPORT_LOCK_PATH.write_text(
            json.dumps({"pid": 123, "acquired_at": time.time() - core.REPORT_LOCK_STALE_SECONDS - 10}),
            encoding="utf-8",
        )
        assert core.report_lock_status() is None

    def test_run_report_subprocess_success(self, isolated_paths):
        cmd = [sys.executable, "-u", "-c", "print('line1'); print('line2')"]
        seen = []
        result = core.run_report_subprocess(cmd, cwd=isolated_paths, on_line=seen.append, timeout_seconds=30)
        assert result.returncode == 0
        assert not result.timed_out
        assert "line1" in seen and "line2" in seen
        assert not core.REPORT_LOCK_PATH.exists()  # released

    def test_run_report_subprocess_already_running_raises(self, isolated_paths):
        core.REPORT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.REPORT_LOCK_PATH.write_text(json.dumps({"pid": 1, "acquired_at": time.time()}), encoding="utf-8")
        with pytest.raises(core.ReportAlreadyRunningError):
            core.run_report_subprocess([sys.executable, "-c", "pass"], cwd=isolated_paths)

    def test_run_report_subprocess_timeout_kills_and_releases_lock(self, isolated_paths):
        cmd = [
            sys.executable, "-u", "-c",
            "import time,sys\nfor i in range(20):\n print(i); sys.stdout.flush(); time.sleep(0.3)",
        ]
        result = core.run_report_subprocess(cmd, cwd=isolated_paths, timeout_seconds=1)
        assert result.timed_out is True
        assert any("exceeded" in line for line in result.lines)
        assert not core.REPORT_LOCK_PATH.exists()  # lock released even after a kill

    def test_run_report_subprocess_kills_completely_silent_child(self, isolated_paths):
        """A child that produces ZERO stdout output (no print at all) must
        still be detected and killed by the wall-clock timeout. This is the
        exact gap the review found: the old implementation only checked the
        deadline inside `for line in proc.stdout:`, so a silent child could
        never be caught regardless of timeout_seconds."""
        cmd = [sys.executable, "-u", "-c", "import time; time.sleep(30)"]
        start = time.monotonic()
        result = core.run_report_subprocess(cmd, cwd=isolated_paths, timeout_seconds=1, poll_interval=0.2)
        elapsed = time.monotonic() - start
        assert result.timed_out is True
        assert elapsed < 10, f"took {elapsed:.1f}s to kill a silent child with a 1s timeout"
        assert not core.REPORT_LOCK_PATH.exists()

    def test_run_report_subprocess_releases_lock_even_if_popen_itself_fails(self, isolated_paths):
        """Critical finding: subprocess.Popen(cmd, ...) used to run BEFORE the
        try/finally that releases the lock, so an interpreter path that
        doesn't exist (e.g. a mistyped 'Server Python binary' field) would
        leak .report.lock for the full stale window instead of releasing it."""
        cmd = ["this-binary-definitely-does-not-exist-12345", "-c", "pass"]
        with pytest.raises(OSError):
            core.run_report_subprocess(cmd, cwd=isolated_paths, timeout_seconds=5)
        assert not core.REPORT_LOCK_PATH.exists(), "lock leaked after a failed Popen()"

    def test_run_report_subprocess_check_then_acquire_race_raises_already_running(self, isolated_paths, monkeypatch):
        """TOCTOU: report_lock_status() can say 'free' and then lock.acquire()
        can still lose the race to a concurrent caller. That must surface as
        ReportAlreadyRunningError (which dashboard.py catches), not the
        sibling LockTimeoutError (which it doesn't)."""
        monkeypatch.setattr(core, "report_lock_status", lambda: None)  # pretend it's free
        core.REPORT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.REPORT_LOCK_PATH.write_text(json.dumps({"pid": 1, "acquired_at": time.time()}), encoding="utf-8")

        with pytest.raises(core.ReportAlreadyRunningError):
            core.run_report_subprocess([sys.executable, "-c", "pass"], cwd=isolated_paths)

    def test_run_report_subprocess_merges_lock_meta(self, isolated_paths):
        seen_meta = {}

        def _on_line(line):
            if not seen_meta:
                try:
                    seen_meta.update(json.loads(core.REPORT_LOCK_PATH.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    pass

        cmd = [sys.executable, "-u", "-c", "print('x')"]
        core.run_report_subprocess(cmd, cwd=isolated_paths, on_line=_on_line, lock_meta={"month": "2026-07", "arc": "Both"})
        assert seen_meta.get("month") == "2026-07"
        assert seen_meta.get("arc") == "Both"
        assert "pid" in seen_meta  # original acquire() metadata preserved by the merge


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
class TestMisc:
    def test_month_is_incomplete_or_future(self):
        assert core.month_is_incomplete_or_future("2020-01") is False
        from datetime import date
        today = date.today()
        assert core.month_is_incomplete_or_future(f"{today.year:04d}-{today.month:02d}") is True

    def test_check_db_config_permissions_none_when_absent(self, isolated_paths):
        assert core.check_db_config_permissions() is None

    @pytest.mark.skipif(__import__("os").name != "posix", reason="permission bits are POSIX-only")
    def test_check_db_config_permissions_warns_when_open(self, isolated_paths):
        core.DB_CONFIG_PATH.write_text("enabled: true", encoding="utf-8")
        core.DB_CONFIG_PATH.chmod(0o644)
        assert core.check_db_config_permissions() is not None

    def test_check_db_config_permissions_warning_logic_is_os_independent(self, isolated_paths, monkeypatch):
        """The real warning-generation logic (mode & 0o077) is only ever
        exercised by test_check_db_config_permissions_warns_when_open, which
        is skipped on non-POSIX hosts (including this Windows dev box) — so
        a Windows-only CI run could show '38/39 passed' while this credential-
        exposure check never actually ran. Fake POSIX mode bits so the branch
        is verified regardless of host OS."""
        core.DB_CONFIG_PATH.write_text("enabled: true", encoding="utf-8")
        monkeypatch.setattr(core.os, "name", "posix")

        class _FakeStatWorldReadable:
            st_mode = 0o100644  # regular file, rw-r--r--

        class _FakeStatOwnerOnly:
            st_mode = 0o100600  # regular file, rw-------

        monkeypatch.setattr(Path, "stat", lambda self: _FakeStatWorldReadable())
        warning = core.check_db_config_permissions()
        assert warning is not None and "chmod 600" in warning

        monkeypatch.setattr(Path, "stat", lambda self: _FakeStatOwnerOnly())
        assert core.check_db_config_permissions() is None


# ---------------------------------------------------------------------------
# DB-touching functions — exercised against fakes, never a real DB
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        handler = self._conn.responses_for(sql)
        if isinstance(handler, Exception):
            raise handler
        self._rows = handler or []

    def fetchall(self):
        return self._rows


class FakeConnection:
    """Models real psycopg2 closely enough to catch a missing rollback: once
    a statement raises, the connection is 'aborted' and every subsequent
    execute() raises InFailedSqlTransaction-like errors until rollback()
    is called — exactly like a real Postgres connection. A fake that just
    kept answering from `rules` regardless of prior failures would let a
    missing _safe_rollback() call pass tests while failing against a real DB."""

    class AbortedTransaction(RuntimeError):
        pass

    def __init__(self, rules: list[tuple[str, object]]):
        self.rules = rules  # (substring, rows-or-Exception), first match wins
        self.executed: list[str] = []
        self.rollback_calls = 0
        self.commit_calls = 0
        self._aborted = False

    def responses_for(self, sql):
        if self._aborted:
            return self.AbortedTransaction("current transaction is aborted, commands ignored until end of transaction block")
        for substring, result in self.rules:
            if substring in sql:
                if isinstance(result, Exception):
                    self._aborted = True
                return result
        return []

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollback_calls += 1
        self._aborted = False

    def commit(self):
        self.commit_calls += 1


class TestScanNewContactCenters:
    def test_flags_new_and_registered(self, isolated_paths, monkeypatch):
        conn_arc1 = FakeConnection([("SET statement_timeout", []), ("SELECT DISTINCT bc.contact_center_id", [(7,), (14,)])])
        conn_arc2 = FakeConnection([("SET statement_timeout", []), ("SELECT DISTINCT bc.contact_center_id", [(5,)])])
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": conn_arc1, "ARC-2": conn_arc2}})
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        results = core.scan_new_contact_centers()
        by_cc = {(r["arc"], r["contact_center_id"]): r["status"] for r in results}
        assert by_cc[("ARC-1", 7)] == "registered"   # MBSP
        assert by_cc[("ARC-1", 14)] == "new"
        assert by_cc[("ARC-2", 5)] == "registered"   # TESCO

    def test_query_failure_reported_and_does_not_abort_other_arc(self, isolated_paths, monkeypatch):
        conn_arc1 = FakeConnection([("SET statement_timeout", []), ("SELECT DISTINCT bc.contact_center_id", RuntimeError("connection reset"))])
        conn_arc2 = FakeConnection([("SET statement_timeout", []), ("SELECT DISTINCT bc.contact_center_id", [(5,)])])
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": conn_arc1, "ARC-2": conn_arc2}})
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        results = core.scan_new_contact_centers()
        statuses = {r["arc"]: r["status"] for r in results if r["arc"] == "ARC-1"}
        assert statuses["ARC-1"] == "error"
        assert conn_arc1.rollback_calls >= 1
        assert any(r["arc"] == "ARC-2" and r["status"] == "registered" for r in results)


class TestFetchCampaignsForCc:
    def test_primary_query_success(self, isolated_paths, monkeypatch):
        conn = FakeConnection([
            ("SET statement_timeout", []),
            ("FROM campaign_context", [(1, "Sales"), (2, "Support")]),
            ("JOIN user_session_history", [(2,)]),
        ])
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": conn}})
        monkeypatch.setattr(core, "connection_for_arc", lambda conns, arc: conns[arc])
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        campaigns = core.fetch_campaigns_for_cc("ARC-1", 7)
        by_id = {c["campaign_id"]: c for c in campaigns}
        assert by_id["1"]["name"] == "Sales" and by_id["1"]["active_last_30d"] is False
        assert by_id["2"]["active_last_30d"] is True

    def test_falls_back_when_campaign_context_query_fails(self, isolated_paths, monkeypatch):
        conn = FakeConnection([
            ("SET statement_timeout", []),
            ("FROM campaign_context", RuntimeError("column name does not exist")),
            # unique to the fallback query (no "c." alias, no JOIN) — query 3
            # below shares "FROM campaign_user_working_history" as a substring,
            # so this pattern must not match it.
            ("DISTINCT campaign_id FROM campaign_user_working_history", [(99,)]),
            ("JOIN user_session_history", []),
        ])
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": conn}})
        monkeypatch.setattr(core, "connection_for_arc", lambda conns, arc: conns[arc])
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        campaigns = core.fetch_campaigns_for_cc("ARC-1", 7)
        assert campaigns == [{"campaign_id": "99", "name": "", "active_last_30d": False}]
        assert conn.rollback_calls >= 1


class TestVerifyLoginCounts:
    def test_mismatch_row(self, isolated_paths, monkeypatch):
        cfg = {"arc": "ARC-1", "login_sheets": {"agent": "SheetA"}}
        monkeypatch.setattr(core, "iter_tenants_for_db", lambda config, keys, arc_filter, active_only: iter([("TESTCO", cfg)]))
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": FakeConnection([])}})
        monkeypatch.setattr(core, "connection_for_arc", lambda conns, arc: conns[arc])
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        td = TenantData(key="TESTCO", cfg=cfg, usage_df=pd.DataFrame())
        td.peaks = {"agent": LicensePeak(license_key="agent", license_label="Agent", peak_date="2026-07-15", peak_count=5, peak_hour=14)}

        def _fake_fetch_login_sessions(conn, td_arg, config):
            td_arg.login_sessions["SheetA"] = pd.DataFrame({"user_id": ["u1", "u2", "u3", "u4"]})  # only 4, peak says 5

        monkeypatch.setattr(core, "build_tenant_data_from_db", lambda *a, **k: td)
        monkeypatch.setattr(core, "fetch_login_sessions", _fake_fetch_login_sessions)

        rows = core.verify_login_counts("2026-07", "Both", False)
        assert len(rows) == 1
        assert rows[0]["utilization_peak_count"] == 5
        assert rows[0]["login_session_users"] == 4
        assert rows[0]["match"] is False

    def test_match_row(self, isolated_paths, monkeypatch):
        """A regression that broke the comparison (e.g. flipping `==` to `!=`,
        or hardcoding False) would still pass test_mismatch_row above, since
        4 != 5 either way — this is the only test that requires `match` to
        ever come back True, so it's the one that actually exercises the
        happy path of the billing cross-check."""
        cfg = {"arc": "ARC-1", "login_sheets": {"agent": "SheetA"}}
        monkeypatch.setattr(core, "iter_tenants_for_db", lambda config, keys, arc_filter, active_only: iter([("TESTCO", cfg)]))
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": FakeConnection([])}})
        monkeypatch.setattr(core, "connection_for_arc", lambda conns, arc: conns[arc])
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        td = TenantData(key="TESTCO", cfg=cfg, usage_df=pd.DataFrame())
        td.peaks = {"agent": LicensePeak(license_key="agent", license_label="Agent", peak_date="2026-07-15", peak_count=4, peak_hour=14)}

        def _fake_fetch_login_sessions(conn, td_arg, config):
            td_arg.login_sessions["SheetA"] = pd.DataFrame({"user_id": ["u1", "u2", "u3", "u4"]})  # 4, peak also 4

        monkeypatch.setattr(core, "build_tenant_data_from_db", lambda *a, **k: td)
        monkeypatch.setattr(core, "fetch_login_sessions", _fake_fetch_login_sessions)

        rows = core.verify_login_counts("2026-07", "Both", False)
        assert len(rows) == 1
        assert rows[0]["utilization_peak_count"] == 4
        assert rows[0]["login_session_users"] == 4
        assert rows[0]["match"] is True

    def test_zero_sessions_gets_a_note_not_a_silent_blank(self, isolated_paths, monkeypatch):
        """tmone_report.fetch_login_sessions() catches its own per-sheet query
        exceptions internally and substitutes an empty DataFrame instead of
        re-raising, so a silent query failure and a genuine zero-session peak
        hour are otherwise indistinguishable on the one screen whose job is
        catching wrong billing numbers. A 0-vs-N mismatch must carry a note
        explaining that ambiguity, not blank note=""."""
        cfg = {"arc": "ARC-1", "login_sheets": {"agent": "SheetA"}}
        monkeypatch.setattr(core, "iter_tenants_for_db", lambda config, keys, arc_filter, active_only: iter([("TESTCO", cfg)]))
        monkeypatch.setattr(core, "connect_login_databases", lambda args, script_dir: {"login": {"ARC-1": FakeConnection([])}})
        monkeypatch.setattr(core, "connection_for_arc", lambda conns, arc: conns[arc])
        monkeypatch.setattr(core, "close_connection_pools", lambda pools: None)

        td = TenantData(key="TESTCO", cfg=cfg, usage_df=pd.DataFrame())
        td.peaks = {"agent": LicensePeak(license_key="agent", license_label="Agent", peak_date="2026-07-15", peak_count=3, peak_hour=14)}

        def _fake_fetch_login_sessions(conn, td_arg, config):
            td_arg.login_sessions["SheetA"] = pd.DataFrame(columns=["user_id"])  # empty -- 0 sessions

        monkeypatch.setattr(core, "build_tenant_data_from_db", lambda *a, **k: td)
        monkeypatch.setattr(core, "fetch_login_sessions", _fake_fetch_login_sessions)

        rows = core.verify_login_counts("2026-07", "Both", False)
        assert len(rows) == 1
        assert rows[0]["login_session_users"] == 0
        assert rows[0]["match"] is False
        assert rows[0]["note"], "a 0-session mismatch must explain the ambiguity, not be blank"
