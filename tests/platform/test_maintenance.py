from datetime import datetime, timedelta, timezone

from biologix_ai.platform import maintenance
from biologix_ai.platform.models import Experiment, ExperimentState, User

from test_platform_api import TestingSession


def test_stale_running_experiments_are_requeued(monkeypatch):
    with TestingSession() as db:
        user = User(email="recovery@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        experiment = Experiment(
            owner_id=user.id,
            name="Interrupted run",
            biologic_target="insulin",
            status=ExperimentState.running,
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add(experiment)
        db.commit()
        experiment_id = experiment.id

    monkeypatch.setattr(maintenance, "SessionLocal", TestingSession)
    monkeypatch.setattr(maintenance, "enqueue_experiment", lambda value: f"recovered-{value}")

    assert maintenance.recover_stale_experiments(max_age_minutes=30) == 1
    with TestingSession() as db:
        recovered = db.get(Experiment, experiment_id)
        assert recovered.status == ExperimentState.queued
        assert recovered.current_stage == "recovered"
        assert recovered.job_id == f"recovered-{experiment_id}"
