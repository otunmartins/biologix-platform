"""File-based background job tracking for long-running API operations."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _jobs_dir(experiment_id: Optional[str] = None) -> Path:
    from biologix_ai.http_api.deps import RUNS_DIR, _ROOT

    if experiment_id:
        d = RUNS_DIR / experiment_id / "jobs"
    else:
        d = _ROOT / ".api_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_job(experiment_id: Optional[str] = None) -> str:
    """Create a pending job file and return job_id."""
    job_id = uuid.uuid4().hex[:12]
    path = _jobs_dir(experiment_id) / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "experiment_id": experiment_id,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": None,
        "error": None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return job_id


def _job_path(job_id: str, experiment_id: Optional[str] = None) -> Path:
    return _jobs_dir(experiment_id) / f"{job_id}.json"


def read_job(job_id: str, experiment_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id, experiment_id)
    if not path.is_file():
        if experiment_id:
            path = _job_path(job_id, None)
        if not path.is_file():
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def update_job(
    job_id: str,
    experiment_id: Optional[str],
    *,
    status: Optional[str] = None,
    result: Any = None,
    error: Optional[str] = None,
) -> None:
    data = read_job(job_id, experiment_id) or {"job_id": job_id, "experiment_id": experiment_id}
    if status is not None:
        data["status"] = status
    if result is not None:
        data["result"] = result
    if error is not None:
        data["error"] = error
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    exp = data.get("experiment_id") or experiment_id
    _job_path(job_id, exp).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def run_job_in_background(
    job_id: str,
    experiment_id: Optional[str],
    fn: Callable[[], Dict[str, Any]],
) -> None:
    """Execute fn in a daemon thread and persist status to job file."""

    def _worker() -> None:
        update_job(job_id, experiment_id, status="running")
        try:
            result = fn()
            if result.get("ok") is False:
                update_job(job_id, experiment_id, status="failed", result=result, error=result.get("error"))
            else:
                update_job(job_id, experiment_id, status="completed", result=result)
        except Exception as exc:
            update_job(job_id, experiment_id, status="failed", error=str(exc))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
