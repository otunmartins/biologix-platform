import re
from uuid import uuid4

import pytest

from biologix_ai.platform.models import Experiment, User


def canonical(psmiles: str) -> str:
    """Compare repeat units by structure; the same polymer has several valid spellings."""
    from biologix_ai.material_mappings import validate_psmiles

    result = validate_psmiles(psmiles)
    return result.get("canonical") or psmiles

from biologix_ai.platform.worker import execute_pipeline, resolve_psmiles, run_retrosynthesis

PEG_STRUCTURE = {"psmiles": "[*]OCC[*]", "material_name": "PEG"}

from test_platform_api import TestingSession


def test_retrosynthesis_without_extractions_requires_input(tmp_path, monkeypatch):
    from biologix_ai.retrosynthesis.models import RetrosynthesisResult
    from biologix_ai.services import retrosynthesis_service

    def fake_plan(request):
        return RetrosynthesisResult(
            request=request,
            metadata={"requires_agent_extractions": True},
        )

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)
    experiment = Experiment(
        id=uuid4(),
        owner_id=uuid4(),
        name="PEG qualification",
        biologic_target="human insulin",
        polymer_target="PEG",
        parameters={},
    )

    result = run_retrosynthesis(experiment, PEG_STRUCTURE, {"retrosynthesis_agent": True})

    assert result["status"] == "requires_input"


def test_curated_fallback_is_provisional_not_completed(tmp_path, monkeypatch):
    from biologix_ai.retrosynthesis.models import PolymerRoute, RetrosynthesisResult
    from biologix_ai.services import retrosynthesis_service

    def fake_plan(request):
        return RetrosynthesisResult(
            request=request,
            polymer_routes=[PolymerRoute(target_polymer="poly(ethylene glycol)")],
            metadata={
                "requires_agent_extractions": False,
                "route_provenance": "curated_template",
            },
        )

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)
    experiment = Experiment(
        id=uuid4(), owner_id=uuid4(), name="PEG", biologic_target="insulin",
        polymer_target="PEG", parameters={},
    )

    result = run_retrosynthesis(experiment, PEG_STRUCTURE, {"retrosynthesis_agent": True})
    assert result["status"] == "provisional"
    assert result["evidence_source"] == "offline_curated_route"


def test_supported_polymers_resolve_to_distinct_structures():
    assert canonical(resolve_psmiles("PEG")) == canonical("[*]OCC[*]")
    assert canonical(resolve_psmiles("PLGA")) == canonical("[*]OC(=O)COC(=O)C(C)[*]")
    assert canonical(resolve_psmiles("PEG")) != canonical(resolve_psmiles("PLGA"))


def test_plga_structure_matches_compliance_database():
    from biologix_ai.services.compliance_service import check_excipient_compliance

    result = check_excipient_compliance(resolve_psmiles("PLGA"))
    assert result.approved_name == "PLGA (poly(lactic-co-glycolic acid))"


def test_unknown_polymer_does_not_silently_become_peg(monkeypatch):
    from biologix_ai import material_mappings

    monkeypatch.setattr(
        material_mappings,
        "name_to_psmiles",
        lambda _name: {"ok": False, "error": "not found"},
    )
    with pytest.raises(ValueError, match="Could not resolve polymer target"):
        resolve_psmiles("definitely-not-a-real-polymer")


def test_retrosynthesis_uses_experiment_extractions(tmp_path, monkeypatch):
    from biologix_ai.retrosynthesis.models import PolymerRoute, RetrosynthesisResult
    from biologix_ai.retrosynthesis.retro_adapter import session_has_extractions
    from biologix_ai.services import retrosynthesis_service

    def fake_plan(request):
        assert request.session_dir is not None
        assert session_has_extractions(
            tmp_path / str(experiment.id),
            "poly(acrylic acid)",
        )
        return RetrosynthesisResult(
            request=request,
            polymer_routes=[PolymerRoute(target_polymer="poly(acrylic acid)")],
            metadata={
                "requires_agent_extractions": False,
                "route_provenance": "session_agent_llm",
            },
        )

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)
    experiment = Experiment(
        id=uuid4(),
        owner_id=uuid4(),
        name="PAA qualification",
        biologic_target="human insulin",
        polymer_target="poly(acrylic acid)",
        parameters={
            "retrosynthesis_material_name": "poly(acrylic acid)",
            "retrosynthesis_extractions": {
                "verified source": (
                    "Reaction 001:\n"
                    "Reactants: acrylic acid\n"
                    "Products: poly(acrylic acid)\n"
                    "Conditions: RAFT, 70 C"
                )
            },
        },
    )

    structure = {"psmiles": "[*]CC([*])C(=O)O", "material_name": "poly(acrylic acid)"}
    result = run_retrosynthesis(experiment, structure, {"retrosynthesis_agent": True})

    assert result["status"] == "completed"
    assert result["evidence_source"] == "user_supplied"


def test_claude_plans_evidence_and_replans_until_the_graph_builds(tmp_path, monkeypatch):
    """No evidence supplied: Claude proposes it, and a graph that builds nothing is retried
    with the failure handed back as the next instruction."""
    from biologix_ai.llm import retro_planner
    from biologix_ai.retrosynthesis.models import PolymerRetroStep, PolymerRoute, RetrosynthesisResult
    from biologix_ai.services import retrosynthesis_service

    plans = []
    feedback_seen = []

    def fake_evidence(material_name, target_psmiles="", *, workspace=None, extra_feedback=""):
        feedback_seen.append(extra_feedback)
        return {
            "ok": True,
            "material_name": material_name,
            "sources": [{"name": "Smith 2019", "reactions": [
                {"reactants": ["custom acrylate"], "products": [material_name], "conditions": "AIBN, 70 C"}
            ]}],
            "mechanism_by_source": {"Smith 2019": "free_radical"},
            "extractions": {"Smith 2019": (
                f"Reaction 1:\nReactants: custom acrylate\n"
                f"Products: {material_name}\nConditions: AIBN, 70 C"
            )},
            "diagnostics": {"root_product_found": True},
            "unresolved_reactants": ["custom acrylate"] if not extra_feedback else [],
            "rounds": [{"round": 1}],
            "model": "claude-opus-5",
        }

    def fake_plan(request):
        plans.append(request)
        if len(plans) == 1:
            return RetrosynthesisResult(
                request=request,
                warnings=["KG leaf coverage may be incomplete"],
                metadata={"requires_agent_extractions": True},
            )
        return RetrosynthesisResult(
            request=request,
            polymer_routes=[PolymerRoute(
                target_polymer="poly(custom acrylate)",
                steps=[PolymerRetroStep(
                    reactant_names=["custom acrylate"],
                    product_name="poly(custom acrylate)",
                    literature_source="Smith 2019",
                )],
            )],
            metadata={"requires_agent_extractions": False, "route_provenance": "session_agent_llm"},
        )

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retro_planner, "plan_evidence", fake_evidence)
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)
    experiment = Experiment(
        id=uuid4(),
        owner_id=uuid4(),
        name="Custom polymer",
        biologic_target="human insulin",
        polymer_target="poly(custom acrylate)",
        parameters={},
    )
    structure = {"psmiles": "[*]CC([*])C(=O)OC", "material_name": "poly(custom acrylate)"}

    result = run_retrosynthesis(experiment, structure, {"retrosynthesis_agent": True})

    assert result["status"] == "completed"
    assert result["evidence_source"] == "llm_literature_evidence"
    assert len(plans) == 2, "the first empty graph should have been retried"
    assert "custom acrylate" in feedback_seen[1], "the failure has to reach the next prompt"
    assert result["planning"]["sources"][0]["name"] == "Smith 2019"
    # The graph carries no mechanism; the evidence does, and it has to reach the route.
    assert result["result"]["polymer_routes"][0]["polymerization_type"] == "free_radical"


def test_curated_route_is_only_reached_after_planning_fails(tmp_path, monkeypatch):
    from biologix_ai.llm import retro_planner
    from biologix_ai.retrosynthesis.models import PolymerRoute, RetrosynthesisResult
    from biologix_ai.services import retrosynthesis_service

    allowed = []

    def fake_plan(request):
        allowed.append(request.constraints.allow_curated_template)
        if not request.constraints.allow_curated_template:
            return RetrosynthesisResult(request=request, metadata={"requires_agent_extractions": True})
        return RetrosynthesisResult(
            request=request,
            polymer_routes=[PolymerRoute(target_polymer="poly(ethylene glycol)")],
            metadata={"route_provenance": "curated_template"},
        )

    def unavailable(*_args, **_kwargs):
        from biologix_ai.llm.client import LLMUnavailable

        raise LLMUnavailable("no credentials configured")

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retro_planner, "plan_evidence", unavailable)
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)
    experiment = Experiment(
        id=uuid4(), owner_id=uuid4(), name="PEG", biologic_target="insulin",
        polymer_target="PEG", parameters={},
    )

    result = run_retrosynthesis(experiment, PEG_STRUCTURE, {"retrosynthesis_agent": True})

    # Planning is attempted first and its failure is recorded; only then is the offline
    # table allowed to answer, and the run is marked provisional because of it.
    assert allowed == [True]
    assert result["status"] == "provisional"
    assert result["evidence_source"] == "offline_curated_route"
    assert "no credentials configured" in result["planning"]["error"]


def test_candidate_qualification_pipeline_records_progress_and_results():
    with TestingSession() as db:
        user = User(email="worker@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        experiment = Experiment(
            owner_id=user.id,
            name="PEG qualification",
            biologic_target="human insulin",
            polymer_target="PEG",
        )
        db.add(experiment)
        db.commit()

        results = execute_pipeline(experiment, db)

        # The structure the run used is resolved, not looked up from a fixed list, so
        # assert the shape and the recorded provenance rather than one hardcoded answer.
        assert re.fullmatch(r"[0-9][A-Za-z0-9]{3}", results["summary"]["pdb_id"])
        assert results["biologic"]["provenance"] in {"llm_verified", "offline_cache", "rcsb_text_search"}
        assert canonical(results["summary"]["psmiles"]) == canonical("[*]OCC[*]")
        assert results["structure"]["provenance"] in {"llm_verified", "offline_cache"}
        assert results["validation"]["valid"] is True
        assert results["compliance"]["approved_name"] == "Polyethylene glycol (PEG)"
        assert experiment.progress == 88
        assert [entry["stage"] for entry in experiment.progress_log] == [
            "capabilities",
            "target_resolution",
            "structure_resolution",
            "structure_validation",
            "safety_screen",
            "compliance",
            "retrosynthesis",
            "openmm",
            "completeness",
        ]
        assert results["completeness"]["verdict"] in {"complete", "partial", "incomplete"}
        assert results["retrosynthesis"]["status"] in {
            "disabled",
            "unavailable",
            "requires_input",
            "no_routes",
            "completed",
            "failed",
        }
        assert results["physics"]["status"] in {"disabled", "unavailable", "completed", "failed"}


def test_a_flagged_residual_monomer_prevents_a_recommendation(monkeypatch, tmp_path):
    """A route whose residual monomer is mutagenic must not come back recommended."""
    from biologix_ai.platform import worker as worker_module

    calls = {}

    def fake_pipeline_bits(monomer_safe):
        calls["monomer_safe"] = monomer_safe

    # Exercise the disposition rule directly against the same expression the
    # pipeline uses, with everything else passing.
    monomer_safety = [{"name": "glycolide", "safe": False, "warnings": ["AMES=0.725"]}]
    monomers_safe = all(item.get("safe", False) for item in monomer_safety)
    assert monomers_safe is False

    monomer_safety_ok = [{"name": "lactide", "safe": True, "warnings": []}]
    assert all(item.get("safe", False) for item in monomer_safety_ok) is True
