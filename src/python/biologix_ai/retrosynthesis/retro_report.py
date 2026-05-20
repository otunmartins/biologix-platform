"""Markdown reports from persisted retrosynthesis plan artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target


def _targets_match(stored: str, query: str) -> bool:
    a = (stored or "").strip()
    b = (query or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ra = resolve_retro_target(a)
    rb = resolve_retro_target(b)
    if ra.get("psmiles") and ra["psmiles"] == rb.get("psmiles"):
        return True
    if ra.get("material_name", "").lower() == rb.get("material_name", "").lower():
        return True
    return False


def load_cached_plan_artifact(
    session_dir: Path,
    target: str,
) -> Optional[Dict[str, Any]]:
    """Load newest plan_*.json wrapper for target (PSMILES or name)."""
    retro_dir = session_dir / "retrosynthesis"
    if not retro_dir.is_dir():
        return None
    candidates = sorted(
        retro_dir.glob("plan_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if _targets_match(data.get("target", ""), target):
            data["_artifact_path"] = str(path)
            return data
    return None


def _provenance_honesty(meta: Dict[str, Any]) -> str:
    prov = meta.get("route_provenance", "none")
    if prov in ("session_agent_llm", "retro_agent_llm"):
        return (
            f"**Reporting honesty:** Provenance is `{prov}` — literature-derived "
            "RetroSynAgent knowledge-graph routes."
        )
    if prov == "template":
        return (
            "**Reporting honesty:** Provenance is `template` — curated polymerisation "
            "template, **not** a literature RetroSyn KG tree. Do not describe as full "
            "literature retrosynthesis."
        )
    return (
        f"**Reporting honesty:** Provenance is `{prov}` — no viable polymer routes. "
        "Do not invent synthesis steps."
    )


def format_plan_result_markdown(plan_wrapper: Dict[str, Any]) -> str:
    """Format one persisted plan artifact (target + result) as markdown."""
    target = plan_wrapper.get("target", "")
    result = plan_wrapper.get("result") or {}
    meta = result.get("metadata") or {}
    routes = result.get("polymer_routes") or []
    warnings = result.get("warnings") or []
    lines: List[str] = []

    resolved = resolve_retro_target(target)
    title = resolved.get("material_name") or target
    lines.append(f"### {title}")
    lines.append("")
    lines.append(f"- **Target PSMILES:** `{target}`")
    lines.append(f"- **route_provenance:** `{meta.get('route_provenance', 'none')}`")
    lines.append(f"- **retro_stages_completed:** {meta.get('retro_stages_completed', [])}")
    lines.append(f"- **aizynth_monomers_attempted:** {meta.get('aizynth_monomers_attempted', 0)}")
    lines.append(f"- **aizynth_monomers_solved:** {meta.get('aizynth_monomers_solved', 0)}")
    if meta.get("reporting_honesty"):
        lines.append(f"- {meta['reporting_honesty']}")
    else:
        lines.append(f"- {_provenance_honesty(meta)}")
    if meta.get("recommended_next_action"):
        lines.append(f"- **recommended_next_action:** {meta['recommended_next_action']}")
    lines.append("")

    if warnings:
        lines.append("**Warnings:**")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    if not routes:
        lines.append("*No polymer routes in this plan artifact.*")
        lines.append("")
        return "\n".join(lines)

    for ri, route in enumerate(routes, 1):
        lines.append(f"#### Route {ri}" + (" (recommended)" if route.get("recommended") else ""))
        lines.append("")
        lines.append(f"- Polymerization: `{route.get('polymerization_type', 'unknown')}`")
        lines.append(f"- Pathway score: {route.get('pathway_score', 0)}")
        steps = route.get("steps") or []
        if steps:
            lines.append("")
            lines.append("| Step | Reactants | Product | Conditions | Source |")
            lines.append("|------|-----------|---------|------------|--------|")
            for step in steps:
                reactants = ", ".join(step.get("reactant_names") or [])
                lines.append(
                    f"| | {reactants} | {step.get('product_name', '')} | "
                    f"{step.get('conditions') or step.get('reaction_type') or ''} | "
                    f"{step.get('literature_source') or ''} |"
                )
        monomers = route.get("monomers") or []
        for mon in monomers:
            lines.append("")
            lines.append(f"**Monomer:** `{mon.get('smiles', '')}` ({mon.get('name') or 'unnamed'})")
            lines.append(f"- Source: `{mon.get('source', 'unknown')}`")
            sr = mon.get("synthesis_route")
            if sr:
                lines.append(f"- AiZynth solved: `{sr.get('is_solved', False)}` (score: {sr.get('score', 0)})")
                bbs = sr.get("building_blocks") or []
                if bbs:
                    lines.append(f"- Building blocks: {', '.join(f'`{b}`' for b in bbs)}")
                sm_steps = sr.get("steps") or []
                if sm_steps:
                    lines.append("")
                    lines.append("| # | Reactants | Notes |")
                    lines.append("|---|-----------|-------|")
                    for si, sm in enumerate(sm_steps, 1):
                        reactants = ", ".join(sm.get("reactants") or [])
                        lines.append(f"| {si} | {reactants} | {sm.get('reaction_smarts', '')[:80]} |")
            else:
                lines.append("- AiZynth: no synthesis_route attached")
        lines.append("")

    art = plan_wrapper.get("_artifact_path")
    if art:
        lines.append(f"*Artifact: `{art}`*")
        lines.append("")
    return "\n".join(lines)


def assemble_session_retrosynthesis_markdown(
    session_dir: Path,
    targets: Optional[List[str]] = None,
) -> str:
    """Build ## Retrosynthesis section from all (or filtered) plan_*.json files."""
    retro_dir = session_dir / "retrosynthesis"
    if not retro_dir.is_dir():
        return "## Retrosynthesis\n\n*No retrosynthesis artifacts in session.*\n"

    all_plans: List[Dict[str, Any]] = []
    for path in sorted(retro_dir.glob("plan_*.json"), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_artifact_path"] = str(path)
            all_plans.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    if targets:
        filtered = []
        for plan in all_plans:
            if any(_targets_match(plan.get("target", ""), t) for t in targets):
                filtered.append(plan)
        plans = filtered
    else:
        seen: set[str] = set()
        plans = []
        for plan in all_plans:
            key = (plan.get("target") or "").strip()
            if key in seen:
                continue
            seen.add(key)
            plans.append(plan)

    lines = ["## Retrosynthesis", ""]
    if not plans:
        lines.append("*No plan artifacts matched the requested targets.*")
        lines.append("")
        return "\n".join(lines)

    summary = []
    for plan in plans:
        meta = (plan.get("result") or {}).get("metadata") or {}
        summary.append(
            f"- `{plan.get('target', '')}`: provenance=`{meta.get('route_provenance', 'none')}`, "
            f"aizynth={meta.get('aizynth_monomers_solved', 0)}/{meta.get('aizynth_monomers_attempted', 0)}"
        )
    lines.append("**Campaign summary:**")
    lines.extend(summary)
    lines.append("")

    for plan in plans:
        lines.append(format_plan_result_markdown(plan))

    return "\n".join(lines)


def parse_targets_csv(targets: str) -> Optional[List[str]]:
    if not targets or not targets.strip():
        return None
    return [t.strip() for t in targets.split(",") if t.strip()]
