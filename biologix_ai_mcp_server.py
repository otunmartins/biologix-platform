#!/usr/bin/env python3
"""
Biologics AI MCP Server – Materials Discovery & Retrosynthesis Tools for OpenCode

Consolidated: literature mining, PaperQA2 RAG, PSMILES, MD, PubMed, arXiv,
Semantic Scholar, web search, retrosynthesis planning, ADMET screening.
Single MCP server for biologics stabilisation platform.
"""

import os
import sys
import json
import time
import shutil
import subprocess
import traceback
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    requests = None

# Project root
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src", "python"))
sys.path.insert(0, os.path.join(ROOT, "extern", "RetroSynthesisAgent"))

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP

from biologix_ai.run_paths import ENV_SESSION, new_session_dir, session_dir_from_env
from biologix_ai.discovery_world import (
    apply_patch,
    ensure_world_for_session,
    load_world,
    planning_context,
    save_world,
    touch_meta_after_iteration,
    world_path_for_session,
)
from biologix_ai.mcp_stdio_guard import install_stdio_guards
from biologix_ai.mcp_tool_guard import (
    McpProgressReporter,
    log_tool_budget,
    run_guarded_tool,
    run_instant_mcp_tool,
    truncate_mcp_json,
)


def _normalize_psmiles_list_for_eval(psmiles_list: Union[str, List[Any], None]) -> List[str]:
    """
    Build a list of PSMILES strings from MCP arguments.

    Some clients send a comma-separated string (schema-native); others send a JSON array of
    strings. A few send a single string that is itself a JSON array. All are accepted so tool
    validation does not abort before the handler runs.
    """
    if psmiles_list is None:
        return []
    if isinstance(psmiles_list, list):
        out: List[str] = []
        for p in psmiles_list:
            if p is None:
                continue
            s = str(p).strip().strip('"').strip("'")
            if s:
                out.append(s)
        return out

    s = str(psmiles_list).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [
                    str(x).strip().strip('"').strip("'")
                    for x in parsed
                    if str(x).strip()
                ]
        except json.JSONDecodeError:
            pass
    return [p.strip().strip('"').strip("'") for p in s.split(",") if p.strip()]


def _coerce_bool_flag(value: Any, default: bool = True) -> bool:
    """Accept bool or common string/int shapes from MCP clients."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("0", "false", "no", "off", ""):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
    return default


def _coerce_single_psmiles_string(value: Union[str, List[Any], None]) -> str:
    """
    Normalize MCP ``psmiles`` for ``validate_psmiles`` (one repeat unit).

    Hosts sometimes send a JSON array with one element or mangle quoting; this
    avoids schema/type failures and empty tool results.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        return str(value[0]).strip().strip('"').strip("'")
    s = str(value).strip()
    if s.startswith("[") and "[*]" in s:
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list) and len(parsed) == 1:
                return str(parsed[0]).strip()
        except json.JSONDecodeError:
            pass
    return s


def _abort_install_json(error: str, *, extra: Optional[dict] = None) -> str:
    """Structured failure when a required stack component is missing."""
    payload: dict = {
        "error": error,
        "abort": True,
        "fix": "./install",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, indent=2)


mcp = FastMCP(
    "biologics-ai",
    instructions=(
        "Biologics stabilisation: resolve_biologic_target, start_biologics_session, run_biologics_discovery; "
        "prepare_retrosynthesis, submit_retro_extractions, plan_retrosynthesis, assemble_retrosynthesis_report, "
        "check_monomer_admet, check_monomers_batch, compile_results (optional run_dir for session); "
        "ADMET and literature; PSMILES tools; OpenMM openmm_evaluate_psmiles; discovery world; transcripts. "
        "Arbitrary biologics via biologic_target and BIOLOGIX_AI_TARGET_PROTEIN_PDB after start_biologics_session. "
        "MCP TIMEOUT RULE: If any biologix-ai MCP tool call times out for any reason (host limit, hang, no JSON, "
        "transport error), the session LATCHES to CLI-only mode for ALL remaining steps — do NOT call any "
        "biologix-ai MCP tool again in that session. Use bash CLI only, per .opencode/MCP_CLI_FALLBACK.md "
        "(one CLI job at a time, append 2>&1)."
    ),
)


# --- PaperQA2 helpers (optional; paper-qa may not be installed) ---
def _paper_qa_available():
    try:
        import paperqa
        return True
    except ImportError:
        return False


def _paper_qa_settings():
    from biologix_ai.paper_qa_config import get_paper_qa_settings
    return get_paper_qa_settings()


def _paper_qa_index_status():
    """Check index status. Returns dict with ready, message, etc."""
    if not _paper_qa_available():
        return {"ready": False, "message": "paper-qa not installed. pip install paper-qa"}
    try:
        from pathlib import Path
        settings = _paper_qa_settings()
        paper_dir = Path(settings.agent.index.paper_directory)
        if not paper_dir.is_dir():
            return {"ready": False, "message": f"Paper dir not found: {paper_dir}"}
        total = sum(1 for f in paper_dir.rglob("*") if f.suffix.lower() == ".pdf")
        if total == 0:
            return {"ready": False, "message": f"No PDFs in {paper_dir}. Add papers and run index_papers."}
        # Check for built index (paper-qa stores in index_directory / get_index_name())
        index_dir = Path(settings.agent.index.index_directory)
        index_name = settings.get_index_name()
        manifest_path = index_dir / index_name / "files.zip"
        if not manifest_path.exists():
            return {"ready": total <= 10, "message": f"0/{total} indexed. Run index_papers first."}
        try:
            import pickle
            import zlib
            manifest = pickle.loads(zlib.decompress(manifest_path.read_bytes()))
            errored = sum(1 for v in manifest.values() if v == "ERROR")
            indexed = len(manifest) - errored
            unindexed = max(0, total - len(manifest))
            ready = unindexed <= 10 and errored == 0
            msg = f"{indexed}/{total} indexed"
            if errored:
                msg += f", {errored} errors"
            if unindexed:
                msg += f", {unindexed} unindexed"
            return {"ready": ready, "message": msg}
        except Exception:
            return {"ready": False, "message": f"Index may be incomplete. Try running index_papers."}
    except Exception as e:
        return {"ready": False, "message": str(e)}


@mcp.tool()
def mine_literature(
    query: str = "hydrogels insulin stabilization transdermal",
    max_candidates: int = 15,
    iteration: int = 1,
    top_candidates: str = "",
    stability_mechanisms: str = "",
    limitations: str = "",
    use_paper_qa: bool = True,
) -> str:
    """
    Literature: **Asta MCP** when ASTA_API_KEY is set (server-side); else Semantic Scholar REST.
    Optional PaperQA2 if indexed. You read abstracts and propose materials + PSMILES; then validate_psmiles / openmm_evaluate_psmiles.

    For iteration 2+, pass feedback from the previous iteration:
      top_candidates: comma-separated high performers (e.g. "chitosan,PEG")
      stability_mechanisms: comma-separated mechanisms (e.g. "hydrogen bonding,hydrophobic")
      limitations: comma-separated problems to avoid (e.g. "high_crystallinity")
    use_paper_qa: if True and papers are indexed, appends PaperQA2 synthesis to results.
    """
    out = []
    # Optional: PaperQA2 deep reading first (if indexed)
    if use_paper_qa and _paper_qa_available():
        status = _paper_qa_index_status()
        if status.get("ready"):
            try:
                import asyncio
                from paperqa import agent_query
                pqa_query = f"What polymer materials and stabilization mechanisms are effective for insulin delivery or transdermal patches? Query focus: {query}"
                if top_candidates or stability_mechanisms:
                    pqa_query += f". Prior high performers: {top_candidates or 'none'}. Mechanisms: {stability_mechanisms or 'none'}."
                settings = _paper_qa_settings()
                response = asyncio.run(agent_query(query=pqa_query, settings=settings))
                if response.session.formatted_answer:
                    out.append("--- PaperQA2 synthesis (from your indexed PDFs) ---")
                    out.append(response.session.formatted_answer)
                    out.append("")
            except Exception as e:
                out.append(f"(PaperQA2 skipped: {e})")
                out.append("")
        elif status.get("message"):
            out.append(f"(PaperQA2: {status['message']})")
            out.append("")

    try:
        import os
        from biologix_ai.literature.literature_scholar_only import (
            format_mine_literature_text,
            run_scholar_mine,
        )

        run_dir = session_dir_from_env(Path(ROOT))
        top = [s.strip() for s in top_candidates.split(",") if s.strip()] or None
        mechs = [s.strip() for s in stability_mechanisms.split(",") if s.strip()] or None
        lims = [s.strip() for s in limitations.split(",") if s.strip()] or None
        asta_key = os.environ.get("ASTA_API_KEY")
        if asta_key:
            from biologix_ai.literature.literature_scholar_only import run_asta_mine

            results = run_asta_mine(
                asta_api_key=asta_key,
                base_query=query,
                iteration=iteration,
                top_candidates=top,
                stability_mechanisms=mechs,
                limitations=lims,
                run_dir=run_dir,
                num_candidates=max_candidates,
            )
        else:
            results = run_scholar_mine(
                api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
                base_query=query,
                iteration=iteration,
                top_candidates=top,
                stability_mechanisms=mechs,
                limitations=lims,
                run_dir=run_dir,
                num_candidates=max_candidates,
            )
        out.append(format_mine_literature_text(results))
        return "\n".join(out)
    except Exception as e:
        return "\n".join(out) + f"\n\nError (mining): {e}" if out else f"Error: {e}"


@mcp.tool()
async def paper_qa(question: str) -> str:
    """
    Deep synthesis across indexed PDFs with citations (PaperQA2 RAG).
    Ask a question; get an answer from your papers/ directory with inline citations.
    Best for: mechanisms, material comparisons, literature-backed validation.
    If index incomplete, run index_papers first. Can take 30–90 seconds.
    """
    if not _paper_qa_available():
        return "paper-qa not installed. pip install paper-qa"
    status = _paper_qa_index_status()
    if not status.get("ready"):
        return f"Index incomplete: {status.get('message', 'Run index_papers first.')}"
    try:
        from paperqa import agent_query
        settings = _paper_qa_settings()
        response = await agent_query(query=question, settings=settings)
        return response.session.formatted_answer or f"PaperQA could not answer (status: {response.status})"
    except Exception as e:
        return f"PaperQA error: {e}"


@mcp.tool()
def paper_qa_index_status() -> str:
    """Check PaperQA2 index status (indexed/unindexed counts). Run index_papers to build."""
    status = _paper_qa_index_status()
    return status.get("message", str(status))


@mcp.tool()
def index_papers() -> str:
    """Build PaperQA2 search index over papers in papers/. Run once before using paper_qa. May take minutes for many PDFs."""
    if not _paper_qa_available():
        return "paper-qa not installed. pip install paper-qa"
    try:
        from biologix_ai.paper_qa_config import build_index
        return build_index()
    except Exception as e:
        return f"Index error: {e}"


@mcp.tool()
def lookup_material(material_name: str, max_results: int = 5) -> str:
    """
    Quick lookup for polymer/structure info when translating material names to PSMILES.
    Searches PubMed (API-free) for papers about the material's repeat unit, SMILES, or structure.
    Use when unsure about a material's PSMILES; then validate_psmiles your translation.
    """
    if not material_name or not material_name.strip():
        return "Error: provide a material name (e.g. chitosan, PLGA, PEG)."
    if not requests:
        return "Error: requests library required for lookup."
    query = f"{material_name.strip()} polymer repeat unit SMILES structure"
    try:
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        params = {
            "term": query,
            "db": "pubmed",
            "retmax": min(max_results, 10),
            "retmode": "json",
            "tool": "biologix-ai",
            "email": "research@example.com",
        }
        time.sleep(0.35)
        r = requests.get(f"{base}/esearch.fcgi", params=params, timeout=15)
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return f"No PubMed hits for '{material_name}'. Try a different query or use your chemistry knowledge."
        params2 = {"id": ",".join(ids), "db": "pubmed", "rettype": "xml", "tool": "biologix-ai", "email": "research@example.com"}
        time.sleep(0.35)
        r2 = requests.get(f"{base}/efetch.fcgi", params=params2, timeout=15)
        if r2.status_code != 200 or not r2.text.strip():
            return "Could not fetch abstracts."
        root = ET.fromstring(r2.content)
        lines = [f"PubMed lookup for '{material_name}' (query: {query})", ""]
        for art in root.findall(".//PubmedArticle")[:max_results]:
            title_el = art.find(".//ArticleTitle")
            abs_el = art.find(".//AbstractText")
            title = (title_el.text or "") if title_el is not None else ""
            abstract = (abs_el.text or "") if abs_el is not None else ""
            lines.append(f"Title: {title[:100]}...")
            lines.append(f"Abstract: {abstract[:500]}..." if len(abstract) > 500 else f"Abstract: {abstract}")
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Lookup error: {e}"


@mcp.tool()
def validate_psmiles(
    ctx: Context,
    psmiles: Union[str, List[Any]],
    material_name: str = "",
    crosscheck_web: bool = False,
) -> str:
    """
    Validate, annotate functional groups, and check name-structure consistency of a PSMILES.

    **Always returned:** ``valid``, ``canonical`` (when valid), and ``functional_groups``
    (RDKit SMARTS-based counts of carboxylic_acid, ester, ether, amine, amide, hydroxyl,
    aldehyde, ketone, aromatic, etc.).

    **When ``material_name`` is set:** ``name_consistency`` checks whether the name's
    implied chemistry (e.g. "acid" expects carboxylic_acid or ester) matches the actual
    functional groups. ``pubchem_lookup`` queries PubChem (cached per monomer in-process,
    bounded HTTP timeouts) for the monomer SMILES and Tanimoto similarity vs the repeat
    unit. If ``name_consistency.consistent`` is false, **fix the PSMILES before evaluating**.

    **When ``crosscheck_web`` is also true:** adds ``name_crosscheck`` with DuckDuckGo
    snippets (heuristic aid for manual comparison).
    """
    session = session_dir_from_env(Path(ROOT))

    def _run() -> Dict[str, Any]:
        from biologix_ai.material_mappings import (
            annotate_functional_groups,
            check_name_structure_consistency,
            lookup_monomer_pubchem,
            pubchem_timeout_s,
            validate_psmiles as _validate,
        )

        reporter = McpProgressReporter(
            ctx,
            tool="validate_psmiles",
            session_dir=session,
        )
        reporter.heartbeat("validating PSMILES", stage="validate", force=True)

        psm = _coerce_single_psmiles_string(psmiles)
        out = dict(_validate(psm))

        fg = annotate_functional_groups(psm)
        if fg.get("ok"):
            out["functional_groups"] = fg["groups"]
        else:
            out["functional_groups_error"] = fg.get("error", "unknown")

        name = (material_name or "").strip()
        if name:
            out["name_consistency"] = check_name_structure_consistency(name, psm)
            try:
                out["pubchem_lookup"] = lookup_monomer_pubchem(
                    name, psm, timeout=pubchem_timeout_s()
                )
            except Exception as e:
                out["pubchem_lookup"] = {"ok": False, "error": str(e)}

        if crosscheck_web and name:
            reporter.heartbeat("web crosscheck", stage="crosscheck_web")
            q = f"{name} polymer repeat unit SMILES structure"
            raw = _ddg_text_results(q, max_results=5)
            snippets = []
            for r in raw:
                snippets.append(
                    {
                        "title": (r.get("title") or "")[:120],
                        "snippet": (r.get("body") or r.get("snippet", ""))[:500],
                        "url": r.get("href") or r.get("link", ""),
                    }
                )
            out["name_crosscheck"] = {
                "material_name": name,
                "query": q,
                "snippets": snippets,
                "disclaimer": (
                    "Web snippets are for human/agent review only. They do not prove the PSMILES "
                    "matches the material name; compare chemistry carefully."
                ),
                "psmiles_submitted": psm.strip()[:200],
            }
        elif crosscheck_web and not name:
            out["name_crosscheck"] = {
                "error": "crosscheck_web requires a non-empty material_name",
            }
        return {"ok": True, **out}

    if crosscheck_web:
        payload = run_guarded_tool(
            "validate_psmiles",
            session,
            _run,
            stage="validate_crosscheck",
        )
        return json.dumps(truncate_mcp_json(payload), indent=2, default=str)

    try:
        from biologix_ai.material_mappings import (
            annotate_functional_groups,
            check_name_structure_consistency,
            lookup_monomer_pubchem,
            pubchem_timeout_s,
            validate_psmiles as _validate,
        )

        psm = _coerce_single_psmiles_string(psmiles)
        out = dict(_validate(psm))

        fg = annotate_functional_groups(psm)
        if fg.get("ok"):
            out["functional_groups"] = fg["groups"]
        else:
            out["functional_groups_error"] = fg.get("error", "unknown")

        name = (material_name or "").strip()
        if name:
            out["name_consistency"] = check_name_structure_consistency(name, psm)
            try:
                out["pubchem_lookup"] = lookup_monomer_pubchem(
                    name, psm, timeout=pubchem_timeout_s()
                )
            except Exception as e:
                out["pubchem_lookup"] = {"ok": False, "error": str(e)}

        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"valid": False, "error": str(e)})


@mcp.tool()
def openmm_evaluate_psmiles(
    ctx: Context,
    psmiles_list: Union[str, List[Any]] = "",
    verbose: Union[bool, str, int] = False,
    run_dir: str = "",
    artifacts_dir: str = "",
    max_workers: Optional[int] = None,
    response_format: str = "concise",
) -> str:
    """
    Evaluate PSMILES via OpenMM **Packmol matrix**: insulin AMBER14SB + multiple polymer
    chains (GAFF, Gasteiger) packed **bulk-in-cell** by default (or annulus **shell** via env), energy minimization, optional short NPT,
    then interaction energy (screening — not a multi-ns production MD).

    **psmiles_list:** comma-separated string (preferred in docs) **or** a JSON array of strings,
    e.g. ``"[*]CC[*],[*]O[*]"`` or ``["[*]CC[*]", "[*]O[*]"]``. OpenCode and other hosts vary;
    both shapes are accepted.

    **Requires the ``packmol`` binary on PATH** (conda-forge or ``pip install packmol``). If Packmol is
    missing, the tool fails immediately. See ``docs/OPENMM_SCREENING.md`` for matrix parameters
    (``BIOLOGIX_AI_OPENMM_MATRIX_*``, etc.). For a fast **single-oligomer** vacuum test without Packmol,
    use ``scripts/diagnose_openmm_complex.py`` — that path is **not** used here.

    By default (verbose=true) the JSON includes per-candidate timing and energies (evaluation_progress)
    and the MCP server logs detailed progress to stderr. Pass verbose=false for a smaller JSON payload;
    unless BIOLOGIX_AI_EVAL_QUIET=1 or BIOLOGIX_AI_EVAL_VERBOSE=0 is set, stderr still emits a short
    **heartbeat** line when each candidate starts and finishes (see docs/OPENMM_SCREENING.md). Set
    BIOLOGIX_AI_EVAL_QUIET=1 (or BIOLOGIX_AI_EVAL_VERBOSE=0) to silence stderr entirely.

    **response_format:** Controls the verbosity of the JSON returned to the caller.

    - ``"full"`` (default): full output including ``evaluation_progress``, ``evaluation_note``,
      and ``structure_artifact_paths`` (PNG paths for embedding in SUMMARY_REPORT). Use this
      when writing a SUMMARY_REPORT or when you need structure artifact paths.
    - ``"concise"``: strips ``evaluation_progress``, ``evaluation_note``, and
      ``structure_artifact_paths`` from the response, reducing token usage ~3x. Use this
      for discovery iterations where the LLM only needs energies and mechanisms.

    **candidate_outcomes** is always present regardless of ``response_format`` or ``verbose``.
    It is a compact per-candidate list with status and verbatim failure reason (Packmol timeout,
    wall-clock limit, OpenMM force-field error, prescreen rejection, etc.) suitable for saving
    in ``save_discovery_state`` for cross-iteration diagnostics.

    **Structure artifacts for SUMMARY_REPORT:** When ``run_dir`` is set (or ``BIOLOGIX_AI_SESSION_DIR``
    points at the session folder), minimized matrix complex PDB plus monomer 2D PNG (psmiles ``savefig``),
    preview PNG, and ribbon/chemviz PNG are written under ``<session>/structures/`` unless
    disabled with ``BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1``. Override the directory with
    non-empty ``artifacts_dir`` or env ``BIOLOGIX_AI_EVAL_ARTIFACTS_DIR``.

    **Parallel evaluation:** Pass ``max_workers`` (e.g. 2–4) to run candidates concurrently
    via ``ProcessPoolExecutor``. Default (``None``) reads ``BIOLOGIX_AI_EVAL_MAX_WORKERS`` from
    the environment, falling back to 1 (sequential). Each worker holds a full OpenMM matrix
    system in RAM — start conservatively. Parallel runs may differ slightly from sequential
    unless per-candidate seeds are fixed (they are: seed = base + candidate index).
    """
    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    from biologix_ai.simulation.md_simulator import _candidate_timeout_s

    log_tool_budget(
        session,
        tool="openmm_evaluate_psmiles",
        candidate_timeout_s=_candidate_timeout_s(),
        mcp_timeout_ms=int(os.environ.get("BIOLOGIX_AI_MCP_TIMEOUT_MS", "960000")),
    )
    reporter = McpProgressReporter(
        ctx,
        tool="openmm_evaluate_psmiles",
        session_dir=session,
    )

    def _progress_callback(event: Dict[str, Any]) -> None:
        stage = str(event.get("stage", "progress"))
        msg = str(event.get("message", stage))
        idx = event.get("candidate_index")
        total = event.get("total")
        progress_val = float(idx) + 1.0 if idx is not None else None
        total_val = float(total) if total is not None else None
        reporter.heartbeat(
            msg,
            stage=stage,
            progress=progress_val,
            total=total_val,
            extra=event,
        )

    def _run() -> Dict[str, Any]:
        reporter.heartbeat("openmm batch starting", stage="openmm_matrix_eval", force=True)
        parts = _normalize_psmiles_list_for_eval(psmiles_list)
        if not parts:
            return {
                "ok": False,
                "error": "psmiles_list is empty or was not provided",
                "hint": (
                    "You must pass psmiles_list as a comma-separated string or JSON array, e.g. "
                    "psmiles_list=\"[*]OCC[*],[*]CC(O)[*]\". "
                    "Build this list from the 'psmiles' field of screen_candidate_library results "
                    "where library_disposition='pass'. "
                    "Do NOT retry with only max_workers or response_format — psmiles_list is required."
                ),
                "received_type": type(psmiles_list).__name__,
                "received_value": repr(psmiles_list)[:120],
            }
        from biologix_ai.simulation.openmm_compat import openmm_available

        if not openmm_available():
            return {
                "ok": False,
                "error": (
                    "OpenMM screening stack incomplete (openmm, openmmforcefields, openff.toolkit, "
                    "and AmberTools antechamber/parmchk2 on PATH). Run ./install."
                ),
            }
        from biologix_ai.simulation import MDSimulator

        candidates = [
            {"material_name": f"Candidate_{i}", "chemical_structure": p}
            for i, p in enumerate(parts)
        ]
        sim = MDSimulator(n_steps=5000)
        ad = (artifacts_dir or "").strip()
        if not ad and (run_dir or "").strip():
            ad = str(_session_dir_for_mcp(run_dir) / "structures")
        vb = _coerce_bool_flag(verbose, default=False)
        concise = str(response_format).strip().lower() == "concise"
        result = sim.evaluate_candidates(
            candidates,
            max_candidates=len(candidates),
            verbose=vb,
            artifacts_dir=ad or None,
            max_workers=max_workers if max_workers is not None else 1,
            progress_callback=_progress_callback,
        )
        try:
            from biologix_ai.simulation.scoring import discovery_score

            _score = discovery_score(result)
        except Exception:
            _score = None

        candidate_outcomes = []
        for ep in result.get("evaluation_progress") or []:
            status = ep.get("status", "unknown")
            oc: Dict[str, Any] = {
                "index": ep.get("index"),
                "material_name": ep.get("material_name"),
                "status": status,
            }
            if status == "completed":
                oc["interaction_energy_kj_mol"] = ep.get("interaction_energy_kj_mol")
            else:
                if ep.get("stage"):
                    oc["stage"] = ep["stage"]
                if ep.get("reason"):
                    oc["reason"] = ep["reason"]
            candidate_outcomes.append(oc)

        out: Dict[str, Any] = {
            "ok": True,
            "high_performers": result["high_performers"],
            "effective_mechanisms": result["effective_mechanisms"],
            "problematic_features": result["problematic_features"],
        }
        if result.get("property_analysis"):
            out["property_analysis"] = result["property_analysis"]
        if _score is not None:
            out["discovery_score"] = round(_score, 4)
        out["candidate_outcomes"] = candidate_outcomes
        if not concise:
            if vb and result.get("evaluation_progress") is not None:
                out["evaluation_progress"] = result["evaluation_progress"]
            if result.get("evaluation_note"):
                out["evaluation_note"] = result["evaluation_note"]
        if result.get("structure_artifacts_dir"):
            out["structure_artifacts_dir"] = result["structure_artifacts_dir"]
        if not concise:
            raw = result.get("md_results_raw") or []
            paths = []
            for r in raw:
                if not isinstance(r, dict):
                    continue
                paths.append(
                    {
                        "psmiles": r.get("psmiles"),
                        "complex_pdb_path": r.get("complex_pdb_path"),
                        "monomer_png_path": r.get("monomer_png_path"),
                        "complex_preview_png_path": r.get("complex_preview_png_path"),
                        "complex_chemviz_png_path": r.get("complex_chemviz_png_path"),
                        "packing_metrics": r.get("packing_metrics"),
                    }
                )
            if paths:
                out["structure_artifact_paths"] = paths
        return out

    payload = run_guarded_tool(
        "openmm_evaluate_psmiles",
        session,
        _run,
        stage="openmm_matrix_eval",
        failure_hint=(
            "If MCP timed out for any reason, the session latches to CLI-only — do not call any "
            "biologix-ai MCP tool again; run scripts/run_openmm_matrix.py via bash for OpenMM and "
            "see .opencode/MCP_CLI_FALLBACK.md for all other steps."
        ),
    )
    return json.dumps(truncate_mcp_json(payload), indent=2, default=str)


@mcp.tool()
def generate_psmiles_from_name(ctx: Context, material_name: str) -> str:
    """
    Convert a polymer or monomer **name** to a PSMILES repeat-unit string.

    Resolution order:

    1. **Known polymer table** (~60 common polymers: PEG, PLA, PLGA, PCL, PS,
       PMMA, PVDF, chitosan, ...).  High confidence — no network call.
    2. **PubChem lookup** → monomer SMILES → automated polymerisation-site
       detection (vinyl C=C opening, hydroxy-acid condensation, amino-acid
       amide condensation).  Medium confidence.

    Examples::

        generate_psmiles_from_name("PEG")           → "[*]OCC[*]"
        generate_psmiles_from_name("polystyrene")    → "[*]CC([*])c1ccccc1"
        generate_psmiles_from_name("lactic acid")    → "[*]OC(=O)C(C)[*]"

    Returns JSON with ``ok``, ``psmiles``, ``source``, ``confidence``,
    ``mechanism`` (for PubChem auto), and ``md_compatible`` (prescreen result).
    If conversion fails, ``ok`` is false with ``error`` and the raw PubChem
    SMILES so the caller can attempt manual conversion.
    """
    session = session_dir_from_env(Path(ROOT))

    def _run() -> Dict[str, Any]:
        from biologix_ai.material_mappings import name_to_psmiles

        reporter = McpProgressReporter(
            ctx,
            tool="generate_psmiles_from_name",
            session_dir=session,
        )
        reporter.heartbeat(f"lookup {material_name}", stage="name_to_psmiles", force=True)
        result = name_to_psmiles(material_name)
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        return result

    payload = run_guarded_tool(
        "generate_psmiles_from_name",
        session,
        _run,
        stage="name_to_psmiles",
        failure_hint=(
            "If MCP timed out, session latches to CLI-only — no further MCP; use bash CLI per "
            ".opencode/MCP_CLI_FALLBACK.md (generate_psmiles_from_name python -c snippet)."
        ),
    )
    return json.dumps(truncate_mcp_json(payload), indent=2, default=str)


@mcp.tool()
def mutate_psmiles(library_size: int = 10, feedback_json: str = "") -> str:
    """
    Generate mutated PSMILES candidates via cheminformatics.
    Optionally pass feedback JSON with high_performer_psmiles, problematic_psmiles for feedback-guided mutation.
    Returns JSON list of candidates with material_name, chemical_structure.
    """
    try:
        import json as _json
        from biologix_ai.mutation import MaterialMutator, feedback_guided_mutation
        feedback = {}
        if feedback_json:
            feedback = _json.loads(feedback_json)
        if feedback.get("high_performer_psmiles"):
            cands = feedback_guided_mutation(feedback, library_size=library_size, random_seed=42)
        else:
            mutator = MaterialMutator(random_seed=42)
            cands = mutator.generate_library(library_size=library_size)
        return _json.dumps([
            {"material_name": c["material_name"], "chemical_structure": c["chemical_structure"]}
            for c in cands
        ], indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def start_discovery_session(run_name: str = "") -> str:
    """
    Start a new discovery session. All subsequent saves and autonomous runs can use this folder.
    Returns session_dir path; pass it to save_discovery_state(run_dir=...) or set BIOLOGIX_AI_SESSION_DIR in shell.
    Also snapshots the active agent instructions (biologics-delivery-discovery.md) to
    agent_instructions_snapshot.md inside the session folder for reproducibility.
    """
    try:
        d = new_session_dir(Path(ROOT), name=run_name.strip() or None)
        os.environ[ENV_SESSION] = str(d)
        snapshot_note = ""
        instructions_src = Path(ROOT) / ".opencode" / "agent" / "biologics-delivery-discovery.md"
        if instructions_src.exists():
            try:
                shutil.copy2(str(instructions_src), str(d / "agent_instructions_snapshot.md"))
                snapshot_note = " Agent instructions snapshotted to agent_instructions_snapshot.md."
            except Exception as snap_err:
                snapshot_note = f" Warning: could not snapshot agent instructions: {snap_err}"
        return json.dumps(
            {
                "session_dir": str(d),
                "note": (
                    "Server process now uses this session for mine_literature saves and "
                    "save_discovery_state when run_dir omitted." + snapshot_note
                ),
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def resolve_biologic_target(
    name_or_pdb_id: str,
    fetch_pdb: bool = True,
    run_dir: str = "",
) -> str:
    """Resolve a biologic name or 4-letter PDB ID to a local PDB path (bundled data or RCSB download).

    When ``run_dir`` is set (or ``BIOLOGIX_AI_SESSION_DIR`` is active and you omit ``run_dir`` after
    ``start_biologics_session``), the structure is cached under ``<session>/structures/biologic_<PDB>.pdb``.

    Returns JSON: ``pdb_id``, ``pdb_path``, ``fetch_ok``, ``errors``, etc.
    """
    from biologix_ai.services import biologic_resolver as bio_res

    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    bio = bio_res.resolve_biologic_target(
        name_or_pdb_id,
        Path(ROOT),
        session_dir=Path(session) if session else None,
        fetch_pdb=fetch_pdb,
    )
    return json.dumps(bio.model_dump(), indent=2, default=str)


@mcp.tool()
def start_biologics_session(
    biologic_target: str,
    polymer_target: str = "",
    run_name: str = "",
    fetch_pdb: bool = True,
) -> str:
    """Start a session for the biologics retrosynthesis workflow: new ``runs/<id>/``, world file, protein PDB.

    Sets ``BIOLOGIX_AI_SESSION_DIR`` and, when resolution succeeds, ``BIOLOGIX_AI_TARGET_PROTEIN_PDB`` so
    ``openmm_evaluate_psmiles`` uses the resolved structure (OpenMM matrix). Snapshots ``.opencode/agent/biologics-retrosynthesis.md``
    when present.
    """
    try:
        from biologix_ai.services import biologic_resolver as bio_res

        d = new_session_dir(Path(ROOT), name=(run_name.strip() or None))
        os.environ[ENV_SESSION] = str(d)
        bio = bio_res.resolve_biologic_target(
            biologic_target,
            Path(ROOT),
            session_dir=d,
            fetch_pdb=fetch_pdb,
        )
        if bio.pdb_path and bio.fetch_ok:
            os.environ["BIOLOGIX_AI_TARGET_PROTEIN_PDB"] = bio.pdb_path
        else:
            os.environ.pop("BIOLOGIX_AI_TARGET_PROTEIN_PDB", None)

        obj = (
            f"Biologics stabilisation: {biologic_target}"
            + (f"; polymer: {polymer_target}" if polymer_target.strip() else "")
        )
        ensure_world_for_session(d, objective=obj)
        wpath = world_path_for_session(d)
        world = load_world(wpath)
        meta_links = {
            "biologic_target": biologic_target.strip(),
            "polymer_target": (polymer_target or "").strip(),
            "biologic_pdb_id": bio.pdb_id,
            "biologic_pdb_path": bio.pdb_path,
        }
        world = apply_patch(world, {"meta": {"links": meta_links}})
        save_world(wpath, world)

        snap_note = ""
        instructions_src = Path(ROOT) / ".opencode" / "agent" / "biologics-retrosynthesis.md"
        if instructions_src.is_file():
            try:
                shutil.copy2(str(instructions_src), str(d / "agent_instructions_snapshot.md"))
                snap_note = " Agent instructions snapshotted to agent_instructions_snapshot.md."
            except OSError as e:
                snap_note = f" Warning: could not snapshot agent instructions: {e}"

        return json.dumps(
            {
                "session_dir": str(d),
                "biologic_resolution": bio.model_dump(),
                "note": "Use run_dir=this path in plan_retrosynthesis / compile_results for persistence." + snap_note,
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def run_autonomous_discovery(
    budget_minutes: float = 60.0,
    run_in_background: bool = True,
    run_name: str = "",
    md_steps: int = 5000,
    max_eval_per_iteration: int = 8,
) -> str:
    """
    Autonomous loop; all outputs live in one new folder runs/<session_id>/ (TSV, log, summary, iteration JSON).
    When run_in_background=True, subprocess logs to that folder's autoresearch_subprocess.log.
    """
    session_dir = new_session_dir(Path(ROOT), name=(run_name.strip() or f"autonomous_{time.strftime('%Y%m%d_%H%M%S')}"))
    log_out = session_dir / "autoresearch_subprocess.log"
    script = os.path.join(ROOT, "scripts", "run_autonomous_discovery.py")
    env = os.environ.copy()
    env["BIOLOGIX_AI_ROOT"] = ROOT
    env[ENV_SESSION] = str(session_dir)

    if run_in_background:
        if not os.path.isfile(script):
            return json.dumps({"error": f"Script not found: {script}"})
        cmd = [
            sys.executable,
            script,
            "--budget-minutes",
            str(budget_minutes),
            "--session-dir",
            str(session_dir),
            "--md-steps",
            str(md_steps),
            "--max-eval",
            str(max_eval_per_iteration),
        ]
        try:
            log_f = open(log_out, "a", encoding="utf-8")
            log_f.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} budget={budget_minutes}m ---\n")
            log_f.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_f.close()
            return json.dumps(
                {
                    "status": "started_background",
                    "pid": proc.pid,
                    "session_dir": str(session_dir),
                    "budget_minutes": budget_minutes,
                    "subprocess_log": str(log_out),
                    "results_tsv": str(session_dir / "autoresearch_results.tsv"),
                    "summary_json_when_done": str(session_dir / "autoresearch_summary.json"),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    try:
        from biologix_ai.autonomous_discovery import run_autonomous_discovery_loop

        summary = run_autonomous_discovery_loop(
            budget_minutes=budget_minutes,
            session_dir=session_dir,
            root=ROOT,
            md_steps=md_steps,
            max_eval_per_iteration=max_eval_per_iteration,
        )
        return json.dumps(summary, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "traceback": traceback.format_exc()})


@mcp.tool()
def run_biologics_discovery(
    biologic_target: str,
    polymer_target: str = "",
    budget_minutes: float = 60.0,
    run_in_background: bool = True,
    run_name: str = "",
    max_routes: int = 5,
    run_admet: bool = True,
    run_openmm: bool = False,
) -> str:
    """Scripted biologics retrosynthesis loop (retro + ADMET + compile; optional OpenMM). Outputs under ``runs/<id>/``."""
    session_dir = new_session_dir(
        Path(ROOT),
        name=(run_name.strip() or f"biologics_{time.strftime('%Y%m%d_%H%M%S')}"),
    )
    log_out = session_dir / "biologics_discovery_subprocess.log"
    script = os.path.join(ROOT, "scripts", "run_biologics_discovery.py")
    env = os.environ.copy()
    env["BIOLOGIX_AI_ROOT"] = ROOT
    env[ENV_SESSION] = str(session_dir)

    if run_in_background:
        if not os.path.isfile(script):
            return json.dumps({"error": f"Script not found: {script}"})
        cmd = [
            sys.executable,
            script,
            "--biologic-target",
            biologic_target,
            "--budget-minutes",
            str(budget_minutes),
            "--session-dir",
            str(session_dir),
            "--max-routes",
            str(max_routes),
        ]
        if polymer_target.strip():
            cmd.extend(["--polymer-target", polymer_target.strip()])
        if not run_admet:
            cmd.append("--no-admet")
        if run_openmm:
            cmd.append("--openmm")
        try:
            log_f = open(log_out, "a", encoding="utf-8")
            log_f.write(f"\n--- start biologics {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log_f.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_f.close()
            return json.dumps(
                {
                    "status": "started_background",
                    "pid": proc.pid,
                    "session_dir": str(session_dir),
                    "budget_minutes": budget_minutes,
                    "subprocess_log": str(log_out),
                    "summary_json_when_done": str(session_dir / "biologics_discovery_summary.json"),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    try:
        from biologix_ai.autonomous_biologics import run_biologics_discovery_loop

        summary = run_biologics_discovery_loop(
            biologic_target=biologic_target,
            polymer_target=polymer_target,
            session_dir=session_dir,
            root=ROOT,
            budget_minutes=budget_minutes,
            max_routes=max_routes,
            run_admet=run_admet,
            run_openmm=run_openmm,
        )
        return json.dumps(summary, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "traceback": traceback.format_exc()})


@mcp.tool()
def get_materials_status() -> str:
    """Get status of materials discovery system (MD, literature, PaperQA2, mutation)."""
    lines = ["Insulin AI Materials Discovery Status"]
    try:
        from biologix_ai.simulation import MDSimulator
        sim = MDSimulator()
        lines.append(f"MD Simulation: {'insulin + polymer (implicit solvent)' if sim.runner else 'unavailable'} (CPU)")
    except Exception:
        lines.append("MD Simulation: unavailable")
    try:
        from biologix_ai.mutation import MaterialMutator
        lines.append("Mutation: available (cheminformatics)")
    except ImportError:
        lines.append("Mutation: unavailable (pip install psmiles)")
    lines.append("Literature Mining: Semantic Scholar + agent extraction (no Ollama)")
    pqa = _paper_qa_index_status()
    lines.append(f"PaperQA2: {pqa.get('message', 'unavailable')}")
    return "\n".join(lines)


def _session_dir_for_mcp(run_dir: str = "") -> Path:
    if run_dir.strip():
        return Path(run_dir.strip()).resolve()
    d = session_dir_from_env(Path(ROOT))
    if d:
        return d
    d = new_session_dir(Path(ROOT), name=None)
    os.environ[ENV_SESSION] = str(d)
    return d


def _optional_session_dir(run_dir: str) -> Optional[Path]:
    if not (run_dir or "").strip():
        return None
    return Path(run_dir.strip()).resolve()


def _persist_retrosynthesis_plan(
    session: Path,
    target: str,
    biologic_target: str,
    result_dict: dict,
) -> str:
    from biologix_ai.services.biologics_session import patch_world_retrosynthesis, write_retrosynthesis_artifact

    stem = f"plan_{int(time.time())}"
    path = write_retrosynthesis_artifact(
        session,
        f"{stem}.json",
        {"target": target, "biologic_target": biologic_target, "result": result_dict},
    )
    try:
        rel_art = os.path.relpath(str(path), str(session))
    except ValueError:
        rel_art = str(path)
    patch_world_retrosynthesis(
        session,
        {
            "id": stem,
            "polymer_target": target,
            "biologic_target": biologic_target,
            "n_routes": len(result_dict.get("polymer_routes", [])),
            "artifact": rel_art,
        },
    )
    return str(path)


def _persist_compiled_report(session: Path, target: str, biologic_target: str, report_dict: dict) -> str:
    from biologix_ai.services.biologics_session import patch_world_retrosynthesis, write_retrosynthesis_artifact

    stem = f"compile_{int(time.time())}"
    path = write_retrosynthesis_artifact(
        session,
        f"{stem}.json",
        {"target": target, "biologic_target": biologic_target, "report": report_dict},
    )
    try:
        rel_art = os.path.relpath(str(path), str(session))
    except ValueError:
        rel_art = str(path)
    n_card = len(report_dict.get("scorecards", []))
    patch_world_retrosynthesis(
        session,
        {
            "id": stem,
            "polymer_target": target,
            "biologic_target": biologic_target,
            "n_routes": n_card,
            "artifact": rel_art,
            "kind": "compiled_report",
        },
    )
    return str(path)


def _persist_monomer_admet(session: Path, smiles: str, admet_dict: dict) -> None:
    from biologix_ai.services.biologics_session import write_retrosynthesis_artifact

    safe = "".join(c if c.isalnum() else "_" for c in smiles[:40])
    write_retrosynthesis_artifact(session, f"admet_{safe}_{int(time.time())}.json", admet_dict)


def _allowed_transcript_source(src: Path, repo_root: Path) -> bool:
    """Allow repo files or Cursor/OpenCode agent-transcripts under ~/.cursor."""
    try:
        src = src.resolve()
    except OSError:
        return False
    repo_root = repo_root.resolve()
    if src == repo_root or repo_root in src.parents:
        return True
    cursor_home = (Path.home() / ".cursor").resolve()
    if not cursor_home.is_dir():
        return False
    try:
        src.relative_to(cursor_home)
    except ValueError:
        return False
    return "agent-transcripts" in src.parts


@mcp.tool()
def save_session_transcript(
    content: str,
    filename: str = "SESSION_TRANSCRIPT.md",
    run_dir: str = "",
) -> str:
    """
    Save **text you provide** into the active discovery session. **Default biologics-delivery-discovery protocol:**
    call this **every iteration** if ``import_chat_transcript_file`` cannot be used (unknown JSONL path
    or copy failure), with a **complete** Markdown recap (tool calls, decisions, results). OpenCode
    does not mirror chat into ``runs/`` automatically.

    Writes UTF-8 to ``<session>/<filename>`` (default ``SESSION_TRANSCRIPT.md``) under the iteration
    output folder only — **not** under ``.cursor/``. For JSONL originals from disk, prefer
    ``import_chat_transcript_file``.
    """
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    fn = (filename or "SESSION_TRANSCRIPT.md").strip()
    if not fn or ".." in fn.replace("\\", "/"):
        return json.dumps({"error": "invalid filename"})

    def _run() -> Dict[str, Any]:
        path = session / fn
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "saved": str(path), "session_dir": str(session)}

    payload = run_instant_mcp_tool(
        "save_session_transcript",
        session,
        _run,
        stage="transcript_write",
        artifact_key="saved",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def import_chat_transcript_file(
    source_path: str,
    dest_filename: str = "",
    run_dir: str = "",
) -> str:
    """
    Copy a chat transcript **file** from disk **into** ``runs/<session>/`` (same folder as SUMMARY_REPORT
    and other iteration outputs). **Do not** use ``.cursor/`` as the **destination**; it may be the
    **source** path only. Allowed sources only:

    - Any path **under this repository** (biologix-ai), or
    - Files under ``~/.cursor/.../agent-transcripts/`` (OpenCode parent chat JSONL).

    **Materials discovery:** invoke **by default at the end of every iteration** so the session folder
    contains the OpenCode chat snapshot. If this fails, use ``save_session_transcript`` instead.
    See ``docs/OpenCode_PLATFORM.md``.
    """
    src = Path(source_path).expanduser()
    if not src.is_file():
        return json.dumps({"error": f"not a file: {src}"})
    if not _allowed_transcript_source(src, Path(ROOT)):
        return json.dumps(
            {
                "error": "path not allowed (use repo path or ~/.cursor/.../agent-transcripts/...)",
                "hint": "see docs/OpenCode_PLATFORM.md",
            },
            indent=2,
        )
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    dest = (dest_filename or "").strip() or src.name
    if ".." in dest.replace("\\", "/"):
        return json.dumps({"error": "invalid dest_filename"})
    out = session / dest

    def _run() -> Dict[str, Any]:
        shutil.copy2(src, out)
        return {"ok": True, "copied_to": str(out), "session_dir": str(session)}

    payload = run_instant_mcp_tool(
        "import_chat_transcript_file",
        session,
        _run,
        stage="transcript_import",
        artifact_key="copied_to",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def save_discovery_state(
    iteration: int,
    feedback_json: str,
    query_used: str = "",
    notes: str = "",
    run_dir: str = "",
) -> str:
    """
    Persist discovery state under the session folder (runs/.../iteration_N.json).
    If run_dir omitted, uses active session (start_discovery_session) or creates a new session.
    """
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session)
    try:
        feedback = json.loads(feedback_json) if feedback_json else {}
    except json.JSONDecodeError as e:
        return f"Error parsing feedback_json: {e}"

    def _run() -> Dict[str, Any]:
        from datetime import datetime

        state = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "query_used": query_used,
            "notes": notes,
            "feedback": feedback,
        }
        path = session / f"agent_iteration_{iteration}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

        wp = world_path_for_session(session)
        if wp.is_file():
            try:
                world = load_world(wp)
                world = touch_meta_after_iteration(world, iteration, path.name)
                save_world(wp, world)
            except OSError:
                pass
        return {"ok": True, "saved": str(path), "session_dir": str(session)}

    payload = run_instant_mcp_tool(
        "save_discovery_state",
        session,
        _run,
        stage="discovery_state",
        artifact_key="saved",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def load_discovery_state(iteration: int = 0, run_dir: str = "") -> str:
    """
    Load discovery state from session folder. iteration=0 loads latest agent_iteration_*.json.
    run_dir: session path; if empty, uses active session (same server as start_discovery_session / save).
    """
    if run_dir.strip():
        session = Path(run_dir.strip()).resolve()
    else:
        session = session_dir_from_env(Path(ROOT))
    if not session or not session.is_dir():
        return "No session directory. Call start_discovery_session or pass run_dir= path to runs/.../"

    if iteration > 0:
        path = session / f"agent_iteration_{iteration}.json"
        if not path.is_file():
            return f"No state file for iteration {iteration} in {session}."
    else:
        files = sorted(
            [f for f in os.listdir(session) if f.startswith("agent_iteration_") and f.endswith(".json")]
        )
        if not files:
            return f"No agent_iteration_*.json in {session}."
        path = session / files[-1]

    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.tool()
def get_discovery_world_state(run_dir: str = "", summary: bool = False) -> str:
    """
    Read ``discovery_world.json`` for the session (Kosmos-style structured rollup).
    If ``summary=true``, returns compact JSON with ``planning_context`` text only (smaller payload).
    If the file is missing, returns a fresh empty schema (or empty planning context when summary).
    """
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session)
    wp = world_path_for_session(session)

    def _run() -> Dict[str, Any]:
        data = load_world(wp)
        if _coerce_bool_flag(summary, default=False):
            ctx = planning_context(data, max_chars=12_000)
            return {
                "ok": True,
                "session_dir": str(session),
                "world_path": str(wp),
                "planning_context": ctx,
            }
        return {
            "ok": True,
            "session_dir": str(session),
            "world_path": str(wp),
            "world": data,
        }

    payload = run_instant_mcp_tool(
        "get_discovery_world_state",
        session,
        _run,
        stage="discovery_world_read",
    )
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def patch_discovery_world(patch_json: str, run_dir: str = "") -> str:
    """
    Merge a JSON patch into ``discovery_world.json`` (lists keyed by ``id`` are merged).
    Creates the file if missing. Use after iterations to record literature claims, simulation rows,
    hypotheses, open questions, and human directives.
    """
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session)
    wp = world_path_for_session(session)

    def _run() -> Dict[str, Any]:
        patch = json.loads(patch_json) if patch_json.strip() else {}
        if not isinstance(patch, dict):
            return {"ok": False, "error": "patch_json must be a JSON object"}
        existing = load_world(wp)
        merged = apply_patch(existing, patch)
        save_world(wp, merged)
        return {
            "ok": True,
            "world_path": str(wp),
            "session_dir": str(session),
            "meta": merged.get("meta", {}),
        }

    try:
        payload = run_instant_mcp_tool(
            "patch_discovery_world",
            session,
            _run,
            stage="discovery_world_patch",
        )
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, indent=2)
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def discovery_world_planning_context(max_chars: int = 8000, run_dir: str = "") -> str:
    """
    Return a bounded Markdown-friendly text block for prompts (objective, hypotheses, questions,
    human directives, recent literature/simulation summaries). Prefer this over full world JSON during discovery.
    """
    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session)
    wp = world_path_for_session(session)

    def _run() -> Dict[str, Any]:
        data = load_world(wp)
        n = int(max_chars) if max_chars else 8000
        if n < 500:
            n = 500
        if n > 50_000:
            n = 50_000
        ctx = planning_context(data, max_chars=n)
        return {
            "ok": True,
            "session_dir": str(session),
            "world_path": str(wp),
            "planning_context": ctx,
        }

    payload = run_instant_mcp_tool(
        "discovery_world_planning_context",
        session,
        _run,
        stage="discovery_world_context",
    )
    return json.dumps(payload, indent=2, ensure_ascii=False)


# --- Literature search (folded from lit-* servers) ---
@mcp.tool()
def semantic_scholar_search(query: str, max_results: int = 20) -> str:
    """Search Semantic Scholar. No API key required (rate limited). Set SEMANTIC_SCHOLAR_API_KEY for higher limits."""
    try:
        from biologix_ai.literature.scholar_client import SemanticScholarClient
        client = SemanticScholarClient(api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"))
        results = client.search_papers(query=query, limit=max_results)
        papers = results.get("data", [])
        lines = [f"Found {len(papers)} papers."]
        for i, p in enumerate(papers[:10], 1):
            title = p.get("title", "")[:80]
            year = p.get("year", "")
            lines.append(f"{i}. {title}... ({year})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _pubmed_esearch(query: str, retmax: int) -> list:
    if not requests:
        return []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params = {"term": query, "db": "pubmed", "retmax": retmax, "retmode": "json", "tool": "biologix-ai", "email": "research@example.com"}
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    time.sleep(0.35)
    r = requests.get(f"{base}/esearch.fcgi", params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def _pubmed_get_abstracts(ids: list) -> list:
    if not requests or not ids:
        return []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    params = {"id": ",".join(str(i) for i in ids), "db": "pubmed", "rettype": "xml", "tool": "biologix-ai", "email": "research@example.com"}
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    time.sleep(0.35)
    r = requests.get(f"{base}/efetch.fcgi", params=params, timeout=15)
    if r.status_code != 200 or not r.text.strip():
        return []
    root = ET.fromstring(r.content)
    out = []
    for art in root.findall(".//PubmedArticle"):
        aid = art.find(".//PMID")
        title = art.find(".//ArticleTitle")
        abstract = art.find(".//AbstractText")
        out.append({
            "pmid": aid.text if aid is not None else "",
            "title": (title.text or "") if title is not None else "",
            "abstract": (abstract.text or "") if abstract is not None else "",
        })
    return out


@mcp.tool()
def pubmed_search(query: str, max_results: int = 20) -> str:
    """Search PubMed for papers. No API key required. Set NCBI_API_KEY for higher rate limit."""
    try:
        ids = _pubmed_esearch(query, retmax=max_results)
        if not ids:
            return "No papers found."
        papers = _pubmed_get_abstracts(ids[:max_results])
        lines = [f"Found {len(papers)} papers."]
        for i, p in enumerate(papers[:10], 1):
            lines.append(f"{i}. {p['title'][:80]}... (PMID {p['pmid']})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def arxiv_search(query: str, max_results: int = 20) -> str:
    """Search arXiv for papers. No API key required."""
    if not requests:
        return "Error: requests required"
    try:
        params = {"search_query": f"all:{query}", "start": 0, "max_results": max_results, "sortBy": "relevance", "sortOrder": "descending"}
        r = requests.get("https://export.arxiv.org/api/query", params=params, headers={"User-Agent": "biologix-ai/1.0 (research@example.com)"}, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        lines = [f"Found {len(entries)} papers."]
        for i, entry in enumerate(entries[:10], 1):
            title = entry.find("atom:title", ns)
            aid = entry.find("atom:id", ns)
            t = (title.text or "").replace("\n", " ").strip() if title is not None else ""
            id_text = (aid.text or "").split("/")[-1] if aid is not None else ""
            lines.append(f"{i}. {t[:80]}... ({id_text})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _ddg_text_results(query: str, max_results: int = 5, timeout: float = 20.0) -> list:
    """Return raw DuckDuckGo text results, or empty list on failure.

    A hard per-call timeout (default 20 s) prevents DDGS from silently stalling
    when DuckDuckGo rate-limits or the connection hangs mid-stream.
    """
    import concurrent.futures

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []

    def _fetch() -> list:
        return list(DDGS(timeout=10).text(query, max_results=min(max_results, 10)))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_fetch)
            return future.result(timeout=timeout)
    except Exception:
        return []


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo. No API key. Use for material structures, PSMILES, polymer repeat units."""
    try:
        from duckduckgo_search import DDGS  # noqa: F401 — ensure package exists before _ddg_text_results
    except ImportError:
        return "Error: pip install duckduckgo-search"
    results = _ddg_text_results(query, max_results)
    if not results:
        return f"No results for: {query}"
    lines = [f"Web search: {query}", ""]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "")[:80]
        body = (r.get("body") or r.get("snippet", ""))[:400]
        url = r.get("href") or r.get("link", "")
        lines.append(f"{i}. {title}\n   {body}...\n   {url}")
        lines.append("")
    return "\n".join(lines).strip()


# --- PSMILES (Ramprasad, folded from psmiles-ramprasad) ---
def _psmiles_check():
    try:
        from psmiles import PolymerSmiles
        return None
    except ImportError:
        return "psmiles not installed. Use biologix-ai-sim env or: pip install git+https://github.com/FermiQ/psmiles.git"


@mcp.tool()
def psmiles_canonicalize(psmiles: str) -> str:
    """Canonicalize PSMILES (Ramprasad-Group). Returns unique representation."""
    err = _psmiles_check()
    if err:
        return err
    try:
        from psmiles import PolymerSmiles
        ps = PolymerSmiles(psmiles)
        c = ps.canonicalize
        if callable(c):
            c = c()
        return str(c)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def psmiles_dimerize(psmiles: str, star_index: int = 0) -> str:
    """Dimerize PSMILES at connection point. star_index: 0 or 1 for which [*]."""
    err = _psmiles_check()
    if err:
        return err
    try:
        from psmiles import PolymerSmiles
        ps = PolymerSmiles(psmiles)
        if hasattr(ps, "dimer"):
            return str(ps.dimer(star_index))
        return str(ps.dimerize(star_index=star_index))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def psmiles_fingerprint(psmiles: str, fingerprint_type: str = "rdkit") -> str:
    """Get fingerprint for PSMILES. Types: rdkit, mordred, polyBERT, morgan."""
    err = _psmiles_check()
    if err:
        return err
    try:
        from psmiles import PolymerSmiles
        fp = PolymerSmiles(psmiles).descriptor(fingerprint_type)
        if hasattr(fp, "tolist"):
            return json.dumps(fp.tolist()[:20])
        return str(fp)[:500]
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def psmiles_similarity(psmiles1: str, psmiles2: str) -> str:
    """Compute similarity between two PSMILES (Ramprasad-Group)."""
    err = _psmiles_check()
    if err:
        return err
    try:
        from psmiles import PolymerSmiles
        sim = PolymerSmiles(psmiles1).similarity(PolymerSmiles(psmiles2))
        return f"Similarity: {sim}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def render_psmiles_png(
    psmiles: str,
    output_basename: str = "",
    run_dir: str = "",
) -> str:
    """
    Render a 2D depiction of the polymer repeat unit to PNG (psmiles ``PolymerSmiles.savefig``).

    **Reporting workflow:** you (the agent) author ``SUMMARY_REPORT.md`` and embed figures with
    ``![caption](structures/<file>.png)`` after saving PNGs here. Requires **psmiles** (see
    ``docs/DEPENDENCIES.md``).

    Saves under ``<session>/structures/<basename>.png``. Session is the active discovery run
    (``start_discovery_session``) or ``run_dir`` when set. Use after ``validate_psmiles``.
    """
    err = _psmiles_check()
    if err:
        return err
    from biologix_ai.psmiles_drawing import safe_filename_basename, save_psmiles_png

    session = _session_dir_for_mcp(run_dir)
    session.mkdir(parents=True, exist_ok=True)

    def _run() -> Dict[str, Any]:
        struct = session / "structures"
        struct.mkdir(parents=True, exist_ok=True)
        base = (output_basename or "").strip() or safe_filename_basename(psmiles[:80])
        out = struct / f"{base}.png"
        r = save_psmiles_png(psmiles.strip(), out, overwrite=True)
        return {**r, "session_dir": str(session), "relative": f"structures/{out.name}"}

    payload = run_guarded_tool(
        "render_psmiles_png",
        session,
        _run,
        stage="psmiles_png",
        artifact_key="path",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def compile_discovery_markdown_to_pdf(
    markdown_path: str = "SUMMARY_REPORT.md",
    output_pdf_name: str = "SUMMARY_REPORT.pdf",
    run_dir: str = "",
) -> str:
    """
    Convert **agent-written** Markdown (default ``SUMMARY_REPORT.md``) to a PDF in the session folder.

    You compose the narrative, tables, and interpretation in Markdown; follow ``docs/SUMMARY_REPORT_STYLE.md``
    (research-paper tone, full journal-style references, avoid em-dash/colon AI prose patterns). Call
    ``render_psmiles_png`` for 2D figures, reference them in the MD, then run this tool to produce
    ``SUMMARY_REPORT.pdf``. Uses **markdown** + **fpdf2** + **Pillow** (see ``docs/DEPENDENCIES.md``).
    Local images (e.g. under ``structures/``) are re-encoded to RGB PNG for fpdf2; you do not need
    separate ``*_raster.png`` copies. Relative image paths are resolved against the session directory.
    """
    session = _session_dir_for_mcp(run_dir)
    from biologix_ai.discovery_report import compile_markdown_to_pdf

    md_name = markdown_path.strip() or "SUMMARY_REPORT.md"
    pdf_name = output_pdf_name.strip() or "SUMMARY_REPORT.pdf"

    def _run() -> Dict[str, Any]:
        return compile_markdown_to_pdf(
            session,
            markdown_filename=md_name,
            output_pdf_name=pdf_name,
        )

    payload = run_guarded_tool(
        "compile_discovery_markdown_to_pdf",
        session,
        _run,
        stage="pdf_compile",
        artifact_key="pdf",
        failure_hint=(
            "PDF compile failed; SUMMARY_REPORT.md may contain tables fpdf2 cannot render. "
            "Check tool_errors.log — a plain-text table fallback is attempted automatically."
        ),
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def write_discovery_summary_report(
    title: str = "Discovery summary",
    run_dir: str = "",
    include_all_iterations: bool = True,
) -> str:
    """
    **Optional batch helper** (not a substitute for an AI-written report): reads ``agent_iteration_*.json``,
    auto-builds a minimal **SUMMARY_REPORT.md** + PNGs + PDF from saved feedback only—use when you need
    a quick skeleton without narrative. Any openmm_evaluate_psmiles-style files already in ``structures/``
    (``*_monomer.png``, ``*_complex_preview.png``, ``*_complex_chemviz.png``, optional ``*_complex_minimized_pymol.png``)
    are embedded in the Markdown (and PDF) under each matching candidate slug, or in a **Molecular visualizations**
    section for filenames that do not match feedback labels (e.g. ``Candidate_0_*``). For **normal** scientific
    summaries, the agent should write ``SUMMARY_REPORT.md`` and call ``compile_discovery_markdown_to_pdf`` after
    ``render_psmiles_png`` (same image paths; see ``docs/SUMMARY_REPORT_STYLE.md``).
    Requires **psmiles**, **fpdf2**, **markdown** (see ``docs/DEPENDENCIES.md``).
    """
    session = _session_dir_for_mcp(run_dir)
    from biologix_ai.discovery_report import write_session_summary_reports

    def _run() -> Dict[str, Any]:
        return write_session_summary_reports(
            session,
            title=title,
            include_all_iterations=include_all_iterations,
        )

    payload = run_guarded_tool(
        "write_discovery_summary_report",
        session,
        _run,
        stage="summary_report",
        artifact_key="markdown",
    )
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# Retrosynthesis, ADMET, and results compilation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def prepare_retrosynthesis(
    target: str,
    biologic_target: str = "insulin",
    run_dir: str = "",
    max_pdfs: int = 5,
) -> str:
    """Download literature PDFs and prepare workspace for agent-backed retrosynthesis.

    Returns material_name, pdf_paths, and extraction_schema for the OpenCode agent to
    fill via submit_retro_extractions before calling plan_retrosynthesis.
    """
    from biologix_ai.services.retrosynthesis_service import (
        _is_retrosynthesisagent_available,
        prepare_retrosynthesis_workspace,
    )

    if not _is_retrosynthesisagent_available():
        return _abort_install_json(
            "RetroSynthesisAgent not installed. Run ./install (includes git submodules)."
        )

    session = _optional_session_dir(run_dir)
    if session is None:
        return json.dumps(
            {
                "error": "run_dir is required for prepare_retrosynthesis",
                "hint": "Pass run_dir to your session folder (e.g. runs/my-campaign)",
            },
            indent=2,
        )
    out = prepare_retrosynthesis_workspace(
        target=target,
        session_dir=session,
        max_pdfs=max_pdfs,
    )
    out["biologic_target"] = biologic_target
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
def submit_retro_extractions(
    run_dir: str,
    material_name: str,
    extractions: str,
    target: str = "",
) -> str:
    """Submit agent-produced reaction extractions for retrosynthesis tree building.

    ``extractions`` is a JSON object: paper_name -> reaction text (RetroSynAgent format).
    Pass ``target`` (PSMILES) when ``material_name`` may be ambiguous; names are canonicalized.
    """
    from biologix_ai.retrosynthesis.retro_adapter import (
        normalize_extractions,
        resolve_material_name,
        validate_extractions_for_tree,
        write_llm_res,
    )

    session = _optional_session_dir(run_dir)
    if session is None:
        return json.dumps({"error": "run_dir is required"})
    try:
        canonical = resolve_material_name(target or material_name, agent_provided_name=material_name)
        data = normalize_extractions(extractions)
        target_psmiles = target if target and "[*]" in target else ""
        llm_path, parse_stats = write_llm_res(
            session, canonical, data, target_psmiles=target_psmiles
        )
        validation = validate_extractions_for_tree(data, canonical)
        validation["parse_stats"] = parse_stats
        return json.dumps(
            {
                "ok": True,
                "material_name": canonical,
                "llm_res_path": str(llm_path),
                "paper_count": len(data),
                "validation": validation,
                "next_step": f"Call plan_retrosynthesis(target={target or material_name!r}, run_dir={run_dir!r})",
            },
            indent=2,
        )
    except ValueError as exc:
        try:
            canonical = resolve_material_name(target or material_name, agent_provided_name=material_name)
        except Exception:
            canonical = material_name
        return json.dumps(
            {
                "ok": False,
                "error": str(exc),
                "material_name": canonical,
                "validation": validate_extractions_for_tree({}, canonical),
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)


@mcp.tool()
def plan_retrosynthesis(
    target: str,
    biologic_target: str = "insulin",
    max_routes: int = 5,
    allowed_mechanisms: str = "",
    banned_reagents: str = "",
    run_dir: str = "",
    biologic_pdb_path: str = "",
) -> str:
    """Plan retrosynthetic routes for a target polymer excipient.

    **Agent-backed path (default, no RetroSyn OpenAI key):** prepare_retrosynthesis →
    agent extracts reactions → submit_retro_extractions → plan_retrosynthesis (with run_dir).

    **Engines:** RetroSynthesisAgent KG tree from session extractions (via
    ``submit_retro_extractions``); AiZynthFinder enriches leaf monomers when models are installed.

    When ``run_dir`` is set, uses session workspace and writes ``retrosynthesis/plan_*.json``.

    Args:
        target: polymer as PSMILES, SMILES, or common name (e.g. 'Polyimide', '[*]OCC[*]')
        biologic_target: the biologic being stabilized (e.g. 'insulin', 'adalimumab', 'trastuzumab')
        max_routes: maximum number of routes to return
        allowed_mechanisms: comma-separated (e.g. 'RAFT,condensation'); empty means all
        banned_reagents: comma-separated SMILES of reagents to exclude
        run_dir: session directory for persistence (optional)
        biologic_pdb_path: optional PDB path for metadata; defaults to ``BIOLOGIX_AI_TARGET_PROTEIN_PDB`` if set
    """
    from biologix_ai.retrosynthesis.models import (
        RetrosynthesisConstraints,
        RetrosynthesisRequest,
        PolymerizationType,
    )
    from biologix_ai.services.retrosynthesis_service import (
        _is_retrosynthesisagent_available,
        plan_retrosynthesis as _plan,
    )

    if not _is_retrosynthesisagent_available():
        return _abort_install_json(
            "RetroSynthesisAgent not installed. Run ./install (includes git submodules)."
        )

    mechanisms = None
    if allowed_mechanisms.strip():
        mechanisms = []
        for m in allowed_mechanisms.split(","):
            m = m.strip().upper()
            try:
                mechanisms.append(PolymerizationType(m))
            except ValueError:
                mechanisms.append(PolymerizationType.OTHER)

    banned = [r.strip() for r in banned_reagents.split(",") if r.strip()] if banned_reagents else []

    pdb_effective = (biologic_pdb_path or "").strip() or os.environ.get("BIOLOGIX_AI_TARGET_PROTEIN_PDB", "").strip()

    session = _optional_session_dir(run_dir)
    request = RetrosynthesisRequest(
        target=target,
        biologic_target=biologic_target,
        biologic_pdb_path=pdb_effective or None,
        session_dir=str(session) if session is not None else None,
        constraints=RetrosynthesisConstraints(
            max_routes=max_routes,
            allowed_mechanisms=mechanisms,
            banned_reagents=banned,
        ),
    )

    result = _plan(request)
    out = result.model_dump()
    if session is not None:
        try:
            art = _persist_retrosynthesis_plan(session, target, biologic_target, out)
            out["session_artifact"] = art
        except Exception as exc:
            out["session_persist_warning"] = str(exc)
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
def diagnose_retro_extractions(
    run_dir: str,
    material_name: str,
    target: str = "",
) -> str:
    """Diagnose why retrosynthesis returned no routes for a candidate.

    Analyses existing extraction files and reports per-reactant leaf-reachability
    without running the full KG tree build.  Call this when ``plan_retrosynthesis``
    returns ``kg_empty_after_session_extractions: true``.

    Returns:
        tree_root, reaction_count, parsed reactants, leaf_status per reactant
        (purchasable, resolution_source, blocking), missing_steps suggestions,
        and recommended_actions list.

    Args:
        run_dir: session directory (same as used for submit_retro_extractions).
        material_name: polymer name (human-readable, not PSMILES).
        target: optional PSMILES to help locate the workspace.
    """
    from biologix_ai.retrosynthesis.retro_adapter import (
        resolve_material_name,
        session_has_extractions,
    )
    from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace
    from biologix_ai.retrosynthesis.precursor_registry import (
        collect_reactants_from_extractions,
        diagnose_leaf_reachability,
        seed_workspace_precursors,
    )
    from biologix_ai.services.retrosynthesis_service import _find_extractions_material_name

    session = _optional_session_dir(run_dir)
    if session is None:
        return json.dumps({"error": "run_dir is required"})

    try:
        canonical = resolve_material_name(target or material_name, agent_provided_name=material_name)
        ws_name = _find_extractions_material_name(session, canonical, target if "[*]" in target else "")

        if not session_has_extractions(session, ws_name):
            return json.dumps({
                "ok": False,
                "error": f"No extractions found for {material_name!r}. Call submit_retro_extractions first.",
                "material_name": canonical,
            }, indent=2)

        dirs = ensure_workspace(session, ws_name)
        llm_path = dirs["results"] / "llm_res.json"
        results_dict: dict = {}
        try:
            results_dict = json.loads(llm_path.read_text(encoding="utf-8"))
        except Exception:
            pass

        reactants = collect_reactants_from_extractions(results_dict)
        # Seed to populate workspace caches
        resolution_map = seed_workspace_precursors(dirs["workspace"], reactants)
        leaf_status = diagnose_leaf_reachability(reactants)

        blocking = [n for n, s in leaf_status.items() if s["blocking"]]
        resolved = [n for n, s in leaf_status.items() if not s["blocking"]]

        missing_steps: list = []
        recommended_actions: list = []
        for name in blocking:
            missing_steps.append(
                f"{name!r} not purchasable — add a reaction step showing its synthesis, "
                f"or call register_retro_precursors(run_dir, {material_name!r}, "
                f'[{{"name": "{name}"}}]) if it is commercially available.'
            )
        if blocking:
            recommended_actions.append(
                "Submit additional upstream reactions for: " + ", ".join(blocking)
            )
            recommended_actions.append(
                "Or call register_retro_precursors to mark specialty monomers as purchasable"
            )
        else:
            recommended_actions.append(
                "All reactants resolve — re-run plan_retrosynthesis; check Products line matches "
                f"tree root {canonical.strip().lower()!r}"
            )

        # Count parsed reactions
        reaction_count = sum(
            text.lower().count("reactants:") for text in results_dict.values()
        )

        return json.dumps({
            "ok": True,
            "material_name": canonical,
            "tree_root": canonical.strip().lower(),
            "reaction_count": reaction_count,
            "reactants_found": sorted(reactants),
            "leaf_status": leaf_status,
            "blocking_count": len(blocking),
            "blocking_reactants": blocking,
            "resolved_reactants": resolved,
            "missing_steps": missing_steps,
            "recommended_actions": recommended_actions,
            "hint": (
                "Add upstream reactions for blocking reactants via submit_retro_extractions, "
                "then plan_retrosynthesis again."
            ),
        }, indent=2)

    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)


@mcp.tool()
def register_retro_precursors(
    run_dir: str,
    material_name: str,
    precursors: str,
) -> str:
    """Explicitly register specialty reagents as purchasable leaves for a campaign.

    Use when a precursor is commercially available but PubChem or the bundled DB
    cannot resolve it automatically (e.g. proprietary NCA reagents, specialty monomers).
    Must be called BEFORE plan_retrosynthesis for the registration to take effect.

    Args:
        run_dir: session directory.
        material_name: polymer target name (human-readable).
        precursors: JSON array of objects: [{\"name\": str, \"smiles\": str (optional)}]

    Returns:
        registered count, names added, workspace registry path.
    """
    from biologix_ai.retrosynthesis.retro_adapter import resolve_material_name
    from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace
    from biologix_ai.retrosynthesis.precursor_registry import (
        _workspace_precursors,
        get_bundled_precursors,
    )
    from biologix_ai.services.retrosynthesis_service import _find_extractions_material_name

    session = _optional_session_dir(run_dir)
    if session is None:
        return json.dumps({"error": "run_dir is required"})

    try:
        prec_list = json.loads(precursors) if isinstance(precursors, str) else precursors
        if not isinstance(prec_list, list):
            return json.dumps({"error": "precursors must be a JSON array"})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON for precursors: {exc}"})

    try:
        canonical = resolve_material_name(material_name, agent_provided_name=material_name)
        ws_name = _find_extractions_material_name(session, canonical)
        dirs = ensure_workspace(session, ws_name)
        ws = dirs["workspace"]
        registry_path = ws / "precursor_registry.json"

        registry: list = []
        if registry_path.is_file():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                if not isinstance(registry, list):
                    registry = []
            except Exception:
                registry = []

        # Load workspace-level substance_query_result
        substance_result_path = ws / "substance_query_result.json"
        substance_result: dict = {}
        if substance_result_path.is_file():
            try:
                substance_result = json.loads(substance_result_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        added: list = []
        for item in prec_list:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip().lower()
            smiles = (item.get("smiles") or "").strip() or None
            if not name:
                continue

            # Mark purchasable in module-level set (immediate effect)
            _workspace_precursors.add(name)
            substance_result[name] = True

            registry.append({
                "name": name,
                "smiles": smiles,
                "source": "agent_registered",
            })
            added.append(name)

        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        substance_result_path.write_text(json.dumps(substance_result, indent=2), encoding="utf-8")

        return json.dumps({
            "ok": True,
            "material_name": canonical,
            "registered_count": len(added),
            "registered_names": added,
            "registry_path": str(registry_path),
            "next_step": f"Call plan_retrosynthesis(target={material_name!r}, run_dir={run_dir!r})",
        }, indent=2)

    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, indent=2)


@mcp.tool()
def check_monomer_admet(
    smiles: str,
    run_dir: str = "",
) -> str:
    """Screen a monomer SMILES for toxicity using SMARTS alerts and ADMET-AI predictions.

    Returns SMARTS structural alerts (acrylamide, epoxide, etc.) and ADMET endpoint
    predictions (hERG, hepatotoxicity, mutagenicity). Use on residual monomers identified
    by plan_retrosynthesis.

    With ``run_dir``, writes a JSON artifact under ``<session>/retrosynthesis/``.

    Requires: pip install biologix-ai[admet] for ADMET-AI predictions.
    SMARTS screening works with rdkit only.
    """
    from biologix_ai.services.toxicity_service import screen_monomer

    result = screen_monomer(smiles)
    out = result.model_dump()
    session = _optional_session_dir(run_dir)
    if session is not None:
        try:
            _persist_monomer_admet(session, smiles, out)
        except Exception as exc:
            out["session_persist_warning"] = str(exc)
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
def check_monomers_batch(
    smiles_list: Union[str, List[Any]],
    run_dir: str = "",
) -> str:
    """Screen multiple monomer SMILES for toxicity (SMARTS + ADMET-AI).

    Pass a comma-separated string or JSON array of SMILES.
    With ``run_dir``, writes one artifact per SMILES under ``<session>/retrosynthesis/``.
    """
    from biologix_ai.services.toxicity_service import (
        _is_admet_available,
        screen_monomers_batch,
    )

    if not _is_admet_available():
        return _abort_install_json(
            "ADMET-AI not importable. Run ./install (includes ADMET-AI submodule)."
        )

    parsed = _normalize_psmiles_list_for_eval(smiles_list)
    results = screen_monomers_batch(parsed)
    out = [r.model_dump() for r in results]
    session = _optional_session_dir(run_dir)
    if session is not None:
        for r in results:
            try:
                _persist_monomer_admet(session, r.smiles, r.model_dump())
            except Exception:
                pass
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
def compile_results(
    target: str,
    biologic_target: str = "insulin",
    max_routes: int = 5,
    run_admet: bool = True,
    run_dir: str = "",
    biologic_pdb_path: str = "",
    use_cached_plan: bool = True,
) -> str:
    """Run full pipeline and compile results: retrosynthesis + ADMET screening + ranking.

    This is the final reasoning step: runs retrosynthesis, screens monomers for toxicity,
    ranks routes by composite score, and produces a structured report with recommended
    next steps. Call this when you want the full picture, or call plan_retrosynthesis
    and check_monomer_admet separately for step-by-step control.

    With ``run_dir``, persists the compiled report under ``<session>/retrosynthesis/`` and patches
    ``discovery_world.json``.

    Args:
        target: polymer as PSMILES, SMILES, or common name
        biologic_target: the biologic being stabilized
        max_routes: max routes to evaluate
        run_admet: whether to run ADMET-AI on identified monomers
        run_dir: session directory for persistence
        biologic_pdb_path: optional; defaults to ``BIOLOGIX_AI_TARGET_PROTEIN_PDB`` env if set
    """
    from biologix_ai.retrosynthesis.models import (
        RetrosynthesisConstraints,
        RetrosynthesisRequest,
    )
    from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis as _plan
    from biologix_ai.services.toxicity_service import screen_monomer
    from biologix_ai.services.results_compiler import compile_results as _compile

    pdb_effective = (biologic_pdb_path or "").strip() or os.environ.get("BIOLOGIX_AI_TARGET_PROTEIN_PDB", "").strip()

    session = _optional_session_dir(run_dir)
    request = RetrosynthesisRequest(
        target=target,
        biologic_target=biologic_target,
        biologic_pdb_path=pdb_effective or None,
        session_dir=str(session) if session is not None else None,
        constraints=RetrosynthesisConstraints(max_routes=max_routes),
    )

    retro_result = None
    if session is not None and use_cached_plan:
        from biologix_ai.services.retrosynthesis_service import load_cached_plan_result

        retro_result = load_cached_plan_result(session, target)
    if retro_result is None:
        retro_result = _plan(request)

    tox_results = {}
    if run_admet:
        seen_smiles = set()
        for route in retro_result.polymer_routes:
            for monomer in route.monomers:
                if monomer.smiles not in seen_smiles:
                    seen_smiles.add(monomer.smiles)
                    tox_results[monomer.smiles] = screen_monomer(monomer.smiles)

    report = _compile(retro_result, tox_results=tox_results)
    rep_dict = report.model_dump()
    if session is not None:
        try:
            art = _persist_compiled_report(session, target, biologic_target, rep_dict)
            rep_dict["session_artifact"] = art
        except Exception as exc:
            rep_dict["session_persist_warning"] = str(exc)
    return json.dumps(rep_dict, indent=2, default=str)


@mcp.tool()
def assemble_retrosynthesis_report(
    run_dir: str,
    targets: str = "",
    include_compile_narrative: bool = False,
    biologic_target: str = "insulin",
) -> str:
    """Build markdown retrosynthesis section from session plan_*.json artifacts.

    Writes ``retrosynthesis/RETROSYNTHESIS_REPORT.md``. Use output verbatim in
    SUMMARY_REPORT § Retrosynthesis. ``targets``: comma-separated PSMILES/names; empty = all plans.
    """
    from biologix_ai.retrosynthesis.retro_report import (
        assemble_session_retrosynthesis_markdown,
        parse_targets_csv,
    )

    session = _optional_session_dir(run_dir)
    if session is None:
        return json.dumps({"error": "run_dir is required"})
    target_list = parse_targets_csv(targets)
    md = assemble_session_retrosynthesis_markdown(session, target_list)
    out_path = session / "retrosynthesis" / "RETROSYNTHESIS_REPORT.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    provenance_summary = []
    if include_compile_narrative and target_list:
        extra = []
        for t in target_list:
            try:
                comp = json.loads(
                    compile_results(
                        target=t,
                        biologic_target=biologic_target,
                        run_dir=run_dir,
                        run_admet=False,
                        use_cached_plan=True,
                    )
                )
                if comp.get("narrative"):
                    extra.append(f"### Compile narrative: {t}\n\n{comp['narrative']}")
            except Exception as exc:
                extra.append(f"### Compile failed for {t}: {exc}")
        if extra:
            md = md + "\n\n" + "\n\n".join(extra)
            out_path.write_text(md, encoding="utf-8")

    return json.dumps(
        {
            "ok": True,
            "markdown_path": str(out_path),
            "markdown": md,
            "polymer_count": md.count("### "),
            "provenance_summary": provenance_summary,
        },
        indent=2,
        default=str,
    )


# ---------------------------------------------------------------------------
# NovoMCP-inspired composite and pipeline tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_candidate_profile(
    psmiles: str,
    biologic_target: str = "insulin",
    run_retro: bool = True,
    run_admet: bool = True,
    run_compliance: bool = True,
    jurisdiction: str = "FDA,EMA",
    run_dir: str = "",
) -> str:
    """Single-call candidate dossier for one polymer PSMILES.

    Combines validate_psmiles + ADMET screening + retrosynthesis metadata +
    excipient compliance into one structured JSON response. Equivalent to
    NovoMCP ``get_molecule_profile`` for polymers.

    With ``run_dir``, persists ADMET and retro artifacts to the session folder and
    logs audit records for each stage.

    Args:
        psmiles: Polymer SMILES repeat unit (with [*] connection points).
        biologic_target: Biologic being stabilised (e.g. 'insulin', 'adalimumab').
        run_retro: Whether to run plan_retrosynthesis.
        run_admet: Whether to screen residual monomers with ADMET.
        run_compliance: Whether to check excipient compliance (EMA/FDA/GRAS).
        jurisdiction: Comma-separated jurisdictions for compliance (FDA, EMA).
        run_dir: Session directory for persistence and audit.
    """
    profile: dict = {"psmiles": psmiles, "biologic_target": biologic_target}
    session = _optional_session_dir(run_dir)

    # 1. Validate
    try:
        val_result = json.loads(validate_psmiles(psmiles))
        profile["validation"] = val_result
        if session:
            disp = "pass" if val_result.get("valid") else "fail"
            from biologix_ai.services.pipeline_audit import save_pipeline_stage as _audit
            _audit(session, psmiles, "validation", disp,
                   json.dumps({"functional_groups": val_result.get("functional_groups", {})}))
    except Exception as exc:
        profile["validation"] = {"error": str(exc)}

    # 2. ADMET on the PSMILES itself (treating it as a monomer-like SMILES)
    if run_admet:
        try:
            smiles_bare = psmiles.replace("[*]", "").strip()
            admet_result = json.loads(check_monomer_admet(smiles=smiles_bare, run_dir=run_dir))
            profile["admet"] = admet_result
            if session:
                from biologix_ai.services.pipeline_audit import save_pipeline_stage as _audit
                disp = "pass" if admet_result.get("safe") else "fail"
                _audit(session, psmiles, "admet", disp,
                       json.dumps({"alerts": admet_result.get("structural_alerts", [])}))
        except Exception as exc:
            profile["admet"] = {"error": str(exc)}

    # 3. Retrosynthesis
    if run_retro:
        try:
            retro_result = json.loads(plan_retrosynthesis(
                target=psmiles,
                biologic_target=biologic_target,
                max_routes=3,
                run_dir=run_dir,
            ))
            n_routes = len(retro_result.get("polymer_routes", []))
            profile["retrosynthesis"] = {
                "n_routes": n_routes,
                "routes_summary": [
                    {
                        "steps": len(r.get("steps", [])),
                        "monomers": [m.get("smiles") for m in r.get("monomers", [])],
                        "recommended": r.get("recommended", False),
                    }
                    for r in retro_result.get("polymer_routes", [])[:3]
                ],
                "warnings": retro_result.get("warnings", []),
            }
            if session:
                from biologix_ai.services.pipeline_audit import save_pipeline_stage as _audit
                _audit(session, psmiles, "retro",
                       "pass" if n_routes else "warning",
                       f"routes={n_routes}")
        except Exception as exc:
            profile["retrosynthesis"] = {"error": str(exc)}

    # 4. Compliance
    if run_compliance:
        try:
            comp = check_excipient_compliance(psmiles=psmiles, jurisdiction=jurisdiction, run_dir=run_dir)
            comp_dict = json.loads(comp)
            profile["compliance"] = comp_dict
            if session:
                from biologix_ai.services.pipeline_audit import save_pipeline_stage as _audit
                status = comp_dict.get("overall_status", "unknown")
                disp = "pass" if status == "approved" else ("fail" if status == "flagged" else "warning")
                _audit(session, psmiles, "compliance", disp, f"status={status}")
        except Exception as exc:
            profile["compliance"] = {"error": str(exc)}

    return json.dumps(profile, indent=2, default=str)


@mcp.tool()
def screen_candidate_library(
    psmiles_list: Union[str, List[Any]],
    biologic_target: str = "insulin",
    run_retro: bool = False,
    run_admet: bool = True,
    run_compliance: bool = True,
    jurisdiction: str = "FDA,EMA",
    max_candidates: int = 50,
    run_dir: str = "",
) -> str:
    """Batch screen a candidate library: validate + ADMET + optional retro + compliance.

    Equivalent to NovoMCP ``screen_library``. Returns a ranked JSON array with
    per-candidate profile and a composite pass/fail/warning disposition.

    With ``run_dir``, persists ADMET artifacts and audit records per candidate.

    Args:
        psmiles_list: Comma-separated string or JSON array of PSMILES strings.
        biologic_target: Biologic being stabilised.
        run_retro: Include retrosynthesis routes (slower; set True for top candidates).
        run_admet: Screen residual monomers with ADMET-AI.
        run_compliance: Check excipient compliance.
        jurisdiction: Comma-separated jurisdictions (FDA, EMA).
        max_candidates: Hard cap on library size.
        run_dir: Session directory for persistence.
    """
    if run_admet:
        from biologix_ai.services.toxicity_service import _is_admet_available

        if not _is_admet_available():
            return _abort_install_json(
                "ADMET-AI not importable (run_admet=true). Run ./install."
            )

    candidates = _normalize_psmiles_list_for_eval(psmiles_list)[:max_candidates]
    results = []
    for psmiles in candidates:
        try:
            profile = json.loads(get_candidate_profile(
                psmiles=psmiles,
                biologic_target=biologic_target,
                run_retro=run_retro,
                run_admet=run_admet,
                run_compliance=run_compliance,
                jurisdiction=jurisdiction,
                run_dir=run_dir,
            ))
        except Exception as exc:
            profile = {"psmiles": psmiles, "error": str(exc)}

        # Derive overall disposition
        disposition = "pass"
        if profile.get("validation", {}).get("valid") is False:
            disposition = "fail"
        elif profile.get("admet", {}).get("safe") is False:
            disposition = "fail"
        elif profile.get("compliance", {}).get("overall_status") == "flagged":
            disposition = "warning"

        profile["library_disposition"] = disposition
        results.append(profile)

    # Sort: pass first, then warning, then fail
    order = {"pass": 0, "warning": 1, "fail": 2}
    results.sort(key=lambda r: order.get(r.get("library_disposition", "fail"), 2))
    return json.dumps(results, indent=2, default=str)


@mcp.tool()
def check_excipient_compliance(
    psmiles: str,
    jurisdiction: str = "FDA,EMA",
    check_gras: bool = True,
    check_immunogenicity: bool = True,
    run_dir: str = "",
) -> str:
    """Regulatory excipient compliance check.

    Checks a polymer PSMILES against EMA/FDA approved excipient databases, GRAS
    status, and structural immunogenicity alerts (anti-PEG motifs, etc.). Equivalent
    to NovoMCP FAVES / ``check_compliance``.

    Aligns with blueprint section 4 (toxicity) and scoring dimension 5
    (Regulatory Precedent).

    Args:
        psmiles: Polymer SMILES repeat unit.
        jurisdiction: Comma-separated jurisdictions to check (FDA, EMA).
        check_gras: Whether to report FDA GRAS status.
        check_immunogenicity: Whether to run immunogenicity SMARTS alerts.
        run_dir: Session directory for audit persistence.
    """
    from biologix_ai.services.compliance_service import check_excipient_compliance as _check

    result = _check(
        psmiles=psmiles,
        jurisdiction=jurisdiction,
        check_gras=check_gras,
        check_immunogenicity=check_immunogenicity,
    )
    out = result.to_dict()
    session = _optional_session_dir(run_dir)
    if session:
        try:
            from biologix_ai.services.pipeline_audit import save_pipeline_stage as _audit
            disp = "pass" if result.overall_status == "approved" else (
                "fail" if result.overall_status == "flagged" else "warning"
            )
            _audit(session, psmiles, "compliance", disp,
                   json.dumps({"overall_status": result.overall_status,
                                "immunogenicity_flags": len(result.immunogenicity_flags)}))
        except Exception:
            pass
    return json.dumps(out, indent=2, default=str)


@mcp.tool()
def save_funnel_context(
    stage: str,
    checkpoint_data: str,
    run_dir: str = "",
) -> str:
    """Persist a named pipeline checkpoint so the session can be resumed.

    Equivalent to NovoMCP ``save_funnel_context``. Writes a JSON checkpoint
    to ``<session>/checkpoints/<stage>.json`` and updates the stage manifest.

    Use after each major pipeline phase (e.g. "post_screening", "post_retro",
    "post_compile") so that a resumed session can call get_funnel_context and
    skip already-completed phases.

    Args:
        stage: Pipeline stage label (e.g. "post_screening", "post_retro").
        checkpoint_data: JSON string containing the pipeline state to persist
            (e.g. top candidates, retro results, compliance summaries).
        run_dir: Session directory. Defaults to active BIOLOGIX_AI_SESSION_DIR.
    """
    from biologix_ai.services.funnel_context import save_funnel_context as _save

    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    if session is None:
        return json.dumps({"error": "No session directory. Provide run_dir or call start_biologics_session first."})
    try:
        data = json.loads(checkpoint_data) if isinstance(checkpoint_data, str) else checkpoint_data
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"checkpoint_data is not valid JSON: {exc}"})

    def _run() -> Dict[str, Any]:
        path = _save(stage=stage, checkpoint_data=data, session_dir=Path(session))
        return {"ok": True, "saved": True, "stage": stage, "path": str(path)}

    payload = run_instant_mcp_tool(
        "save_funnel_context",
        session,
        _run,
        stage="funnel_checkpoint",
        artifact_key="path",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def get_funnel_context(
    stage: str = "",
    run_dir: str = "",
) -> str:
    """Retrieve a named pipeline checkpoint or the most recent one.

    Equivalent to NovoMCP ``get_funnel_context``. Returns the checkpoint data
    persisted by save_funnel_context, or null when no checkpoints exist.

    Use at the start of a resumed session or at the beginning of each iteration
    to check for prior completed phases before re-running them.

    Args:
        stage: Stage to retrieve (e.g. "post_retro"). Empty = latest checkpoint.
        run_dir: Session directory.
    """
    from biologix_ai.services.funnel_context import get_funnel_context as _get, list_funnel_stages

    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    if session is None:
        return json.dumps({"checkpoint": None, "stages_available": []})

    def _run() -> Dict[str, Any]:
        cp = _get(session_dir=Path(session), stage=stage)
        stages = list_funnel_stages(Path(session))
        return {
            "ok": True,
            "checkpoint": cp,
            "stages_available": [s.get("stage") for s in stages],
        }

    payload = run_instant_mcp_tool(
        "get_funnel_context",
        session,
        _run,
        stage="funnel_read",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def save_pipeline_stage(
    ctx: Context,
    candidate_psmiles: str,
    stage: str,
    disposition: str,
    detail: str = "",
    run_dir: str = "",
) -> str:
    """Append an audit record for one pipeline stage applied to one candidate.

    Equivalent to NovoMCP ``save_funnel_stage``. Records are written to an
    append-only JSONL file under ``<session>/audit/pipeline_audit.jsonl`` and
    are never modified — providing a GxP / 21 CFR Part 11 compliant audit trail.

    Args:
        candidate_psmiles: The polymer PSMILES being processed.
        stage: Pipeline stage label: "validation", "admet", "retro", "compliance",
            "scoring", "openmm", "compile", etc.
        disposition: Outcome: "pass", "fail", or "warning".
        detail: Optional JSON or text explaining the disposition (alert names,
            scores, exclusion reason, route count, etc.).
        run_dir: Session directory.

    Note: This append is instant via MCP **before latch** (capped by ``BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S``,
    default 30 s). After **any MCP timeout**, the session latches to CLI-only — use the
    save_pipeline_stage CLI one-liner in .opencode/MCP_CLI_FALLBACK.md instead of calling this MCP tool again.
    """
    from biologix_ai.services.pipeline_audit import save_pipeline_stage as _save

    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    if session is None:
        return json.dumps({"error": "No session directory. Provide run_dir or call start_biologics_session first."})

    def _run() -> Dict[str, Any]:
        record = _save(
            session_dir=Path(session),
            candidate_psmiles=candidate_psmiles,
            stage=stage,
            disposition=disposition,
            detail=detail,
        )
        return {"ok": True, "recorded": True, "audit_id": record["audit_id"]}

    payload = run_instant_mcp_tool(
        "save_pipeline_stage",
        session,
        _run,
        stage="pipeline_audit",
    )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def get_pipeline_audit(
    candidate_psmiles: str = "",
    run_dir: str = "",
) -> str:
    """Retrieve the audit trail for one candidate or the entire session.

    Equivalent to NovoMCP ``get_pipeline_audit``. Returns every audit record
    appended by save_pipeline_stage, filtered to a specific candidate when
    candidate_psmiles is provided.

    Args:
        candidate_psmiles: Filter to this PSMILES only. Empty = return all records.
        run_dir: Session directory.
    """
    from biologix_ai.services.pipeline_audit import get_pipeline_audit as _get

    session = _optional_session_dir(run_dir) or session_dir_from_env(Path(ROOT))
    if session is None:
        return json.dumps([])

    def _run() -> Dict[str, Any]:
        records = _get(session_dir=Path(session), candidate_psmiles=candidate_psmiles)
        return {"ok": True, "records": records}

    payload = run_instant_mcp_tool(
        "get_pipeline_audit",
        session,
        _run,
        stage="pipeline_audit_read",
    )
    if payload.get("ok") and "records" in payload:
        return json.dumps(payload["records"], indent=2, default=str)
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def get_retrosynthesis_templates() -> str:
    """List recognised polymerisation types accepted by plan_retrosynthesis.

    HTTP API parity: GET /api/retrosynthesis/templates.
    """
    from biologix_ai.retrosynthesis.models import PolymerizationType

    def _run() -> Dict[str, Any]:
        return {
            "ok": True,
            "polymerization_types": [t.value for t in PolymerizationType],
            "note": "Template catalog is extensible via rxnutils.",
        }

    payload = run_instant_mcp_tool(
        "get_retrosynthesis_templates",
        None,
        _run,
        stage="catalog",
    )
    if payload.get("ok"):
        return json.dumps(
            {
                "polymerization_types": payload["polymerization_types"],
                "note": payload["note"],
            },
            indent=2,
        )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def get_personas() -> str:
    """List all expert persona presets with scoring weight vectors.

    HTTP API parity: GET /api/personas.
    """
    from biologix_ai.persona_presets import PERSONAS

    def _run() -> Dict[str, Any]:
        return {"ok": True, "personas": [p.model_dump() for p in PERSONAS]}

    payload = run_instant_mcp_tool("get_personas", None, _run, stage="catalog")
    if payload.get("ok") and "personas" in payload:
        return json.dumps(payload["personas"], indent=2, default=str)
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def get_persona(persona_id: str) -> str:
    """Return one persona preset by id.

    HTTP API parity: GET /api/personas/{persona_id}.
    """
    from biologix_ai.persona_presets import PERSONA_MAP

    pid = persona_id.strip()

    def _run() -> Dict[str, Any]:
        persona = PERSONA_MAP.get(pid)
        if persona is None:
            return {
                "ok": False,
                "error": f"Persona '{persona_id}' not found.",
                "available": list(PERSONA_MAP),
            }
        return {"ok": True, "persona": persona.model_dump()}

    payload = run_instant_mcp_tool("get_persona", None, _run, stage="catalog")
    if payload.get("ok") and "persona" in payload:
        return json.dumps(payload["persona"], indent=2, default=str)
    return json.dumps(payload, indent=2, default=str)


if __name__ == "__main__":
    install_stdio_guards(mcp)
    mcp.run()
