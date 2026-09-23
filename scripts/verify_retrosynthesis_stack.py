#!/usr/bin/env python3
"""Exercise RetroSynAgent and AiZynthFinder together using baked image assets."""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from biologix_ai.retrosynthesis.models import (
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
)
from biologix_ai.retrosynthesis.retro_adapter import write_llm_res
from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace
from biologix_ai.retrosynthesis.retrosyn_bootstrap import ensure_retrosyn_agent_ready
from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis


TARGET_PSMILES = "[*]CC([*])C(=O)NCCO"
MATERIAL_NAME = "poly(N-hydroxyethyl acrylamide)"


class RetrosynthesisStackError(RuntimeError):
    """Raised when the integrated retrosynthesis smoke path is incomplete."""


@dataclass(frozen=True)
class RetrosynthesisStackReport:
    """Integrated route counts from RetroSynAgent and AiZynthFinder."""

    polymer_routes: int
    aizynth_monomers_attempted: int
    aizynth_monomers_solved: int


def prime_offline_tree_cache(
    workspace: Path,
    root_name: str,
    monomer_name: str,
    monomer_smiles: str,
) -> None:
    """Seed deterministic RetroSyn caches so the smoke test requires no PubChem."""
    workspace.mkdir(parents=True, exist_ok=True)
    root_key = root_name.strip().lower()
    monomer_key = monomer_name.strip().lower()
    (workspace / "substance_query_result.json").write_text(
        json.dumps(
            {
                root_key: False,
                monomer_key: True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "smiles_cache.json").write_text(
        json.dumps(
            {
                root_key: root_key,
                monomer_key: monomer_smiles,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def validate_retrosynthesis_result(
    payload: dict[str, Any],
) -> RetrosynthesisStackReport:
    """Require a provenance-backed polymer route and solved AiZynth monomer route."""
    failures: list[str] = []
    metadata = payload.get("metadata") or {}
    polymer_routes = payload.get("polymer_routes") or []

    for flag in (
        "retrosynthesis_agent_available",
        "aizynthfinder_available",
        "aizynthfinder_models_ready",
    ):
        if metadata.get(flag) is not True:
            failures.append(f"{flag} is not true")
    if metadata.get("route_provenance") != "session_agent_llm":
        failures.append(
            "route_provenance is not session_agent_llm: "
            f"{metadata.get('route_provenance')!r}"
        )
    if payload.get("errors"):
        failures.append(f"planner errors: {payload['errors']}")
    if not polymer_routes:
        failures.append("RetroSynAgent produced no polymer routes")

    attempted = int(metadata.get("aizynth_monomers_attempted") or 0)
    solved = int(metadata.get("aizynth_monomers_solved") or 0)
    if attempted < 1:
        failures.append("AiZynthFinder attempted no monomers")
    if solved < 1:
        failures.append("AiZynthFinder solved no monomers")

    solved_routes = 0
    for polymer_route in polymer_routes:
        for monomer in polymer_route.get("monomers") or []:
            synthesis_route = monomer.get("synthesis_route") or {}
            if synthesis_route.get("is_solved") is True:
                solved_routes += 1
    if solved_routes < 1:
        failures.append("No solved AiZynthFinder monomer route was attached")

    if failures:
        raise RetrosynthesisStackError(
            "Retrosynthesis stack smoke failed:\n- " + "\n- ".join(failures)
        )

    return RetrosynthesisStackReport(
        polymer_routes=len(polymer_routes),
        aizynth_monomers_attempted=attempted,
        aizynth_monomers_solved=solved,
    )


def verify_retrosynthesis_stack() -> RetrosynthesisStackReport:
    """Build one session KG route and enrich its monomer through AiZynthFinder."""
    ensure_retrosyn_agent_ready()
    extraction = {
        "modal_image_smoke_fixture": (
            "Reaction 001:\n"
            "Reactants: N-hydroxyethyl acrylamide (C=CC(=O)NCCO)\n"
            "Products: poly(N-hydroxyethyl acrylamide) [*]CC([*])C(=O)NCCO\n"
            "Conditions: RAFT, AIBN, 60 C"
        )
    }
    with tempfile.TemporaryDirectory(prefix="biologix-retro-smoke-") as temp_dir:
        session_dir = Path(temp_dir)
        write_llm_res(
            session_dir,
            MATERIAL_NAME,
            extraction,
            target_psmiles=TARGET_PSMILES,
        )
        workspace = ensure_workspace(session_dir, MATERIAL_NAME)["workspace"]
        prime_offline_tree_cache(
            workspace=workspace,
            root_name=MATERIAL_NAME,
            monomer_name="N-hydroxyethyl acrylamide",
            monomer_smiles="C=CC(=O)NCCO",
        )
        request = RetrosynthesisRequest(
            target=TARGET_PSMILES,
            biologic_target="insulin",
            session_dir=str(session_dir),
            constraints=RetrosynthesisConstraints(max_routes=1),
        )
        result = plan_retrosynthesis(request)
        return validate_retrosynthesis_result(result.model_dump())


def main() -> None:
    """Run the integrated smoke and print a machine-readable report."""
    with contextlib.redirect_stdout(sys.stderr):
        report = verify_retrosynthesis_stack()
    print(json.dumps({"ok": True, **asdict(report)}, sort_keys=True))


if __name__ == "__main__":
    main()
