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
    configfile = get_configfile()
    if configfile is None:
        return False

    try:
        lines = Path(configfile).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    paths: list[str] = []
    counts = {"expansion": 0, "filter": 0, "stock": 0}
    section = ""
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw_line[0].isspace() and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        if section == "expansion" and stripped.startswith("- "):
            paths.append(stripped[2:].strip().strip("'\""))
            counts[section] += 1
        elif section in {"filter", "stock"} and ":" in stripped:
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if value:
                paths.append(value)
                counts[section] += 1

    if counts["expansion"] < 2 or counts["filter"] < 1 or counts["stock"] < 1:
        return False

    config_dir = Path(configfile).parent
    for configured_path in paths:
        if not isinstance(configured_path, str) or not configured_path.strip():
            return False
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = config_dir / path
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True
