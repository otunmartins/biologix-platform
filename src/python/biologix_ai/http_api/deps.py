"""Shared dependencies for HTTP API routers."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

_ROOT = Path(os.environ.get("BIOLOGIX_AI_ROOT", Path(__file__).parents[4]))
RUNS_DIR = _ROOT / "runs"


def session_path(experiment_id: str) -> Path:
    """Resolve session directory; raise 404 if not found."""
    path = RUNS_DIR / experiment_id
    if not path.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not found under runs/",
        )
    return path
