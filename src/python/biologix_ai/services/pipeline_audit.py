"""Append-only pipeline audit trail for GxP / 21 CFR Part 11 reproducibility.

Mirrors the NovoMCP save_funnel_stage / get_pipeline_audit pattern.
Every call to save_pipeline_stage appends a JSONL record to
<session_dir>/audit/pipeline_audit.jsonl — records are never modified.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _audit_dir(session_dir: Path) -> Path:
    d = Path(session_dir) / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit_path(session_dir: Path) -> Path:
    return _audit_dir(session_dir) / "pipeline_audit.jsonl"


def save_pipeline_stage(
    session_dir: Path,
    candidate_psmiles: str,
    stage: str,
    disposition: str,
    detail: str = "",
) -> Dict[str, Any]:
    """
    Append one audit record for a pipeline stage applied to a candidate.

    Parameters
    ----------
    session_dir:
        Session folder root.
    candidate_psmiles:
        The polymer PSMILES string being processed.
    stage:
        Pipeline stage label: "validation", "admet", "retro", "compliance", "scoring",
        "openmm", "compile", etc.
    disposition:
        Outcome: "pass", "fail", or "warning".
    detail:
        Optional JSON string or free text explaining the disposition (alert names,
        scores, exclusion reason, route count, etc.).

    Returns
    -------
    The audit record dict.
    """
    if disposition not in ("pass", "fail", "warning"):
        disposition = "warning"

    record: Dict[str, Any] = {
        "audit_id": uuid.uuid4().hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate_psmiles": candidate_psmiles,
        "stage": stage,
        "disposition": disposition,
        "detail": detail,
    }

    path = _audit_path(session_dir)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return record


def get_pipeline_audit(
    session_dir: Path,
    candidate_psmiles: str = "",
) -> List[Dict[str, Any]]:
    """
    Retrieve audit records for a candidate or the entire session.

    Parameters
    ----------
    session_dir:
        Session folder root.
    candidate_psmiles:
        If provided, filter records to this exact PSMILES. Empty string returns all.

    Returns
    -------
    List of audit record dicts in append order.
    """
    path = _audit_path(session_dir)
    if not path.is_file():
        return []

    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if candidate_psmiles and rec.get("candidate_psmiles") != candidate_psmiles:
                continue
            records.append(rec)
    return records
