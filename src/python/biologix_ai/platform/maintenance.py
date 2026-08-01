import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import SessionLocal
from .models import Experiment, ExperimentState
from .queue import enqueue_experiment


def recover_stale_experiments(max_age_minutes: int | None = None) -> int:
    age = max_age_minutes or int(os.getenv("STALE_JOB_MINUTES", "45"))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=age)
    recovered = 0
    with SessionLocal() as db:
        experiments = db.scalars(
            select(Experiment).where(
                Experiment.status == ExperimentState.running,
                Experiment.started_at < cutoff,
            )
        ).all()
        for experiment in experiments:
            experiment.status = ExperimentState.queued
            experiment.current_stage = "recovered"
            experiment.error_message = None
            experiment.job_id = enqueue_experiment(str(experiment.id))
            db.commit()
            recovered += 1
    return recovered


if __name__ == "__main__":
    count = recover_stale_experiments()
    print(f"Recovered {count} stale experiments")
