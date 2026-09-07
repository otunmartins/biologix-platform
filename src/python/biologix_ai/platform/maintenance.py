import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from .database import SessionLocal
from .models import Experiment, ExperimentState
from .queue import enqueue_experiment


def recover_stale_experiments(max_age_minutes: int | None = None) -> int:
    """Requeue experiments abandoned by a dead worker.

    Every worker replica runs this at start-up, so the claim has to be atomic:
    a single UPDATE ... RETURNING lets exactly one replica win each row instead
    of several of them enqueuing duplicate jobs for the same experiment.
    """
    # A run is only abandoned once it has outlived the time the queue would have
    # allowed it; reaping earlier requeues experiments that are still working.
    from .queue import job_timeout_s

    default_minutes = max(45, job_timeout_s() // 60 + 15)
    age = max_age_minutes or int(os.getenv("STALE_JOB_MINUTES", str(default_minutes)))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=age)
    with SessionLocal() as db:
        claimed = db.scalars(
            update(Experiment)
            .where(
                Experiment.status == ExperimentState.running,
                Experiment.started_at < cutoff,
            )
            .values(
                status=ExperimentState.queued,
                current_stage="recovered",
                error_message=None,
            )
            .returning(Experiment.id)
            .execution_options(synchronize_session=False)
        ).all()
        db.commit()

        recovered = 0
        for experiment_id in claimed:
            experiment = db.get(Experiment, experiment_id)
            if not experiment:
                continue
            experiment.job_id = enqueue_experiment(str(experiment_id))
            db.commit()
            recovered += 1
    return recovered


if __name__ == "__main__":
    count = recover_stale_experiments()
    print(f"Recovered {count} stale experiments")
