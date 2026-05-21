"""RetrosynthesisService: orchestrates polymer and monomer retrosynthesis.

Two engines:
1. RetroSynthesisAgent (extern/) for macromolecular / polymer routes
2. AiZynthFinder for small-molecule monomer routes to purchasable building blocks
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Set, Tuple

from biologix_ai.retrosynthesis.aizynth_config import get_configfile, models_ready
from biologix_ai.retrosynthesis.models import (
    MonomerInfo,
    MonomerSource,
    PolymerRoute,
    PolymerRetroStep,
    PolymerizationType,
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
    RetrosynthesisResult,
    SmallMolRoute,
    SmallMolStep,
)
from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target
from biologix_ai.retrosynthesis.retro_adapter import session_has_extractions
from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace

logger = logging.getLogger(__name__)


def _is_aizynthfinder_available() -> bool:
    try:
        import aizynthfinder  # noqa: F401
        return True
    except ImportError:
        return False


def _is_retrosynthesisagent_available() -> bool:
    try:
        from RetroSynAgent.treeBuilder import Tree  # noqa: F401
        return True
    except ImportError:
        return False


def _is_runnable_smiles_for_aizynth(smiles: str) -> bool:
    if not smiles or "[*]" in smiles:
        return False
    try:
        from rdkit import Chem

        return Chem.MolFromSmiles(smiles) is not None
    except ImportError:
        return bool(smiles) and len(smiles) < 200


def _check_purchasability(smiles: str) -> MonomerSource:
    """Check if a monomer SMILES is in known purchasable stocks."""
    try:
        from biologix_ai.material_mappings import prescreen_psmiles_for_md

        result = prescreen_psmiles_for_md(smiles)
        if result and result.get("ok"):
            return MonomerSource.PURCHASABLE
    except (ImportError, Exception):
        pass
    try:
        from RetroSynAgent.treeBuilder import CommonSubstanceDB

        db = CommonSubstanceDB()
        if smiles.lower() in db.added_database:
            return MonomerSource.PURCHASABLE
    except (ImportError, Exception):
        pass
    return MonomerSource.UNKNOWN


def _run_aizynthfinder(target_smiles: str) -> Optional[SmallMolRoute]:
    """Run AiZynthFinder on a single monomer target."""
    if not _is_aizynthfinder_available():
        logger.warning("AiZynthFinder not installed; skipping monomer route planning")
        return None

    configfile = get_configfile()
    if not configfile:
        logger.warning(
            "AiZynthFinder models not found; run scripts/setup_aizynthfinder.sh"
        )
        return None

    try:
        from aizynthfinder.aizynthfinder import AiZynthFinder

        finder = AiZynthFinder(configfile=configfile)
        finder.stock.select("zinc")
        finder.expansion_policy.select("uspto")
        try:
            finder.filter_policy.select("uspto")
        except Exception:
            pass
        finder.target_smiles = target_smiles
        finder.tree_search()
        finder.build_routes()

        if not len(finder.routes):
            return SmallMolRoute(target_smiles=target_smiles, is_solved=False)

        top_tree = finder.routes.reaction_trees[0]
        route_dict = top_tree.to_dict()

        steps: List[SmallMolStep] = []
        building_blocks: List[str] = []

        def _walk_route(node: dict) -> None:
            if node.get("type") == "reaction":
                child_smiles = [
                    c["smiles"]
                    for c in node.get("children", [])
                    if c.get("type") == "mol"
                ]
                steps.append(
                    SmallMolStep(
                        reaction_smarts=node.get("smiles", ""),
                        reactants=child_smiles,
                        product=node.get("smiles", ""),
                    )
                )
            elif node.get("type") == "mol" and node.get("in_stock"):
                building_blocks.append(node["smiles"])
            for child in node.get("children", []):
                _walk_route(child)

        _walk_route(route_dict)

        score = 0.0
        if finder.routes.scores:
            first_scores = finder.routes.scores[0]
            if isinstance(first_scores, dict) and first_scores:
                score = list(first_scores.values())[0]
            elif isinstance(first_scores, (int, float)):
                score = float(first_scores)

        return SmallMolRoute(
            target_smiles=target_smiles,
            steps=steps,
            score=score,
            is_solved=top_tree.is_solved,
            building_blocks=list(set(building_blocks)),
        )
    except Exception as exc:
        logger.error("AiZynthFinder failed for %s: %s", target_smiles, exc)
        return None


def _routes_from_tree(
    tree: object,
    material_name: str,
) -> List[PolymerRoute]:
    all_paths = tree.find_all_paths()  # type: ignore[attr-defined]
    routes: List[PolymerRoute] = []
    for i, path in enumerate(all_paths[:5]):
        steps: List[PolymerRetroStep] = []
        all_products: Set[str] = set()
        all_reactants: Set[str] = set()

        for rxn_idx in path:
            rxn = tree.reactions[rxn_idx]  # type: ignore[attr-defined]
            reactants = (
                list(rxn["reactants"])
                if isinstance(rxn["reactants"], tuple)
                else list(rxn["reactants"])
            )
            products = (
                list(rxn["products"])
                if isinstance(rxn["products"], tuple)
                else list(rxn["products"])
            )
            all_products.update(products)
            all_reactants.update(reactants)
            steps.append(
                PolymerRetroStep(
                    reactant_names=reactants,
                    product_name=products[0] if products else "",
                    conditions=rxn.get("conditions"),
                    literature_source=rxn.get("source"),
                )
            )

        leaf_names = all_reactants - all_products
        monomers: List[MonomerInfo] = []
        for name in sorted(leaf_names):
            smiles = name
            try:
                smiles = tree.db.get_smiles_cached(name) or name  # type: ignore[attr-defined]
            except Exception:
                pass
            monomers.append(
                MonomerInfo(
                    smiles=smiles,
                    name=name,
                    source=_check_purchasability(smiles),
                )
            )

        routes.append(
            PolymerRoute(
                target_polymer=material_name,
                steps=steps,
                monomers=monomers,
                pathway_score=1.0 / (i + 1),
                recommended=(i == 0),
            )
        )
    return routes


def _run_retrosynthesis_agent(
    material_name: str,
    num_results: int = 5,
    session_dir: Optional[Path] = None,
) -> Tuple[List[PolymerRoute], str]:
    """Run RetroSynthesisAgent for macromolecular retrosynthesis.

    Returns (routes, route_provenance) where provenance is session_agent_llm or none.
    """
    if not _is_retrosynthesisagent_available():
        logger.warning("RetroSynthesisAgent not available; returning empty routes")
        return [], "none"

    cleanup_tmp = False
    if session_dir is not None:
        dirs = ensure_workspace(session_dir, material_name)
        ws = dirs["workspace"]
        pdf_folder = dirs["pdfs"]
        result_folder = dirs["results"]
        tree_folder = dirs["trees"]
        had_session_extractions = session_has_extractions(session_dir, material_name)
    else:
        ws = Path(tempfile.mkdtemp())
        cleanup_tmp = True
        pdf_folder = ws / "pdfs"
        result_folder = ws / "results"
        tree_folder = ws / "trees"
        for d in (pdf_folder, result_folder, tree_folder):
            d.mkdir(parents=True, exist_ok=True)
        had_session_extractions = False

    provenance = "none"

    try:
        from RetroSynAgent.entityAlignment import EntityAlignment
        from RetroSynAgent.pdfProcessor import PDFProcessor
        from RetroSynAgent.treeBuilder import Tree

        prev_cwd = os.getcwd()
        try:
            os.chdir(ws)

            processor = PDFProcessor(
                pdf_folder_name=str(pdf_folder),
                result_folder_name=str(result_folder),
                result_json_name="llm_res",
            )
            processor.load_existing_results()

            if processor.result_dict:
                provenance = "session_agent_llm"
                logger.info(
                    "Using %d existing llm_res entries for %s",
                    len(processor.result_dict),
                    material_name,
                )
            else:
                logger.warning(
                    "No llm_res in session workspace; returning empty routes "
                    "(call submit_retro_extractions before plan_retrosynthesis)"
                )
                return [], "none"

            ea = EntityAlignment()
            results_dict = ea.alignRootNode(
                str(result_folder), "llm_res", material_name
            )

            tree_root = material_name.strip().lower()
            tree = Tree(tree_root, result_dict=results_dict)
            tree.construct_tree()
            routes = _routes_from_tree(tree, material_name)
            if routes and provenance == "none":
                provenance = "session_agent_llm"
            if processor.result_dict and not routes:
                logger.warning(
                    "RetroSynAgent tree produced no paths for %s; "
                    "check that extraction Products include %r",
                    material_name,
                    tree_root,
                )
            return routes, provenance
        finally:
            os.chdir(prev_cwd)
    except Exception as exc:
        logger.error("RetroSynthesisAgent failed for %s: %s", material_name, exc)
        return [], "none"
    finally:
        if cleanup_tmp:
            import shutil

            try:
                shutil.rmtree(ws, ignore_errors=True)
            except Exception:
                pass


def prepare_retrosynthesis_workspace(
    target: str,
    session_dir: Path,
    max_pdfs: int = 5,
) -> dict:
    """Download PDFs and return workspace metadata for agent extraction."""
    resolved = resolve_retro_target(target)
    material_name = resolved["material_name"]
    dirs = ensure_workspace(session_dir, material_name)
    pdf_paths: List[str] = []

    if _is_retrosynthesisagent_available():
        try:
            from RetroSynAgent.pdfDownloader import PDFDownloader

            prev_cwd = os.getcwd()
            try:
                os.chdir(dirs["workspace"])
                downloader = PDFDownloader(
                    material_name,
                    pdf_folder_name=str(dirs["pdfs"]),
                    num_results=max_pdfs,
                    n_thread=2,
                )
                pdf_names = downloader.main()
                pdf_paths = [str(dirs["pdfs"] / f"{n}.pdf") for n in pdf_names]
            finally:
                os.chdir(prev_cwd)
        except Exception as exc:
            logger.warning("PDF download failed for %s: %s", material_name, exc)

    from biologix_ai.retrosynthesis.retro_workspace import EXTRACTION_SCHEMA

    return {
        "material_name": material_name,
        "psmiles": resolved["psmiles"] or target,
        "monomer_smiles": resolved["monomer_smiles"],
        "session_workspace": str(dirs["workspace"]),
        "pdf_paths": pdf_paths,
        "pdf_count": len(pdf_paths),
        "extraction_schema": EXTRACTION_SCHEMA,
        "session_extractions_present": session_has_extractions(session_dir, material_name),
        "next_step": (
            "Read PDFs or session literature, extract reactions, then call "
            "submit_retro_extractions(run_dir=..., material_name=..., extractions=<JSON>)"
        ),
    }


def load_cached_plan_result(
    session_dir: Path,
    target: str,
) -> Optional[RetrosynthesisResult]:
    """Load RetrosynthesisResult from newest matching plan_*.json in session."""
    from biologix_ai.retrosynthesis.retro_report import load_cached_plan_artifact

    wrapper = load_cached_plan_artifact(session_dir, target)
    if not wrapper or "result" not in wrapper:
        return None
    return RetrosynthesisResult.model_validate(wrapper["result"])


def plan_retrosynthesis(request: RetrosynthesisRequest) -> RetrosynthesisResult:
    """Main entry point: plan retrosynthetic routes for a target polymer."""
    errors: List[str] = []
    warnings: List[str] = []
    constraints = request.constraints or RetrosynthesisConstraints()

    target = request.target.strip()
    resolved = resolve_retro_target(target)
    material_name = resolved["material_name"]
    target_psmiles = resolved["psmiles"] or (target if "[*]" in target else "")
    target_smiles = resolved["monomer_smiles"] or None

    session_dir: Optional[Path] = None
    if request.session_dir:
        session_dir = Path(request.session_dir).expanduser().resolve()

    logger.info(
        "Planning retrosynthesis for target=%r material_name=%r biologic_target=%r",
        target,
        material_name,
        request.biologic_target,
    )

    polymer_routes, route_provenance = _run_retrosynthesis_agent(
        material_name=material_name,
        num_results=constraints.max_routes,
        session_dir=session_dir,
    )

    retro_stages: List[str] = []
    if route_provenance == "session_agent_llm":
        retro_stages.append("session_extractions")
        retro_stages.append("kg_tree")
    kg_empty_after_extractions = False
    if (
        not polymer_routes
        and session_dir
        and session_has_extractions(session_dir, material_name)
    ):
        kg_empty_after_extractions = True
        warnings.append(
            f"Session extractions exist for {material_name!r} but RetroSynAgent "
            f"built no routes. Ensure reaction Products include the polymer name "
            f"({material_name!r}, case-insensitive) and reactants chain to purchasable "
            "precursors. Re-submit via submit_retro_extractions if needed."
        )

    requires_agent_extractions = False
    if not polymer_routes:
        requires_agent_extractions = True
        route_provenance = "none"
        warnings.append(
            "No polymer routes found. Call prepare_retrosynthesis, then "
            "submit_retro_extractions (Products must include material_name), "
            "then plan_retrosynthesis again."
        )

    if constraints.allowed_mechanisms:
        before = len(polymer_routes)
        polymer_routes = [
            r
            for r in polymer_routes
            if r.polymerization_type in constraints.allowed_mechanisms
        ]
        if len(polymer_routes) < before:
            warnings.append(
                f"Filtered {before - len(polymer_routes)} routes not matching "
                f"allowed mechanisms: {[m.value for m in constraints.allowed_mechanisms]}"
            )

    if constraints.max_steps:
        before = len(polymer_routes)
        polymer_routes = [
            r for r in polymer_routes if len(r.steps) <= constraints.max_steps
        ]
        if len(polymer_routes) < before:
            warnings.append(
                f"Filtered {before - len(polymer_routes)} routes exceeding "
                f"max_steps={constraints.max_steps}"
            )

    if constraints.banned_reagents:
        banned = set(s.strip() for s in constraints.banned_reagents)
        before = len(polymer_routes)
        polymer_routes = [
            r
            for r in polymer_routes
            if not any(m.smiles in banned for m in r.monomers)
        ]
        if len(polymer_routes) < before:
            warnings.append(
                f"Filtered {before - len(polymer_routes)} routes containing banned reagents"
            )

    aizynth_pkg = _is_aizynthfinder_available()
    aizynth_models = models_ready() if aizynth_pkg else False
    aizynth_attempted = 0
    aizynth_solved = 0

    for route in polymer_routes:
        for monomer in route.monomers:
            if monomer.source == MonomerSource.UNKNOWN:
                monomer.source = _check_purchasability(monomer.smiles)

            run_aizynth = (
                aizynth_models
                and not constraints.require_purchasable_monomers
                and _is_runnable_smiles_for_aizynth(monomer.smiles)
                and monomer.synthesis_route is None
                and (
                    constraints.enrich_monomers_with_aizynth
                    or monomer.source != MonomerSource.PURCHASABLE
                )
            )
            if run_aizynth:
                aizynth_attempted += 1
                sm_route = _run_aizynthfinder(monomer.smiles)
                if sm_route and sm_route.is_solved:
                    monomer.synthesis_route = sm_route
                    if monomer.source == MonomerSource.UNKNOWN:
                        monomer.source = MonomerSource.NEEDS_SYNTHESIS
                    aizynth_solved += 1
                    if "aizynth_monomer" not in retro_stages:
                        retro_stages.append("aizynth_monomer")
                elif sm_route:
                    warnings.append(
                        f"AiZynthFinder could not solve route for monomer: {monomer.smiles}"
                    )
                elif aizynth_models:
                    warnings.append(
                        f"AiZynthFinder returned no route for monomer: {monomer.smiles}"
                    )

    meta = {
        "retrosynthesis_agent_available": _is_retrosynthesisagent_available(),
        "aizynthfinder_available": aizynth_pkg,
        "aizynthfinder_models_ready": aizynth_models,
        "route_provenance": route_provenance,
        "retro_stages_completed": retro_stages,
        "material_name_resolved": material_name,
        "target_psmiles": target_psmiles,
        "target_smiles": target_smiles,
        "biologic_target": request.biologic_target,
        "requires_agent_extractions": requires_agent_extractions,
        "session_extractions_present": (
            session_has_extractions(session_dir, material_name)
            if session_dir
            else False
        ),
        "retro_workspace_ready": session_dir is not None,
        "kg_empty_after_session_extractions": kg_empty_after_extractions,
        "aizynth_monomers_attempted": aizynth_attempted,
        "aizynth_monomers_solved": aizynth_solved,
    }
    if not aizynth_models and aizynth_pkg:
        meta["aizynth_setup_hint"] = "Run: bash scripts/setup_aizynthfinder.sh"
    elif aizynth_models and aizynth_attempted == 0 and polymer_routes:
        meta["aizynth_skip_reason"] = (
            "No eligible monomer SMILES, enrich_monomers_with_aizynth=false, "
            "or all monomers marked non-runnable"
        )
    if request.biologic_pdb_path:
        meta["biologic_pdb_path"] = request.biologic_pdb_path

    prov = route_provenance
    if prov == "session_agent_llm":
        meta["reporting_honesty"] = (
            "Provenance: session_agent_llm (literature-derived RetroSyn KG routes)."
        )
    else:
        meta["reporting_honesty"] = f"Provenance: {prov}; no polymer routes."
        if prov == "none" and session_dir and session_has_extractions(session_dir, material_name):
            meta["recommended_next_action"] = (
                f"Re-submit extractions with Products containing "
                f"{material_name.strip().lower()!r}, then plan_retrosynthesis again."
            )

    return RetrosynthesisResult(
        request=request,
        polymer_routes=polymer_routes[: constraints.max_routes],
        errors=errors,
        warnings=warnings,
        metadata=meta,
    )
