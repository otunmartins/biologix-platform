"""Pick the PDB entry that represents a biologic, and check the entry is really that.

The model chooses the structure the way a structural biologist would - the entry the
field actually uses for this molecule - and the RCSB entry record is then read back to
confirm the choice describes the right protein before any simulation is built on it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from biologix_ai.llm.client import complete_json, model_name

logger = logging.getLogger(__name__)

_ENTRY_URL = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"

_SYSTEM = """You are a structural biologist choosing the Protein Data Bank entry to run a
simulation against.

Given the name of a biologic - a therapeutic protein, an antibody, an enzyme, a peptide
hormone - give the PDB ID of the structure the field normally uses for it. Prefer:
- the human or clinically used form over a homologue from another organism,
- a complete, well-resolved X-ray structure over a fragment or a low-resolution model,
- for an antibody, the Fab or Fv structure of that specific antibody when one exists.

Give alternates in preference order so a failed download can fall back. Set resolved
false when the name is not a biological macromolecule or you know of no entry for it."""

_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "resolved": {"type": "boolean"},
        "pdb_id": {"type": "string"},
        "alternates": {"type": "array", "items": {"type": "string"}},
        "canonical_name": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["resolved", "pdb_id", "alternates", "canonical_name", "rationale", "confidence"],
    "additionalProperties": False,
}


def _entry_record(pdb_id: str) -> Optional[Dict[str, Any]]:
    """Read the RCSB entry so a hallucinated or retired ID cannot reach the simulator."""
    try:
        response = requests.get(_ENTRY_URL.format(pdb_id=pdb_id.upper()), timeout=20)
    except requests.RequestException as exc:
        logger.warning("RCSB entry lookup failed for %s: %s", pdb_id, exc)
        return None
    if not response.ok:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def describe_entry(pdb_id: str) -> Optional[Dict[str, Any]]:
    """Title, method and polymer count for a PDB entry, or None when it does not exist."""
    record = _entry_record(pdb_id)
    if not record:
        return None
    struct = record.get("struct") or {}
    entry_info = record.get("rcsb_entry_info") or {}
    return {
        "pdb_id": str(record.get("entry", {}).get("id") or pdb_id).upper(),
        "title": struct.get("title") or "",
        "method": ", ".join(
            item.get("method", "")
            for item in (record.get("exptl") or [])
            if isinstance(item, dict)
        ),
        "resolution_a": (entry_info.get("resolution_combined") or [None])[0],
        "polymer_entity_count": entry_info.get("polymer_entity_count_protein"),
    }


def resolve_biologic(name: str) -> Dict[str, Any]:
    """Resolve a biologic name to a PDB entry that exists and contains a protein.

    Raises LLMUnavailable when the model cannot be reached, and ValueError when none of
    its candidates survive the RCSB check.
    """
    target = (name or "").strip()
    if not target:
        raise ValueError("A biologic name is required")

    answer = complete_json(
        system=_SYSTEM,
        user=f"Biologic: {json.dumps(target)}",
        schema=_SCHEMA,
        max_tokens=16000,
    )
    usage = answer.pop("_usage", {})
    if not answer.get("resolved"):
        raise ValueError(
            f"{target!r} was not recognised as a biological macromolecule: {answer.get('rationale')}"
        )

    candidates: List[str] = [str(answer.get("pdb_id") or "").strip()]
    candidates += [str(item).strip() for item in (answer.get("alternates") or [])]
    rejected: List[str] = []
    for candidate in [item for item in candidates if item]:
        entry = describe_entry(candidate)
        if entry is None:
            rejected.append(f"{candidate}: no such entry in the PDB")
            continue
        if not entry.get("polymer_entity_count"):
            rejected.append(f"{candidate}: entry contains no protein polymer")
            continue
        return {
            "pdb_id": entry["pdb_id"],
            "canonical_name": str(answer.get("canonical_name") or target).strip() or target,
            "rationale": answer.get("rationale") or "",
            "confidence": answer.get("confidence") or "medium",
            "entry": entry,
            "rejected": rejected,
            "provenance": "llm_verified",
            "model": usage.get("model") or model_name(),
        }

    raise ValueError(
        f"No proposed structure for {target!r} passed the PDB check: {'; '.join(rejected) or 'none proposed'}"
    )
