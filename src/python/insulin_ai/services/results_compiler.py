"""ResultsCompiler: the final reasoning step.

Collects structured outputs from retrosynthesis, ADMET, literature, and
physics services, ranks them, and optionally produces an LLM narrative.
Lightweight version of the blueprint's CSO Agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from insulin_ai.retrosynthesis.models import (
    MonomerSource,
    PolymerRoute,
    RetrosynthesisResult,
)
from insulin_ai.services.toxicity_service import ToxicityResult

logger = logging.getLogger(__name__)


class RouteScorecard(BaseModel):
    route_index: int
    target_polymer: str
    polymerization_type: str
    num_steps: int
    num_monomers: int
    monomers_purchasable: int
    monomers_need_synthesis: int
    admet_flags_total: int
    smarts_alerts_total: int
    composite_score: float
    recommended: bool


class CompiledReport(BaseModel):
    biologic_target: str
    target_polymer: str
    scorecards: List[RouteScorecard] = Field(default_factory=list)
    safety_summary: List[str] = Field(default_factory=list)
    literature_refs: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    narrative: str = ""
    raw_data: Dict[str, Any] = Field(default_factory=dict)


def _score_route(
    route: PolymerRoute,
    tox_results: Dict[str, ToxicityResult],
) -> float:
    """Composite score for a route. Higher is better."""
    score = route.pathway_score * 10.0

    for monomer in route.monomers:
        if monomer.source == MonomerSource.PURCHASABLE:
            score += 2.0
        elif monomer.source == MonomerSource.NEEDS_SYNTHESIS:
            score += 0.5

        tox = tox_results.get(monomer.smiles)
        if tox:
            if not tox.safe:
                score -= 3.0 * len(tox.smarts_hits)
            if tox.admet and tox.admet.flags:
                score -= 1.5 * len(tox.admet.flags)

    step_penalty = max(0, len(route.steps) - 3) * 0.5
    score -= step_penalty

    return max(score, 0.0)


def compile_results(
    retro_result: RetrosynthesisResult,
    tox_results: Optional[Dict[str, ToxicityResult]] = None,
    literature: Optional[Dict[str, Any]] = None,
    physics: Optional[Dict[str, Any]] = None,
    generate_narrative: bool = True,
) -> CompiledReport:
    """Compile outputs from all upstream services into a ranked report."""
    tox_results = tox_results or {}
    literature = literature or {}
    lit_papers = literature.get("papers", [])
    lit_refs = [p.get("title", p.get("pmid", str(p))) for p in lit_papers if p]

    physics_scores: Dict[str, Any] = {}
    if physics and isinstance(physics, dict):
        for r in physics.get("results", []):
            if isinstance(r, dict) and r.get("status") == "completed":
                physics_scores[r.get("psmiles", "")] = r.get("data", {})

    scorecards: List[RouteScorecard] = []
    all_safety: List[str] = []
    all_refs: List[str] = []

    for i, route in enumerate(retro_result.polymer_routes):
        composite = _score_route(route, tox_results)

        for m in route.monomers:
            if m.smiles in physics_scores:
                composite += 1.0

        purchasable = sum(
            1 for m in route.monomers if m.source == MonomerSource.PURCHASABLE
        )
        needs_synth = sum(
            1 for m in route.monomers if m.source == MonomerSource.NEEDS_SYNTHESIS
        )

        admet_flags = 0
        smarts_alerts = 0
        for m in route.monomers:
            tox = tox_results.get(m.smiles)
            if tox:
                smarts_alerts += len(tox.smarts_hits)
                if tox.admet:
                    admet_flags += len(tox.admet.flags)
                for w in tox.warnings:
                    all_safety.append(f"Route {i+1}, {m.name or m.smiles}: {w}")

        all_refs.extend(route.literature_refs)
        all_refs.extend(lit_refs)

        scorecards.append(RouteScorecard(
            route_index=i,
            target_polymer=route.target_polymer,
            polymerization_type=route.polymerization_type.value,
            num_steps=len(route.steps),
            num_monomers=len(route.monomers),
            monomers_purchasable=purchasable,
            monomers_need_synthesis=needs_synth,
            admet_flags_total=admet_flags,
            smarts_alerts_total=smarts_alerts,
            composite_score=composite,
            recommended=False,
        ))

    scorecards.sort(key=lambda s: s.composite_score, reverse=True)
    if scorecards:
        scorecards[0].recommended = True

    next_steps = _suggest_next_steps(scorecards, retro_result)

    narrative = ""
    if generate_narrative:
        narrative = _build_narrative(
            scorecards, all_safety, all_refs, next_steps,
            retro_result.request.biologic_target,
        )

    return CompiledReport(
        biologic_target=retro_result.request.biologic_target,
        target_polymer=retro_result.request.target,
        scorecards=scorecards,
        safety_summary=list(set(all_safety)),
        literature_refs=list(set(all_refs)),
        next_steps=next_steps,
        narrative=narrative,
        raw_data={
            "retro_warnings": retro_result.warnings,
            "retro_errors": retro_result.errors,
            "retro_metadata": retro_result.metadata,
            "literature_paper_count": len(lit_papers),
            "physics_evaluated": len(physics_scores),
        },
    )


def _suggest_next_steps(
    scorecards: List[RouteScorecard],
    retro_result: RetrosynthesisResult,
) -> List[str]:
    steps: List[str] = []

    if not scorecards:
        steps.append("No routes found. Try a different target polymer or relax constraints.")
        return steps

    top = scorecards[0]

    if top.smarts_alerts_total > 0 or top.admet_flags_total > 0:
        steps.append(
            f"Route {top.route_index + 1} has safety flags — review ADMET and SMARTS alerts before proceeding"
        )

    if top.monomers_need_synthesis > 0:
        steps.append(
            f"Route {top.route_index + 1} requires synthesis of {top.monomers_need_synthesis} monomer(s) — "
            "verify AiZynthFinder routes or source from specialty suppliers"
        )

    steps.append(
        f"Run MD simulation on top {min(3, len(scorecards))} candidate(s) "
        f"to validate polymer-biologic interaction"
    )

    if len(scorecards) > 1:
        steps.append(
            f"Consider route {scorecards[1].route_index + 1} as backup "
            f"(score {scorecards[1].composite_score:.1f} vs {top.composite_score:.1f})"
        )

    return steps


def _build_narrative(
    scorecards: List[RouteScorecard],
    safety: List[str],
    refs: List[str],
    next_steps: List[str],
    biologic: str,
) -> str:
    """Build a structured text narrative from the compiled data.

    This is a deterministic template-based summary. For LLM-enriched
    narration, call an LLM with the CompiledReport JSON as context.
    """
    lines: List[str] = []
    lines.append(f"## Retrosynthesis Report for {biologic.title()} Stabilization\n")

    if not scorecards:
        lines.append("No viable routes were identified.\n")
        return "\n".join(lines)

    top = scorecards[0]
    lines.append(f"**Recommended route:** Route {top.route_index + 1} for {top.target_polymer}")
    lines.append(f"- Polymerization: {top.polymerization_type}")
    lines.append(f"- Steps: {top.num_steps}, Monomers: {top.num_monomers}")
    lines.append(f"- Purchasable: {top.monomers_purchasable}, Need synthesis: {top.monomers_need_synthesis}")
    lines.append(f"- Composite score: {top.composite_score:.2f}")
    lines.append("")

    if safety:
        lines.append("### Safety Alerts")
        for s in safety[:10]:
            lines.append(f"- {s}")
        lines.append("")

    if next_steps:
        lines.append("### Recommended Next Steps")
        for i, step in enumerate(next_steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if refs:
        lines.append("### Literature References")
        for ref in refs[:10]:
            lines.append(f"- {ref}")

    return "\n".join(lines)
