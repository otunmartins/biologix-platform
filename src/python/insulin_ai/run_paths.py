"""
Per-session discovery output layout.

One discovery run = one folder under runs/<session_id>/ with all artifacts inside.
Env INSULIN_AI_SESSION_DIR: absolute path to active session (set by CLI/subprocess).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

RUNS_DIRNAME = "runs"
SESSION_META = "session.json"
ENV_SESSION = "INSULIN_AI_SESSION_DIR"


def repo_root_from_package() -> Path:
    """src/python/insulin_ai/run_paths.py -> insulin-ai repo root."""
    return Path(__file__).resolve().parent.parent.parent.parent


def sanitize_session_name(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", name.strip())[:80]
    return s or "session"


def new_session_dir(repo_root: Path, name: Optional[str] = None) -> Path:
    """
    Create runs/<session_id>/ with session.json. session_id = name or YYYYMMDD_HHMMSS.
    """
    base = repo_root / RUNS_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    if name:
        sid = sanitize_session_name(name)
        d = base / sid
        n = 0
        while d.is_dir():
            n += 1
            d = base / f"{sid}_{n}"
    else:
        sid = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = base / sid
        while d.is_dir():
            sid = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"
            d = base / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": d.name,
        "created_utc": datetime.now().isoformat() + "Z",
        "repo_root": str(repo_root),
    }
    (d / SESSION_META).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return d.resolve()


def session_dir_from_env(repo_root: Optional[Path] = None) -> Optional[Path]:
    raw = os.environ.get(ENV_SESSION, "").strip()
    if not raw:
        return None
    p = Path(raw).resolve()
    if p.is_dir():
        return p
    return None


def ensure_session_dir(repo_root: Path, name: Optional[str] = None) -> Path:
    """Use env session if set; otherwise create a new session directory."""
    existing = session_dir_from_env(repo_root)
    if existing:
        return existing
    return new_session_dir(repo_root, name=name)
