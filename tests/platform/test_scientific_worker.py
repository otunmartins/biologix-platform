from biologix_ai.platform.models import Experiment, User
from uuid import uuid4

from biologix_ai.platform.worker import execute_pipeline, run_retrosynthesis

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

    result = run_retrosynthesis(experiment, {"retrosynthesis_agent": True})

    assert result["status"] == "requires_input"


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

    result = run_retrosynthesis(experiment, {"retrosynthesis_agent": True})

    assert result["status"] == "completed"


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

        assert results["summary"]["pdb_id"] == "4F1C"
        assert results["summary"]["psmiles"] == "[*]OCC[*]"
        assert results["validation"]["valid"] is True
        assert results["compliance"]["approved_name"] == "Polyethylene glycol (PEG)"
        assert experiment.progress == 78
        assert [entry["stage"] for entry in experiment.progress_log] == [
            "capabilities",
            "target_resolution",
            "structure_validation",
            "safety_screen",
            "compliance",
            "retrosynthesis",
            "openmm",
        ]
        assert results["retrosynthesis"]["status"] in {
            "disabled",
            "unavailable",
            "requires_input",
            "no_routes",
            "completed",
            "failed",
        }
        assert results["physics"]["status"] in {"disabled", "unavailable", "completed", "failed"}
