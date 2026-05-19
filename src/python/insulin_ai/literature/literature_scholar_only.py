"""
Scholar-only literature pipeline: no Ollama or in-server LLM.
Agent reads titles/abstracts and proposes materials + PSMILES.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from insulin_ai.literature.scholar_client import SemanticScholarClient

# Unattended seed names (regex on title+abstract); not LLM extraction
_SEED_PATTERNS = [
    (r"\b(PEG|polyethylene glycol)\b", "PEG"),
    (r"\bPLGA\b", "PLGA"),
    (r"\bPVA\b", "PVA"),
    (r"\bPMMA\b", "PMMA"),
    (r"\bchitosan\b", "chitosan"),
    (r"\balginate\b", "alginate"),
    (r"\bcellulose\b", "cellulose"),
    (r"\bcollagen\b", "collagen"),
    (r"\bhyaluronic acid\b", "hyaluronic acid"),
    (r"\bhydrogel\b", "hydrogel"),
    (r"\bPLA\b", "PLA"),
    (r"\bpoly\(lactic[- ]co[- ]glycolic", "PLGA"),
    (r"\bdextran\b", "dextran"),
    (r"\bpoloxamer\b", "poloxamer"),
]


def generate_search_queries(
    iteration: int,
    base_user_query: str,
    top_candidates: Optional[List[str]] = None,
    stability_mechanisms: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    target_properties: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Same shape as IterativeLiteratureMiner._generate_dynamic_queries; pure Python."""
    base_queries = [
        base_user_query.strip() or "hydrogels insulin delivery transdermal patch",
        "polymer protein stabilization thermal insulin",
        "biocompatible materials drug delivery skin peptide",
        "nanomaterials insulin encapsulation controlled release",
    ]
    top_candidates = top_candidates or []
    stability_mechanisms = stability_mechanisms or []
    limitations = limitations or []
    target_properties = target_properties or {}

    if iteration == 1:
        return list(
            dict.fromkeys(
                base_queries
                + [
                    "protein stabilization polymers temperature insulin",
                    "peptide delivery hydrogels biocompatible",
                    "insulin stability materials room temperature",
                    "transdermal drug delivery patches polymer",
                ]
            )
        )

    if iteration <= 3:
        refined = list(base_queries)
        for material in top_candidates[:3]:
            refined.append(f"{material} insulin stabilization")
            refined.append(f"{material} protein drug delivery")
        for mechanism in stability_mechanisms[:2]:
            refined.append(f"protein stabilization {mechanism} insulin")
        return list(dict.fromkeys(refined))

    targeted: List[str] = []
    for material in top_candidates[:2]:
        for mechanism in stability_mechanisms[:2]:
            targeted.append(f"{material} {mechanism} insulin")
    for prop in list(target_properties.keys())[:2]:
        targeted.append(f"materials {prop} insulin delivery")
    if limitations:
        targeted.append(
            "insulin delivery polymer " + " ".join(limitations[:2])
        )
    return list(dict.fromkeys(targeted + base_queries[:2]))


def deduplicate_papers(papers: List[Dict]) -> List[Dict]:
    seen: Set[str] = set()
    out: List[Dict] = []
    for p in papers:
        key = p.get("paper_id") or p.get("title") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def seed_candidates_from_papers(
    papers: List[Dict], max_names: int = 24
) -> List[Dict]:
    """Deterministic material_name list from abstract/title keywords (autoresearch seed)."""
    seen: Set[str] = set()
    candidates: List[Dict] = []
    for p in papers:
        text = f"{p.get('title', '')} {p.get('abstract', '')}"
        if not text.strip():
            continue
        for pat, label in _SEED_PATTERNS:
            if re.search(pat, text, re.I) and label not in seen:
                seen.add(label)
                candidates.append(
                    {
                        "material_name": label,
                        "material_composition": label,
                        "chemical_structure": "",
                        "generation_method": "scholar_keyword_seed",
                    }
                )
                if len(candidates) >= max_names:
                    return candidates
    return candidates


def run_asta_mine(
    *,
    asta_api_key: Optional[str],
    base_query: str,
    iteration: int,
    top_candidates: Optional[List[str]] = None,
    stability_mechanisms: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    target_properties: Optional[Dict[str, Any]] = None,
    max_papers_per_query: int = 12,
    max_total_papers: int = 25,
    run_dir: Optional[Path] = None,
    num_candidates: int = 15,
) -> Dict[str, Any]:
    """
    Search via Asta MCP (search_papers_by_relevance); same result shape as run_scholar_mine.
    """
    from insulin_ai.literature.asta_client import search_papers_by_relevance_sync

    queries = generate_search_queries(
        iteration,
        base_query,
        top_candidates,
        stability_mechanisms,
        limitations,
        target_properties,
    )
    all_papers: List[Dict] = []
    for q in queries[:10]:
        try:
            papers = search_papers_by_relevance_sync(
                keyword=q, limit=max_papers_per_query, api_key=asta_api_key
            )
            all_papers.extend(papers)
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Asta query failed %s: %s", q[:40], e)
            continue
    unique = deduplicate_papers(all_papers)[:max_total_papers]
    material_candidates = seed_candidates_from_papers(unique, max_names=num_candidates)
    results = {
        "source": "asta",
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "search_queries": queries,
        "papers": unique,
        "papers_analyzed": len(unique),
        "material_candidates": material_candidates,
        "feedback_metadata": {
            "top_candidates": top_candidates,
            "stability_mechanisms": stability_mechanisms,
            "limitations": limitations,
            "target_properties": target_properties,
            "agent_extraction": True,
        },
    }
    if run_dir:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"literature_search_iter{iteration}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass
    return results


def run_scholar_mine(
    *,
    api_key: Optional[str] = None,
    base_query: str,
    iteration: int,
    top_candidates: Optional[List[str]] = None,
    stability_mechanisms: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    target_properties: Optional[Dict[str, Any]] = None,
    max_papers_per_query: int = 12,
    max_total_papers: int = 25,
    run_dir: Optional[Path] = None,
    num_candidates: int = 15,
) -> Dict[str, Any]:
    """
    Search Semantic Scholar; return papers + seed material_candidates + metadata.
    """
    scholar = SemanticScholarClient(api_key=api_key)
    queries = generate_search_queries(
        iteration,
        base_query,
        top_candidates,
        stability_mechanisms,
        limitations,
        target_properties,
    )
    all_papers: List[Dict] = []
    for q in queries[:10]:
        try:
            papers = scholar.search_papers_by_topic(
                topic=q, max_results=max_papers_per_query, recent_years_only=False
            )
            all_papers.extend(papers)
        except Exception:
            continue
    unique = deduplicate_papers(all_papers)[:max_total_papers]
    material_candidates = seed_candidates_from_papers(unique, max_names=num_candidates)

    results = {
        "source": "semantic_scholar",
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "search_queries": queries,
        "papers": unique,
        "papers_analyzed": len(unique),
        "material_candidates": material_candidates,
        "feedback_metadata": {
            "top_candidates": top_candidates,
            "stability_mechanisms": stability_mechanisms,
            "limitations": limitations,
            "target_properties": target_properties,
            "agent_extraction": True,
        },
    }
    if run_dir:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"literature_search_iter{iteration}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass
    return results


def format_mine_literature_text(results: Dict[str, Any], abstract_max: int = 900) -> str:
    """Human + agent readable block."""
    lines: List[str] = []
    src = results.get("source") or "semantic_scholar"
    src_label = "Asta MCP" if src == "asta" else "Semantic Scholar"
    lines.append(
        f"Iteration {results.get('iteration', 1)}: {results.get('papers_analyzed', 0)} papers ({src_label}). "
        "Read abstracts below; list polymer/material candidates and PSMILES with [*]; then validate_psmiles / openmm_evaluate_psmiles."
    )
    lines.append("")
    seeds = results.get("material_candidates") or []
    if seeds:
        lines.append("Keyword seeds (unattended autoresearch only; prefer your own reading):")
        for s in seeds[:20]:
            lines.append(f"  - {s.get('material_name', '')}")
        lines.append("")
    for i, p in enumerate(results.get("papers") or [], 1):
        title = (p.get("title") or "")[:200]
        year = p.get("year") or ""
        url = (p.get("url") or "")[:120]
        ab = (p.get("abstract") or "")[:abstract_max]
        lines.append(f"--- Paper {i} ---")
        lines.append(f"Title: {title}")
        lines.append(f"Year: {year}  URL: {url}")
        lines.append(f"Abstract:\n{ab}")
        lines.append("")
    lines.append(
        "Agent instruction: From the abstracts, extract distinct materials relevant to insulin patch polymers; "
        "output PSMILES with two [*] connection points per candidate; call validate_psmiles then openmm_evaluate_psmiles."
    )
    return "\n".join(lines)
