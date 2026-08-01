import os

from redis import Redis
from rq import Queue


def redis_connection() -> Redis:
    return Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def experiment_queue() -> Queue:
    return Queue("biologix", connection=redis_connection(), default_timeout=1800)


def enqueue_experiment(experiment_id: str) -> str:
    from .worker import mark_job_failed, run_experiment

    job = experiment_queue().enqueue(
        run_experiment,
        experiment_id,
        job_timeout=1800,
        result_ttl=86400,
        failure_ttl=604800,
        on_failure=mark_job_failed,
    )
    return job.id
