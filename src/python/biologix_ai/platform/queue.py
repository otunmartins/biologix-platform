import os

from redis import Redis
from rq import Queue


def redis_connection() -> Redis:
    return Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def job_timeout_s() -> int:
    """Wall clock a whole experiment gets.

    A run now plans its own synthesis evidence and samples a trajectory rather than
    minimising a single pose, so the old 30 minutes cut real work short.
    """
    try:
        return int(os.getenv("EXPERIMENT_JOB_TIMEOUT_S", "5400"))
    except ValueError:
        return 5400


def experiment_queue() -> Queue:
    return Queue("biologix", connection=redis_connection(), default_timeout=job_timeout_s())


def enqueue_experiment(experiment_id: str) -> str:
    from .worker import mark_job_failed, run_experiment

    job = experiment_queue().enqueue(
        run_experiment,
        experiment_id,
        job_timeout=job_timeout_s(),
        result_ttl=86400,
        failure_ttl=604800,
        on_failure=mark_job_failed,
    )
    return job.id
