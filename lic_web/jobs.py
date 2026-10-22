"""Background report runs: a single worker thread, persisted run records, progress and cancel."""
from __future__ import annotations

import json
import os
import logging
import re
import secrets
import shutil
import threading
import time
import traceback
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from .store import DataDir, atomic_write

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
STAGE_RANGE = {"queued": (0, 0), "connect": (0, 5), "usage": (5, 60), "login": (60, 90), "write": (90, 95), "verify": (95, 99), "done": (100, 100)}
FINISHED = ("done", "failed", "cancelled", "interrupted")
MAX_QUEUED = 5
MAX_EVENTS = 2000

Runner = Callable[..., Dict[str, Any]]
log = logging.getLogger("lic_web.jobs")


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id or ""))


class _EventHandler(logging.Handler):
    """Copies engine log lines (logger 'tmone') into the run's event list while it runs."""

    def __init__(self, sink: Callable[[str], None], thread_id: int):
        super().__init__(level=logging.INFO)
        self.sink, self.thread_id = sink, thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id:
            prefix = "WARN " if record.levelno >= logging.WARNING else ""
            self.sink(prefix + record.getMessage())


class JobManager:
    def __init__(self, data: DataDir, runner: Runner, keep_runs: int = 24):
        self.data, self.runner, self.keep_runs = data, runner, keep_runs
        self._cv = threading.Condition()
        self._zip_lock = threading.Lock()
        self._queue: Deque[str] = deque()
        self._state: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[str, List[str]] = {}
        self._cancel: set = set()
        self._worker: Optional[threading.Thread] = None
        self._stop = False
        self._load_existing()

    # ------------------------------------------------------------------ persistence
    def _run_dir(self, run_id: str) -> Path:
        if not is_valid_run_id(run_id):
            raise ValueError("invalid run id")
        return self.data.runs_dir / run_id

    def _persist(self, run_id: str) -> None:
        state = self._state[run_id]
        try:
            atomic_write(self._run_dir(run_id) / "run.json", json.dumps(state, indent=1, default=str).encode("utf-8"), 0o640)
        except OSError:
            log.exception("could not persist run %s", run_id)

    def _load_existing(self) -> None:
        if not self.data.runs_dir.exists():
            return
        for d in self.data.runs_dir.iterdir():
            f = d / "run.json"
            if d.is_dir() and is_valid_run_id(d.name) and f.exists():
                try:
                    st = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if st.get("status") in ("queued", "running"):
                    st["status"] = "interrupted"
                    st["error"] = "The server was restarted while this run was in progress."
                    st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._state[d.name] = st
                    self._persist(d.name)
                self._state[d.name] = st
                ev = d / "events.log"
                if ev.exists():
                    self._events[d.name] = ev.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_EVENTS:]

    # ------------------------------------------------------------------ public API
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop = False
        self._worker = threading.Thread(target=self._loop, name="report-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def submit(self, month: str, arc: Optional[str], tenants: Optional[List[str]], user: str) -> str:
        with self._cv:
            queued = sum(1 for s in self._state.values() if s["status"] in ("queued", "running"))
            if queued >= MAX_QUEUED:
                raise ValueError("Too many reports are already queued or running (%d). Wait for one to finish." % queued)
            run_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2))
            self._run_dir(run_id).mkdir(parents=True, exist_ok=True)
            self._state[run_id] = {
                "id": run_id, "month": month, "arc": arc, "tenants": tenants, "user": user,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"), "started": None, "finished": None,
                "status": "queued", "progress": {"stage": "queued", "i": 0, "n": 0, "message": "Waiting for the worker", "percent": 0},
                "result": None, "error": None,
            }
            self._events[run_id] = []
            self._persist(run_id)
            self._queue.append(run_id)
            self._cv.notify_all()
        self.data.audit(user, "report.submit", "run=%s month=%s arc=%s tenants=%s" % (run_id, month, arc or "all", ",".join(tenants) if tenants else "all"))
        return run_id

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        if not is_valid_run_id(run_id):
            return None
        with self._cv:
            st = self._state.get(run_id)
            return json.loads(json.dumps(st, default=str)) if st else None

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._cv:
            items = sorted(self._state.values(), key=lambda s: s["id"], reverse=True)[:limit]
            return [{k: v for k, v in s.items() if k != "result"} | {"health": (s.get("result") or {}).get("health"),
                                                                    "counts": (s.get("result") or {}).get("counts")} for s in items]

    def events(self, run_id: str, tail: int = 300) -> List[str]:
        with self._cv:
            return list(self._events.get(run_id, []))[-tail:]

    def cancel(self, run_id: str, user: str) -> bool:
        with self._cv:
            st = self._state.get(run_id)
            if not st or st["status"] not in ("queued", "running"):
                return False
            if st["status"] == "queued":
                try:
                    self._queue.remove(run_id)
                except ValueError:
                    pass
                st["status"], st["finished"] = "cancelled", time.strftime("%Y-%m-%d %H:%M:%S")
                st["progress"]["message"] = "Cancelled before it started"
                self._persist(run_id)
            else:
                self._cancel.add(run_id)
        self.data.audit(user, "report.cancel", run_id)
        return True

    def delete(self, run_id: str, user: str) -> bool:
        with self._cv:
            st = self._state.get(run_id)
            if not st or st["status"] in ("queued", "running"):
                return False
            del self._state[run_id]
            self._events.pop(run_id, None)
        shutil.rmtree(self._run_dir(run_id), ignore_errors=True)
        self.data.audit(user, "report.delete", run_id)
        return True

    def file_path(self, run_id: str, kind: str) -> Optional[Path]:
        st = self.get(run_id)
        if not st or not st.get("result"):
            return None
        files = st["result"].get("files", {})
        if kind in ("utilization", "login") and files.get(kind):
            p = self._run_dir(run_id) / files[kind]
        elif kind == "manifest":
            p = self._run_dir(run_id) / "manifest.json"
        else:
            return None
        return p if p.is_file() else None

    def make_zip(self, run_id: str) -> Optional[Path]:
        st = self.get(run_id)
        if not st or not st.get("result"):
            return None
        zpath = self._run_dir(run_id) / ("TMONE-licence-report-%s.zip" % st["month"])
        with self._zip_lock:
            if not zpath.exists():
                tmp = zpath.with_name(zpath.name + ".partial")
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                    for kind in ("utilization", "login", "manifest"):
                        p = self.file_path(run_id, kind)
                        if p:
                            z.write(p, p.name)
                os.replace(tmp, zpath)       # a half-written zip is never visible under the final name
        return zpath

    def previous_summary(self, month: str) -> Optional[Dict[str, Dict[str, int]]]:
        import report_service

        prev = report_service.previous_month(month)
        with self._cv:
            cands = [s for s in self._state.values() if s["status"] == "done" and s["month"] == prev and s.get("result")]
        if not cands:
            return None
        cands.sort(key=lambda s: s["id"], reverse=True)
        return report_service.summary_for_comparison(cands[0]["result"])

    def last_done(self) -> Optional[Dict[str, Any]]:
        with self._cv:
            done = sorted((s for s in self._state.values() if s["status"] == "done"), key=lambda s: s["id"], reverse=True)
            return json.loads(json.dumps(done[0], default=str)) if done else None

    def active(self) -> Optional[str]:
        with self._cv:
            for s in self._state.values():
                if s["status"] == "running":
                    return s["id"]
        return None

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait(timeout=1.0)
                if self._stop:
                    return
                run_id = self._queue.popleft()
            try:
                self._run_one(run_id)
            except Exception:  # never let the worker die
                log.exception("worker error")

    def _add_event(self, run_id: str, text: str) -> None:
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        with self._cv:
            ev = self._events.setdefault(run_id, [])
            ev.append(line)
            if len(ev) > MAX_EVENTS:
                del ev[: len(ev) - MAX_EVENTS]
        try:
            with (self._run_dir(run_id) / "events.log").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _progress(self, run_id: str, ev: Dict[str, Any]) -> None:
        lo, hi = STAGE_RANGE.get(ev.get("stage", ""), (0, 0))
        n, i = ev.get("n") or 0, ev.get("i") or 0
        pct = lo + int((hi - lo) * (i / n)) if n else lo
        with self._cv:
            st = self._state[run_id]
            st["progress"] = {"stage": ev.get("stage"), "i": i, "n": n, "message": ev.get("message", ""), "percent": min(pct, 100)}
        if ev.get("message"):
            self._add_event(run_id, ev["message"])

    def _run_one(self, run_id: str) -> None:
        import report_service

        with self._cv:
            st = self._state.get(run_id)
            if not st or st["status"] != "queued":
                return
            st["status"], st["started"] = "running", time.strftime("%Y-%m-%d %H:%M:%S")
            self._persist(run_id)
            params = {"month": st["month"], "arc": st["arc"], "tenants": st["tenants"]}
        handler = _EventHandler(lambda t: self._add_event(run_id, t), threading.get_ident())
        eng_log = logging.getLogger("tmone")
        eng_log.addHandler(handler)
        old_level = eng_log.level
        eng_log.setLevel(logging.INFO)
        error: Optional[str] = None
        result: Optional[Dict[str, Any]] = None
        status = "done"
        try:
            result = self.runner(
                params["month"], arc=params["arc"], tenants=params["tenants"], output_dir=self._run_dir(run_id),
                progress=lambda ev: self._progress(run_id, ev),
                should_cancel=lambda: run_id in self._cancel,
                previous_summary=self.previous_summary(params["month"]),
            )
        except report_service.RunCancelled:
            status, error = "cancelled", "Cancelled by the user."
        except report_service.ReportError as exc:
            status, error = "failed", str(exc)
        except Exception as exc:
            status, error = "failed", "Unexpected error: %s: %s" % (exc.__class__.__name__, (str(exc).splitlines() or [""])[0])
            log.error("run %s crashed:\n%s", run_id, traceback.format_exc())
            self._add_event(run_id, "ERROR " + error)
        finally:
            eng_log.removeHandler(handler)
            eng_log.setLevel(old_level)
        with self._cv:
            st = self._state[run_id]
            st["status"], st["error"], st["result"] = status, error, result
            st["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if status == "done":
                st["progress"] = {"stage": "done", "i": 1, "n": 1, "message": "Done", "percent": 100}
            self._cancel.discard(run_id)
            self._persist(run_id)
        self.data.audit(st["user"], "report.finish", "run=%s status=%s health=%s" % (run_id, status, (result or {}).get("health", "-")))
        self.prune()

    def prune(self) -> None:
        with self._cv:
            finished = sorted((s for s in self._state.values() if s["status"] in FINISHED), key=lambda s: s["id"], reverse=True)
            drop = [s["id"] for s in finished[self.keep_runs:]]
            for rid in drop:
                self._state.pop(rid, None)
                self._events.pop(rid, None)
        for rid in drop:
            shutil.rmtree(self._run_dir(rid), ignore_errors=True)
