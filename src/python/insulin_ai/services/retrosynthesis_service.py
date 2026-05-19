"""RetrosynthesisService: orchestrates polymer and monomer retrosynthesis.

Two engines:
1. RetroSynthesisAgent (extern/) for macromolecular / polymer routes
2. AiZynthFinder for small-molecule monomer routes to purchasable building blocks
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional, Set

from insulin_ai.retrosynthesis.models import (
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
from insulin_ai.retrosynthesis.psmiles_bridge import name_to_target, psmiles_to_smiles_target

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


def _check_purchasability(smiles: str) -> MonomerSource:
    """Check if a monomer SMILES is in known purchasable stocks."""
    try:
        from insulin_ai.material_mappings import prescreen_psmiles_for_md
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

    try:
        from aizynthfinder.aizynthfinder import AiZynthFinder

        finder = AiZynthFinder()
        finder.stock.load_stock("zinc", "zinc")
        finder.expansion_policy.load_policy("uspto", "uspto")
        finder.filter_policy.load_filter("uspto", "uspto")
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
                    c["smiles"] for c in node.get("children", [])
                    if c.get("type") == "mol"
                ]
                steps.append(SmallMolStep(
                    reaction_smarts=node.get("smiles", ""),
                    reactants=child_smiles,
                    product=node.get("smiles", ""),
                ))
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


def _run_retrosynthesis_agent(
    material_name: str,
    num_results: int = 5,
) -> List[PolymerRoute]:
    """Run RetroSynthesisAgent for macromolecular retrosynthesis.

    Wraps the vendored RetroSynAgent package programmatically instead
    of using its CLI entry point.
    """
    if not _is_retrosynthesisagent_available():
        logger.warning("RetroSynthesisAgent not available; returning empty routes")
        return []

    try:
        from RetroSynAgent.treeBuilder import Tree
        from RetroSynAgent.pdfProcessor import PDFProcessor
        from RetroSynAgent.pdfDownloader import PDFDownloader
        from RetroSynAgent.entityAlignment import EntityAlignment

        with tempfile.TemporaryDirectory() as tmpdir:
            prev_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                pdf_folder = os.path.join(tmpdir, "pdfs")
                result_folder = os.path.join(tmpdir, "results")
                tree_folder = os.path.join(tmpdir, "trees")
                os.makedirs(pdf_folder, exist_ok=True)
                os.makedirs(result_folder, exist_ok=True)
                os.makedirs(tree_folder, exist_ok=True)

                downloader = PDFDownloader(
                    material_name,
                    pdf_folder_name=pdf_folder,
                    num_results=num_results,
                    n_thread=2,
                )
                pdf_name_list = downloader.main()
                logger.info("Downloaded %d PDFs for %s", len(pdf_name_list), material_name)

                processor = PDFProcessor(
                    pdf_folder_name=pdf_folder,
                    result_folder_name=result_folder,
                    result_json_name="llm_res",
                )
                processor.load_existing_results()
                processor.process_pdfs_txt(save_batch_size=2)

                ea = EntityAlignment()
                results_dict = ea.alignRootNode(result_folder, "llm_res", material_name)

                tree = Tree(material_name.lower(), result_dict=results_dict)
                tree.construct_tree()

                all_paths = tree.find_all_paths()

                routes: List[PolymerRoute] = []
                for i, path in enumerate(all_paths[:5]):
                    steps: List[PolymerRetroStep] = []
                    all_products: Set[str] = set()
                    all_reactants: Set[str] = set()

                    for rxn_idx in path:
                        rxn = tree.reactions[rxn_idx]
                        reactants = list(rxn["reactants"]) if isinstance(rxn["reactants"], tuple) else list(rxn["reactants"])
                        products = list(rxn["products"]) if isinstance(rxn["products"], tuple) else list(rxn["products"])
                        all_products.update(products)
                        all_reactants.update(reactants)
                        steps.append(PolymerRetroStep(
                            reactant_names=reactants,
                            product_name=products[0] if products else "",
                            conditions=rxn.get("conditions"),
                            literature_source=rxn.get("source"),
                        ))

                    leaf_names = all_reactants - all_products
                    monomers: List[MonomerInfo] = []
                    for name in sorted(leaf_names):
                        smiles = name
                        try:
                            smiles = tree.db.get_smiles_cached(name) or name
                        except Exception:
                            pass
                        monomers.append(MonomerInfo(
                            smiles=smiles,
                            name=name,
                            source=_check_purchasability(smiles),
                        ))

                    routes.append(PolymerRoute(
                        target_polymer=material_name,
                        steps=steps,
                        monomers=monomers,
                        pathway_score=1.0 / (i + 1),
                        recommended=(i == 0),
                    ))

                return routes
            finally:
                os.chdir(prev_cwd)

    except Exception as exc:
        logger.error("RetroSynthesisAgent failed for %s: %s", material_name, exc)
        return []


def plan_retrosynthesis(request: RetrosynthesisRequest) -> RetrosynthesisResult:
    """Main entry point: plan retrosynthetic routes for a target polymer.

    1. Resolve target to a usable form
    2. Run RetroSynthesisAgent for polymer-level routes
    3. For each identified monomer, check purchasability or run AiZynthFinder
    4. Return combined results
    """
    errors: List[str] = []
    warnings: List[str] = []
    constraints = request.constraints or RetrosynthesisConstraints()

    target = request.target.strip()
    target_smiles = None

    if "[*]" in target:
        target_smiles = psmiles_to_smiles_target(target)
    else:
        resolved = name_to_target(target)
        if resolved:
            target_smiles = psmiles_to_smiles_target(resolved)
        else:
            target_smiles = target

    material_name = target

    logger.info(
        "Planning retrosynthesis for target=%r, biologic_target=%r",
        material_name,
        request.biologic_target,
    )

    polymer_routes = _run_retrosynthesis_agent(
        material_name=material_name,
        num_results=constraints.max_routes,
    )

    if not polymer_routes:
        warnings.append(
            "RetroSynthesisAgent returned no routes; "
            "ensure extern/RetroSynthesisAgent is installed and LLM API is configured"
        )
        polymer_routes.append(PolymerRoute(
            target_polymer=material_name,
            monomers=[MonomerInfo(smiles=target_smiles or target, name=target)],
            pathway_score=0.0,
        ))

    if constraints.allowed_mechanisms:
        before = len(polymer_routes)
        polymer_routes = [
            r for r in polymer_routes
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
            r for r in polymer_routes
            if len(r.steps) <= constraints.max_steps
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
            r for r in polymer_routes
            if not any(m.smiles in banned for m in r.monomers)
        ]
        if len(polymer_routes) < before:
            warnings.append(
                f"Filtered {before - len(polymer_routes)} routes containing "
                f"banned reagents"
            )

    for route in polymer_routes:
        for monomer in route.monomers:
            if monomer.source == MonomerSource.UNKNOWN:
                monomer.source = _check_purchasability(monomer.smiles)

            if (
                monomer.source != MonomerSource.PURCHASABLE
                and not constraints.require_purchasable_monomers
            ):
                sm_route = _run_aizynthfinder(monomer.smiles)
                if sm_route and sm_route.is_solved:
                    monomer.synthesis_route = sm_route
                    monomer.source = MonomerSource.NEEDS_SYNTHESIS
                elif sm_route:
                    warnings.append(
                        f"AiZynthFinder could not solve route for monomer: {monomer.smiles}"
                    )

    meta = {
        "retrosynthesis_agent_available": _is_retrosynthesisagent_available(),
        "aizynthfinder_available": _is_aizynthfinder_available(),
        "target_smiles": target_smiles,
        "biologic_target": request.biologic_target,
    }
    if request.biologic_pdb_path:
        meta["biologic_pdb_path"] = request.biologic_pdb_path

    return RetrosynthesisResult(
        request=request,
        polymer_routes=polymer_routes[:constraints.max_routes],
        errors=errors,
        warnings=warnings,
        metadata=meta,
    )
