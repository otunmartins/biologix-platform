"""Ask Claude for the synthesis evidence the retrosynthesis knowledge graph needs.

The model proposes literature reactions that make the target polymer and terminate in
commercially available reagents. Everything it proposes is then checked: the reaction
text has to parse, the target has to appear as a product, and every leaf reactant has
to resolve as purchasable. Whatever fails goes back to the model as the next prompt,
so the route that survives is one the deterministic tools accepted.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from biologix_ai.llm.client import complete_json, model_name

logger = logging.getLogger(__name__)

_SYSTEM = """You are a polymer chemist writing the synthesis evidence for a retrosynthesis
knowledge graph. Report reactions from the primary literature and from established
industrial practice - reactions that have actually been run, not ones you designed.

The graph is built by working backwards from the target polymer until every branch ends
at a chemical that can be bought. Your answer is what it searches, so:

- At least one reaction must have the target polymer as a product, written under the
  exact target name given to you. Set forms_target true on those reactions.
- Every reactant that is not itself a common purchasable chemical needs its own reaction
  showing how it is made. Keep going upstream until every branch ends at a bulk commodity
  chemical or a standard catalogue reagent.
- Name compounds the way a catalogue does (for example "styrene", "ethylene oxide",
  "adipic acid"). Do not write SMILES in the name fields, and do not use abbreviations.
- Conditions should carry the real catalyst, solvent, temperature and time when known.
- On the reactions that form the target, name the polymerisation mechanism; use "unknown"
  on every upstream small-molecule step.
- Give each source a real citation: author, journal, year, or the patent or process name.
  Do not invent a DOI; leave it empty when you are not certain of it.
- Prefer routes that are actually used to make the material. Where more than one route is
  practised, report each one as a separate source so they can be compared.

Only report chemistry you are confident is real. If you cannot support the target with
literature chemistry, return no sources and explain why in notes."""

_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "citation": {"type": "string"},
                    "doi_or_url": {"type": "string"},
                    "reactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "reactants": {"type": "array", "items": {"type": "string"}},
                                "products": {"type": "array", "items": {"type": "string"}},
                                "conditions": {"type": "string"},
                                "forms_target": {"type": "boolean"},
                                "polymerization_type": {
                                    "type": "string",
                                    "enum": [
                                        "RAFT",
                                        "ATRP",
                                        "condensation",
                                        "ring_opening",
                                        "free_radical",
                                        "step_growth",
                                        "anionic",
                                        "cationic",
                                        "coordination",
                                        "enzymatic",
                                        "modification_of_natural_polymer",
                                        "other",
                                        "unknown",
                                    ],
                                },
                            },
                            "required": [
                                "reactants",
                                "products",
                                "conditions",
                                "forms_target",
                                "polymerization_type",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["citation", "doi_or_url", "reactions"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["sources", "notes"],
    "additionalProperties": False,
}


def _max_rounds() -> int:
    try:
        return max(1, int(os.getenv("BIOLOGIX_LLM_RETRO_ROUNDS", "3")))
    except ValueError:
        return 3


def _clean_names(values: Any) -> List[str]:
    out: List[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _normalize_sources(raw: Any, material_name: str) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Shape the model's answer into the structured evidence the UI and engine share.

    Reactions flagged as forming the target are rewritten to name it exactly, because the
    graph root is matched on that name and a synonym silently produces an empty tree.
    """
    from biologix_ai.platform.extractions import MAX_REACTIONS, MAX_SOURCES

    sources: List[Dict[str, Any]] = []
    seen: set[str] = set()
    root = material_name.strip().lower()
    mechanisms: Dict[str, str] = {}

    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        citation = str(entry.get("citation") or "").strip()
        if not citation:
            continue
        doi = str(entry.get("doi_or_url") or "").strip()
        name = f"{citation} ({doi})" if doi else citation
        if name.lower() in seen:
            continue

        reactions: List[Dict[str, Any]] = []
        for reaction in entry.get("reactions") or []:
            if not isinstance(reaction, dict):
                continue
            reactants = _clean_names(reaction.get("reactants"))
            products = _clean_names(reaction.get("products"))
            if not reactants or not products:
                continue
            if reaction.get("forms_target"):
                if not any(root in product.lower() for product in products):
                    products = [material_name] + products[1:] if products else [material_name]
                mechanism = str(reaction.get("polymerization_type") or "unknown")
                if mechanism != "unknown":
                    mechanisms.setdefault(name, mechanism)
            reactions.append({
                "reactants": reactants,
                "products": products,
                "conditions": str(reaction.get("conditions") or "").strip() or "not specified",
            })
            if len(reactions) >= MAX_REACTIONS:
                break
        if not reactions:
            continue
        seen.add(name.lower())
        sources.append({"name": name, "reactions": reactions})
        if len(sources) >= MAX_SOURCES:
            break
    return sources, mechanisms


def _diagnose(sources: List[Dict[str, Any]], material_name: str, workspace: Optional[Path]) -> Dict[str, Any]:
    """Render the evidence and run the engine's own checks over it."""
    from biologix_ai.platform.extractions import render_sources
    from biologix_ai.retrosynthesis.retro_adapter import (
        normalize_extractions,
        validate_extractions_for_tree,
    )

    extractions = normalize_extractions(render_sources(sources))
    if workspace is not None:
        # Resolve names against PubChem and the purchasable stocks first, otherwise
        # every specialty reagent looks blocking and the model is asked to expand
        # branches that were already fine.
        try:
            from biologix_ai.retrosynthesis.precursor_registry import (
                collect_reactants_from_extractions,
                seed_workspace_precursors,
            )

            seed_workspace_precursors(workspace, collect_reactants_from_extractions(extractions))
        except Exception as exc:
            logger.warning("Precursor seeding before diagnosis failed: %s", exc)
    report = validate_extractions_for_tree(extractions, material_name)
    report["extractions"] = extractions
    return report


def _feedback(report: Dict[str, Any], material_name: str) -> str:
    lines: List[str] = []
    if not report.get("root_product_found"):
        lines.append(
            f"No reaction lists {material_name!r} as a product, so the graph has no root. "
            f"Add the polymerisation step and write the product name exactly as {material_name!r}."
        )
    blocking = report.get("blocking_reactants") or []
    if blocking:
        lines.append(
            "These reactants are not in any purchasable catalogue, so their branches dead-end. "
            "For each one, either add the reaction that makes it from cheaper materials, or "
            "replace it with the commodity reagent actually used: " + ", ".join(sorted(blocking))
        )
    return "\n".join(f"- {line}" for line in lines)


def plan_evidence(
    material_name: str,
    target_psmiles: str = "",
    *,
    workspace: Optional[Path] = None,
    extra_feedback: str = "",
) -> Dict[str, Any]:
    """Produce verified synthesis evidence for *material_name*.

    Returns the structured sources, the rendered extractions and the diagnostics from
    the final round. Raises LLMUnavailable when the model cannot be reached, and
    ValueError when no round produced evidence with the target as a root product.
    """
    target = (material_name or "").strip()
    if not target:
        raise ValueError("A polymer name is required to plan synthesis evidence")

    base_user = f"Target polymer name (use this exact name for the product): {json.dumps(target)}"
    if target_psmiles:
        base_user += f"\nTarget repeat unit (PSMILES): {json.dumps(target_psmiles)}"

    rounds: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    mechanisms: Dict[str, str] = {}
    report: Dict[str, Any] = {}
    feedback = extra_feedback.strip()
    model_used = model_name()

    for round_index in range(_max_rounds()):
        user = base_user
        if sources:
            user += (
                "\n\nEvidence you have already given (keep it and extend it):\n"
                + json.dumps(sources, indent=2)
            )
        if feedback:
            user += (
                "\n\nThe verification tools rejected the following. Fix exactly these:\n"
                + feedback
            )
        answer = complete_json(system=_SYSTEM, user=user, schema=_SCHEMA, max_tokens=32000)
        usage = answer.pop("_usage", {})
        model_used = usage.get("model") or model_used

        proposed, proposed_mechanisms = _normalize_sources(answer.get("sources"), target)
        if not proposed:
            rounds.append({
                "round": round_index + 1,
                "sources": 0,
                "notes": answer.get("notes") or "",
                "rejected": ["the model returned no usable reactions"],
            })
            feedback = (
                f"- You returned no usable reactions for {target!r}. Every reaction needs at "
                "least one reactant and one product, named as chemicals."
            )
            continue

        sources = proposed
        mechanisms = proposed_mechanisms
        try:
            report = _diagnose(sources, target, workspace)
        except ValueError as exc:
            rounds.append({"round": round_index + 1, "sources": len(sources), "rejected": [str(exc)]})
            feedback = f"- The evidence could not be rendered: {exc}"
            continue

        blocking = report.get("blocking_reactants") or []
        rounds.append({
            "round": round_index + 1,
            "sources": len(sources),
            "reactions": sum(len(source["reactions"]) for source in sources),
            "root_product_found": report.get("root_product_found", False),
            "blocking_reactants": sorted(blocking),
            "notes": answer.get("notes") or "",
        })

        if report.get("root_product_found") and not blocking:
            break
        feedback = _feedback(report, target)
        if not feedback:
            break

    if not sources or not report.get("root_product_found"):
        raise ValueError(
            f"Claude could not produce synthesis evidence for {target!r} that names it as a "
            f"reaction product after {len(rounds)} round(s)."
        )

    return {
        "ok": True,
        "material_name": target,
        "sources": sources,
        "mechanism_by_source": mechanisms,
        "extractions": report["extractions"],
        "diagnostics": {key: value for key, value in report.items() if key != "extractions"},
        "unresolved_reactants": sorted(report.get("blocking_reactants") or []),
        "rounds": rounds,
        "model": model_used,
        "source": "llm_literature_evidence",
    }
