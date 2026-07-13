"""Durable background-job registry.

The runtime primitives behind long-running work (imports, indexing, export): an
in-memory `_jobs` dict mirrored into the `jobs` table so progress survives a
restart, plus cancellation plumbing for killable subprocesses. The specific job
*workers* (drive/photos import, deep-index, export, …) live with their domains
and call these primitives.

Depends only on `db.get_conn` + stdlib, so it sits at the bottom of the import
graph — no imports from core.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

from db import get_conn

__all__ = [
    "_jobs", "_jobs_lock", "_JOB_FLUSH_INTERVAL", "_JOB_FLUSH_KEYS",
    "_job_flush", "_new_job", "_update_job", "_job_row_snapshot", "_job_snapshot",
    "reconcile_orphaned_jobs", "JobCancelled", "_job_set_proc", "_job_is_cancelled",
    "_run_cancellable",
]

_jobs: dict[str, dict] = {}

_jobs_lock = threading.Lock()

_JOB_FLUSH_INTERVAL = 2.0  # seconds; throttle progress writes to the table

_JOB_FLUSH_KEYS = frozenset({"phase", "finished", "error", "cancelled", "total"})


def _job_flush(job: dict) -> None:
    """Persist a job's current memory state to the `jobs` table (called under lock)."""
    try:
        conn = get_conn()
        conn.execute(
            """INSERT INTO jobs (id, label, unit, phase, total, done, current, error,
                                 cancelled, finished, results, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                 phase=excluded.phase, total=excluded.total, done=excluded.done,
                 current=excluded.current, error=excluded.error,
                 cancelled=excluded.cancelled, finished=excluded.finished,
                 results=excluded.results, updated_at=CURRENT_TIMESTAMP""",
            (job["id"], job["label"], job["unit"], job["phase"], job["total"],
             job["done"], job["current"], job["error"],
             1 if job.get("cancelled") else 0, 1 if job["finished"] else 0,
             json.dumps(job["results"]) if job["finished"] else None,
             job["started_at"]),
        )
        conn.commit()
        conn.close()
        job["_last_flush"] = time.monotonic()
    except Exception:
        pass  # never let a progress-write failure break the running job


def _new_job(label: str, unit: str) -> str:
    """Create a progress job and return its id. `unit` is the thing being counted
    ("file" or "link") so the UI can phrase "7 of 23 files" vs "2 of 3 links"."""
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        # Prune finished jobs older than 10 minutes so the dict doesn't grow forever.
        cutoff = time.monotonic() - 600
        stale = [k for k, j in _jobs.items() if j["finished"] and j["started"] < cutoff]
        for k in stale:
            _jobs.pop(k, None)
        job = {
            "id": job_id, "label": label, "unit": unit, "phase": "starting",
            "total": None, "done": 0, "current": None,
            "started": time.monotonic(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "results": [], "finished": False, "error": None, "_last_flush": 0.0,
        }
        _jobs[job_id] = job
        _job_flush(job)
    # Bound table growth: drop finished job rows older than 24h.
    try:
        conn = get_conn()
        conn.execute(
            "DELETE FROM jobs WHERE finished = 1 AND updated_at < datetime('now', '-1 day')"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return job_id


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        forced = any(k in _JOB_FLUSH_KEYS and job.get(k) != v for k, v in fields.items())
        job.update(fields)
        if forced or (time.monotonic() - job.get("_last_flush", 0.0)) >= _JOB_FLUSH_INTERVAL:
            _job_flush(job)


def _job_row_snapshot(job_id: str) -> dict | None:
    """Build a snapshot from the persisted row (used after a restart, when the job is
    no longer in memory). Elapsed comes from the wall-clock started_at; eta is unknown."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        started = datetime.fromisoformat(row["started_at"])
        updated = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else started
        elapsed = max(0.0, (updated - started).total_seconds())
    except Exception:
        elapsed = 0.0
    return {
        "id": row["id"], "label": row["label"], "unit": row["unit"],
        "phase": row["phase"], "total": row["total"], "done": row["done"],
        "current": row["current"], "elapsed_s": round(elapsed, 1), "eta_s": None,
        "finished": bool(row["finished"]), "error": row["error"],
        "cancelled": bool(row["cancelled"]),
        "results": json.loads(row["results"]) if (row["finished"] and row["results"]) else [],
    }


def _job_snapshot(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            elapsed = time.monotonic() - job["started"]
            done, total = job["done"], job["total"]
            # Extrapolate remaining time from the average time-per-item so far. Only once
            # at least one item is done and we know the total -- otherwise it's a guess.
            eta = None
            if total and done > 0 and not job["finished"]:
                eta = max(0.0, elapsed / done * (total - done))
            return {
                "id": job["id"], "label": job["label"], "unit": job["unit"],
                "phase": job["phase"], "total": total, "done": done,
                "current": job["current"], "elapsed_s": round(elapsed, 1),
                "eta_s": round(eta, 1) if eta is not None else None,
                "finished": job["finished"], "error": job["error"],
                "cancelled": bool(job.get("cancelled")),
                "results": job["results"] if job["finished"] else [],
            }
    # Not in memory (e.g. after a restart) -- fall back to the persisted row.
    return _job_row_snapshot(job_id)


def reconcile_orphaned_jobs() -> None:
    """On startup, any job still marked unfinished lost its in-memory worker thread
    when the process exited -- mark it interrupted so the UI stops waiting on it."""
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE jobs SET finished = 1, error = 'interrupted (app restarted)', "
            "updated_at = CURRENT_TIMESTAMP WHERE finished = 0"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised inside a job worker when the user has requested cancellation."""


def _job_set_proc(job_id: str, proc) -> None:
    """Track the subprocess a job is currently running, so cancel can kill it."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["proc"] = proc


def _job_is_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancelled"))


def _run_cancellable(job_id: str, cmd: list[str]) -> None:
    """Run an ffmpeg command as a killable subprocess tracked on the job. Raises
    JobCancelled if the job was cancelled, or CalledProcessError on a real failure."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _job_set_proc(job_id, proc)
    try:
        _out, err = proc.communicate()
    finally:
        _job_set_proc(job_id, None)
    if _job_is_cancelled(job_id):
        raise JobCancelled()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=_out, stderr=err)
