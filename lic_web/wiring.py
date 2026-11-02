"""Builds the application object (data dir, users, job runner, WSGI app)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .app import App
from .auth import UserStore
from .jobs import JobManager
from .store import DataDir

REPO_DIR = Path(__file__).resolve().parent.parent


def default_runner(data: DataDir) -> Callable[..., Dict[str, Any]]:
    """Runs the real report engine against the databases configured in the data directory."""

    def runner(month: str, *, arc: Optional[str], tenants: Optional[List[str]], output_dir: Path, progress, should_cancel, previous_summary):
        import report_service

        return report_service.run_report(
            month, config_path=data.tenants_path, db_config_path=data.db_config_path, output_dir=output_dir,
            arc=arc, tenants=tenants, progress=progress, should_cancel=should_cancel, previous_summary=previous_summary)

    return runner


def create_app(
    data_dir: Path,
    repo_dir: Optional[Path] = None,
    *,
    runner: Optional[Callable[..., Dict[str, Any]]] = None,
    trust_proxy: bool = False,
    secure_cookies: bool = False,
    start_worker: bool = True,
) -> App:
    repo = Path(repo_dir) if repo_dir else REPO_DIR
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))          # the report engine modules live in the repo root
    data = DataDir(Path(data_dir), repo)
    data.init()
    users = UserStore(data.users_path)
    jobs = JobManager(data, runner or default_runner(data), keep_runs=int(data.get_settings().get("keep_runs", 24)))
    jobs.timeout_min = int(data.get_settings().get("run_timeout_min", 30))
    app = App(data, jobs, users, trust_proxy=trust_proxy, secure_cookies=secure_cookies)
    if start_worker:
        jobs.start()
    return app
