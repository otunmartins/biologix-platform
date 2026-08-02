#!/usr/bin/env python3

import argparse
import json
import tempfile
from pathlib import Path

from biologix_ai.retrosynthesis.aizynth_config import models_ready
from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
from biologix_ai.retrosynthesis.retro_adapter import normalize_extractions, write_llm_res
from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify polymer and monomer retrosynthesis")
    parser.add_argument("--require-models", action="store_true")
    args = parser.parse_args()

    if args.require_models and not models_ready():
        print(json.dumps({"ok": False, "error": "AiZynthFinder model assets are incomplete"}))
        return 1

    material_name = "poly(N-hydroxyethyl acrylamide)"
    target_psmiles = "[*]CC([*])C(=O)NCCO"
    extraction = normalize_extractions(
        {
            "monomer synthesis verification": (
                "Reaction 001:\n"
                "Reactants: acryloyl chloride (C=CC(=O)Cl), ethanolamine (NCCO)\n"
                "Products: N-hydroxyethyl acrylamide (C=CC(=O)NCCO), HCl\n"
                "Conditions: Schotten-Baumann reaction, 0 to 5 C"
            ),
            "polymerization verification": (
                "Reaction 002:\n"
                "Reactants: N-hydroxyethyl acrylamide (C=CC(=O)NCCO)\n"
                f"Products: {material_name} {target_psmiles}\n"
                "Conditions: RAFT polymerization, 60 C"
            ),
        }
    )

    with tempfile.TemporaryDirectory(prefix="biologix-retro-") as workspace:
        session_dir = Path(workspace)
        write_llm_res(
            session_dir,
            material_name,
            extraction,
            target_psmiles=target_psmiles,
        )
        result = plan_retrosynthesis(
            RetrosynthesisRequest(
                target=material_name,
                biologic_target="insulin",
                session_dir=str(session_dir),
                constraints=RetrosynthesisConstraints(
                    max_routes=1,
                    enrich_monomers_with_aizynth=True,
                ),
            )
        )

    metadata = result.metadata
    payload = {
        "ok": bool(result.polymer_routes) and not result.errors,
        "route_provenance": metadata.get("route_provenance"),
        "polymer_routes": len(result.polymer_routes),
        "aizynthfinder_models_ready": metadata.get("aizynthfinder_models_ready"),
        "aizynth_monomers_attempted": metadata.get("aizynth_monomers_attempted"),
        "aizynth_monomers_solved": metadata.get("aizynth_monomers_solved"),
        "warnings": result.warnings,
        "errors": result.errors,
    }
    if args.require_models:
        payload["ok"] = bool(
            payload["ok"]
            and payload["aizynthfinder_models_ready"]
            and payload["aizynth_monomers_attempted"]
            and payload["aizynth_monomers_solved"]
        )
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
