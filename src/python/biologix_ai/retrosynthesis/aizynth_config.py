"""Resolve AiZynthFinder config path and model readiness."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def get_configfile() -> Optional[str]:
    explicit = os.environ.get("BIOLOGIX_AI_AIZYNTH_CONFIG", "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return str(p.resolve())
    default = _repo_root() / "data" / "aizynthfinder" / "config.yml"
    if default.is_file():
        return str(default.resolve())
    return None


def models_ready() -> bool:
    return get_configfile() is not None
