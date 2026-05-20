"""Funnel context: named pipeline checkpoints for session resumption.

Mirrors the NovoMCP save_funnel_context / get_funnel_context pattern.
Checkpoints are stored as JSON files under <session_dir>/checkpoints/<stage>.json.
A manifest file `_manifest.json` tracks stage order and timestamps.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _checkpoints_dir(session_dir: Path) -> Path:
    d = Path(session_dir) / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(session_dir: Path) -> Path:
    return _checkpoints_dir(session_dir) / "_manifest.json"


def _load_manifest(session_dir: Path) -> List[Dict[str, Any]]:
    p = _manifest_path(session_dir)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_manifest(session_dir: Path, manifest: List[Dict[str, Any]]) -> None:
    p = _manifest_path(session_dir)
    p.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")


def save_funnel_context(
    stage: str,
    checkpoint_data: Dict[str, Any],
    session_dir: Path,
) -> Path:
    """
    Persist a named pipeline checkpoint.

    Parameters
    ----------
    stage:
        Pipeline stage label, e.g. "post_screening", "post_retro", "post_compile".
    checkpoint_data:
        Arbitrary JSON-serialisable dict capturing pipeline state at this stage.
    session_dir:
        Session folder root.

    Returns
    -------
    Path to the written checkpoint file.
    """
    if not stage.strip():
        raise ValueError("stage must be a non-empty string")

    safe_stage = "".join(c if c.isalnum() or c in "_-" else "_" for c in stage.strip())
    cp_dir = _checkpoints_dir(session_dir)
    cp_path = cp_dir / f"{safe_stage}.json"

    payload = {
        "stage": stage,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": checkpoint_data,
    }
    cp_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    manifest = _load_manifest(session_dir)
    # Replace existing entry for this stage or append.
    existing = next((i for i, e in enumerate(manifest) if e.get("stage") == stage), None)
    entry = {"stage": stage, "file": cp_path.name, "saved_at": payload["saved_at"]}
    if existing is not None:
        manifest[existing] = entry
    else:
        manifest.append(entry)
    _save_manifest(session_dir, manifest)

    return cp_path


def get_funnel_context(
    session_dir: Path,
    stage: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a named checkpoint or the most recent one.

    Parameters
    ----------
    session_dir:
        Session folder root.
    stage:
        Stage to retrieve. Empty string returns the last checkpoint in the manifest.

    Returns
    -------
    Checkpoint payload dict or None when no checkpoints exist.
    """
    manifest = _load_manifest(session_dir)
    if not manifest:
        return None

    if stage:
        entry = next((e for e in manifest if e.get("stage") == stage), None)
    else:
        entry = manifest[-1]

    if entry is None:
        return None

    cp_path = _checkpoints_dir(session_dir) / entry["file"]
    if not cp_path.is_file():
        return None

    try:
        return json.loads(cp_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_funnel_stages(session_dir: Path) -> List[Dict[str, Any]]:
    """Return the manifest (ordered list of stage entries)."""
    return _load_manifest(session_dir)
