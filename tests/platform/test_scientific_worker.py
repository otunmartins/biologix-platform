from biologix_ai.platform.models import Experiment, User
from biologix_ai.platform.worker import execute_pipeline

from test_platform_api import TestingSession


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
        assert experiment.progress == 75
        assert [entry["stage"] for entry in experiment.progress_log] == [
            "target_resolution",
            "structure_validation",
            "safety_screen",
            "compliance",
        ]
