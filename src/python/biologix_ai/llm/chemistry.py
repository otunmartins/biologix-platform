"""Resolve a polymer name to a repeat-unit structure by asking Claude, then checking it.

The model proposes the repeat unit, its monomers and the polymerisation class.
RDKit and the MD prescreen decide whether that answer is usable; a rejected answer
goes back to the model with the exact reason attached.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from biologix_ai.llm.client import LLMUnavailable, complete_json, model_name

logger = logging.getLogger(__name__)

_SYSTEM = """You are a polymer chemist resolving a material name to its repeat unit.

Return the PSMILES repeat unit: a SMILES fragment with exactly two [*] atoms marking
the two backbone connection points where the unit joins its neighbours. Write the
smallest constitutional repeat unit, not an oligomer.

Rules that make an answer usable downstream:
- Exactly two [*] atoms, both on backbone atoms, never on a side chain.
- The H-capped form of the unit must be a neutral, closed-shell, RDKit-parseable molecule.
- No explicit radicals, no formal charges, no isotopes, no stereochemistry you are unsure of.
- For a copolymer, give the repeat unit of the dominant sequence and list every monomer.
- For a polysaccharide or protein-derived material, give the repeating residue.

Give canonical_name as a single polymer name with no synonyms, aliases or
parenthetical alternatives - it is used as a graph key downstream.

Also report the monomers the unit is made from, as neutral small-molecule SMILES, and
the polymerisation mechanism. Set confidence to "low" when the name is ambiguous or you
are extrapolating, and say why in notes. If the name is not a polymer at all, or you
cannot determine a repeat unit, set resolved to false and explain in notes."""

_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "resolved": {"type": "boolean"},
        "canonical_name": {"type": "string"},
        "psmiles": {"type": "string"},
        "polymerization_type": {
            "type": "string",
            "enum": [
                "free_radical",
                "ring_opening",
                "condensation",
                "anionic",
                "cationic",
                "coordination",
                "enzymatic",
                "modification_of_natural_polymer",
                "other",
            ],
        },
        "monomers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "smiles": {"type": "string"},
                },
                "required": ["name", "smiles"],
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": [
        "resolved",
        "canonical_name",
        "psmiles",
        "polymerization_type",
        "monomers",
        "confidence",
        "notes",
    ],
    "additionalProperties": False,
}


def _max_rounds() -> int:
    try:
        return max(1, int(os.getenv("BIOLOGIX_LLM_RESOLVE_ROUNDS", "3")))
    except ValueError:
        return 3


def verify_psmiles(name: str, psmiles: str) -> Dict[str, Any]:
    """Run the deterministic checks a proposed repeat unit has to survive."""
    from biologix_ai.material_mappings import (
        check_name_structure_consistency,
        prescreen_psmiles_for_md,
        validate_psmiles,
    )

    problems: List[str] = []
    validation = validate_psmiles(psmiles)
    if not validation.get("valid"):
        problems.append(f"PSMILES does not validate: {validation.get('error')}")

    prescreen = prescreen_psmiles_for_md(psmiles)
    if not prescreen.get("ok"):
        problems.append(f"Rejected by the molecular dynamics prescreen: {prescreen.get('error')}")

    consistency = check_name_structure_consistency(name, psmiles)
    if not consistency.get("consistent", True):
        missing = ", ".join(consistency.get("missing") or []) or "expected groups absent"
        problems.append(
            f"Structure does not carry the groups the name {name!r} implies: {missing}"
        )

    return {
        "ok": not problems,
        "problems": problems,
        "canonical": validation.get("canonical") or psmiles,
        "validation": validation,
        "prescreen": prescreen,
        "name_consistency": consistency,
    }


def _verify_monomers(monomers: Any) -> Dict[str, Any]:
    """Keep only monomers RDKit can parse; report the ones it cannot."""
    try:
        from rdkit import Chem
    except ImportError:
        return {"monomers": list(monomers or []), "problems": []}

    kept: List[Dict[str, str]] = []
    problems: List[str] = []
    for monomer in monomers or []:
        if not isinstance(monomer, dict):
            continue
        smiles = str(monomer.get("smiles") or "").strip()
        label = str(monomer.get("name") or smiles or "unnamed")
        if not smiles:
            problems.append(f"Monomer {label!r} has no SMILES")
            continue
        if Chem.MolFromSmiles(smiles) is None:
            problems.append(f"Monomer {label!r} has an unparseable SMILES: {smiles}")
            continue
        kept.append({"name": label, "smiles": Chem.MolToSmiles(Chem.MolFromSmiles(smiles))})
    return {"monomers": kept, "problems": problems}


def resolve_polymer(name: str) -> Dict[str, Any]:
    """Resolve *name* to a verified PSMILES repeat unit.

    Raises LLMUnavailable when the model cannot be reached, and ValueError when it
    can be reached but never produced a structure that passes the checks.
    """
    target = (name or "").strip()
    if not target:
        raise ValueError("A polymer name is required")

    attempts: List[Dict[str, Any]] = []
    feedback = ""
    for round_index in range(_max_rounds()):
        user = f"Material name: {json.dumps(target)}"
        if feedback:
            user += (
                "\n\nYour previous answer was rejected by the verification tools:\n"
                f"{feedback}\n\nCorrect the structure and answer again."
            )
        answer = complete_json(system=_SYSTEM, user=user, schema=_SCHEMA, max_tokens=16000)
        usage = answer.pop("_usage", {})

        if not answer.get("resolved"):
            attempts.append({"round": round_index + 1, "rejected": ["model reported no resolution"]})
            raise ValueError(
                f"{target!r} could not be resolved to a polymer repeat unit: "
                f"{answer.get('notes') or 'no reason given'}"
            )

        psmiles = str(answer.get("psmiles") or "").strip()
        checks = verify_psmiles(target, psmiles)
        monomer_check = _verify_monomers(answer.get("monomers"))
        problems = list(checks["problems"]) + list(monomer_check["problems"])
        attempts.append({"round": round_index + 1, "psmiles": psmiles, "rejected": problems})

        if not problems:
            logger.info("Resolved %r to %s in round %d", target, psmiles, round_index + 1)
            return {
                "ok": True,
                "psmiles": psmiles,
                "canonical_psmiles": checks["canonical"],
                "material_name": str(answer.get("canonical_name") or target).strip() or target,
                "polymerization_type": answer.get("polymerization_type") or "other",
                "monomers": monomer_check["monomers"],
                "confidence": answer.get("confidence") or "medium",
                "notes": answer.get("notes") or "",
                "source": "llm_verified",
                "model": usage.get("model") or model_name(),
                "rounds": round_index + 1,
                "attempts": attempts,
                "verification": {
                    "validation": checks["validation"],
                    "prescreen": checks["prescreen"],
                    "name_consistency": checks["name_consistency"],
                },
            }
        feedback = "\n".join(f"- {problem}" for problem in problems)
        logger.warning("Round %d for %r rejected: %s", round_index + 1, target, problems)

    raise ValueError(
        f"The model proposed {_max_rounds()} structures for {target!r} and none passed "
        f"verification. Last reasons: {feedback}"
    )


def resolve_polymer_or_none(name: str) -> Optional[Dict[str, Any]]:
    """resolve_polymer, returning None instead of raising, for optional enrichment paths."""
    try:
        return resolve_polymer(name)
    except (LLMUnavailable, ValueError) as exc:
        logger.warning("LLM polymer resolution unavailable for %r: %s", name, exc)
        return None


_NAME_SYSTEM = """You are a polymer chemist naming a repeat unit.

Given a PSMILES repeat unit (a SMILES fragment whose two [*] atoms are the backbone
connection points), give the polymer's common name as a single name with no synonyms
or parenthetical alternatives - it is used as a graph key downstream. Prefer the name
the literature uses, for example "poly(lactic acid)" rather than a systematic name.
Set named false when the fragment is not a recognisable polymer repeat unit."""

_NAME_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "named": {"type": "boolean"},
        "material_name": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["named", "material_name", "confidence", "notes"],
    "additionalProperties": False,
}


def name_polymer(psmiles: str) -> Dict[str, Any]:
    """Name a repeat unit. Raises ValueError when the model cannot name it."""
    unit = (psmiles or "").strip()
    if not unit:
        raise ValueError("A PSMILES repeat unit is required")
    answer = complete_json(
        system=_NAME_SYSTEM,
        user=f"PSMILES repeat unit: {json.dumps(unit)}",
        schema=_NAME_SCHEMA,
        max_tokens=16000,
    )
    usage = answer.pop("_usage", {})
    name = str(answer.get("material_name") or "").strip()
    if not answer.get("named") or not name or "[*]" in name:
        raise ValueError(
            f"The repeat unit {unit!r} could not be named: {answer.get('notes') or 'no reason given'}"
        )
    return {
        "material_name": name,
        "confidence": answer.get("confidence") or "medium",
        "notes": answer.get("notes") or "",
        "model": usage.get("model") or model_name(),
    }
