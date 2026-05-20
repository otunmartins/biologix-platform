"""Session persistence helpers for biologics / retrosynthesis workflows."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict


def write_retrosynthesis_artifact(session_dir: Path, filename: str, payload: Dict[str, Any]) -> Path:
    """Write JSON under ``<session_dir>/retrosynthesis/``."""
    d = Path(session_dir) / "retrosynthesis"
    d.mkdir(parents=True, exist_ok=True)
    path = d / filename
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def patch_world_retrosynthesis(session_dir: Path, entry: Dict[str, Any]) -> None:
    """Append/merge a ``retrosynthesis_entries`` row into ``discovery_world.json``."""
    from biologix_ai.discovery_world import apply_patch, load_world, save_world, world_path_for_session

    path = world_path_for_session(session_dir)
    world = load_world(path)
    row = dict(entry)
    if not row.get("id"):
        row["id"] = f"retro_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    world = apply_patch(world, {"retrosynthesis_entries": [row]})
    save_world(path, world)
