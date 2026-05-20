"""Scripted biologics retrosynthesis campaign (session folder, summary JSON)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from biologix_ai.run_paths import ENV_SESSION


def run_biologics_discovery_loop(
    biologic_target: str,
    polymer_target: str,
    session_dir: Path,
    root: str,
    budget_minutes: float = 60.0,
    max_routes: int = 5,
    run_admet: bool = True,
    run_openmm: bool = False,
) -> Dict[str, Any]:
    """Resolve biologic PDB, plan retrosynthesis per polymer candidate, compile, optionally OpenMM."""
    session_dir = Path(session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session_dir)
    repo_root = Path(root).resolve()

    errors: List[str] = []
    from biologix_ai.services import biologic_resolver as bio_res
    from biologix_ai.discovery_world import ensure_world_for_session
    from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
    from biologix_ai.services.biologics_session import write_retrosynthesis_artifact
    from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis as plan_retro_svc
    from biologix_ai.services.results_compiler import compile_results as compile_svc
    from biologix_ai.services.toxicity_service import screen_monomer

    bio = bio_res.resolve_biologic_target(
        biologic_target,
        repo_root,
        session_dir=session_dir,
        fetch_pdb=True,
    )
    if bio.errors and not bio.fetch_ok:
        errors.extend(bio.errors)
    if bio.pdb_path and bio.fetch_ok:
        os.environ["BIOLOGIX_AI_TARGET_PROTEIN_PDB"] = bio.pdb_path
    else:
        os.environ.pop("BIOLOGIX_AI_TARGET_PROTEIN_PDB", None)

    ensure_world_for_session(
        session_dir,
        objective=f"Biologics stabilisation: {biologic_target}",
    )

    candidates: List[str] = []
    if (polymer_target or "").strip():
        candidates.append(polymer_target.strip())
    else:
        from biologix_ai.services.designer_service import generate_candidates

        gen = generate_candidates(
            "polymer excipient for biologic stabilisation formulation",
            biologic_target=biologic_target,
            library_size=6,
        )
        for c in gen.get("candidates", []):
            if isinstance(c, dict):
                p = c.get("psmiles") or c.get("chemical_structure")
                if p and "[*]" in str(p):
                    candidates.append(str(p))
        if not candidates:
            candidates.append("[*]OCC[*]")

    deadline = time.monotonic() + budget_minutes * 60.0
    iterations_out: List[Dict[str, Any]] = []
    pdb_e = (bio.pdb_path or "").strip() or None

    for tgt in candidates:
        if time.monotonic() > deadline:
            break
        request = RetrosynthesisRequest(
            target=tgt,
            biologic_target=biologic_target,
            biologic_pdb_path=pdb_e,
            constraints=RetrosynthesisConstraints(max_routes=max_routes),
        )
        try:
            retro = plan_retro_svc(request)
        except Exception as exc:
            errors.append(f"plan_retrosynthesis {tgt}: {exc}")
            continue

        tox_results = {}
        if run_admet:
            seen_smiles = set()
            for route in retro.polymer_routes:
                for monomer in route.monomers:
                    if monomer.smiles not in seen_smiles:
                        seen_smiles.add(monomer.smiles)
                        try:
                            tox_results[monomer.smiles] = screen_monomer(monomer.smiles)
                        except Exception as exc:
                            errors.append(f"admet {monomer.smiles}: {exc}")

        try:
            report = compile_svc(retro, tox_results=tox_results or None)
        except Exception as exc:
            errors.append(f"compile {tgt}: {exc}")
            continue

        iterations_out.append(
            {
                "target": tgt,
                "n_routes": len(retro.polymer_routes),
                "n_scorecards": len(report.scorecards),
                "top_score": report.scorecards[0].composite_score if report.scorecards else None,
            }
        )
        try:
            write_retrosynthesis_artifact(
                session_dir,
                f"loop_{int(time.time())}_{abs(hash(tgt)) % 10000}.json",
                {
                    "retro": retro.model_dump(),
                    "report": report.model_dump(),
                },
            )
        except OSError as exc:
            errors.append(f"persist {tgt}: {exc}")

    if run_openmm and candidates:
        try:
            from biologix_ai.simulation.md_simulator import MDSimulator

            _os_max = os.environ.get("BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS", "")
            if not _os_max:
                os.environ["BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS"] = "2000"
            sim = MDSimulator()
            tgt = candidates[0]
            sim.evaluate_candidates(
                [{"psmiles": tgt, "chemical_structure": tgt, "material_name": tgt}],
                max_candidates=1,
                verbose=False,
            )
        except Exception as exc:
            errors.append(f"openmm: {exc}")

    summary: Dict[str, Any] = {
        "session_dir": str(session_dir),
        "biologic_resolution": bio.model_dump(),
        "candidates": candidates,
        "iterations": iterations_out,
        "errors": errors,
    }
    out_path = session_dir / "biologics_discovery_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return summary
