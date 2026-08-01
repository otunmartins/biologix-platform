import json
import sys

from sqlalchemy import text

from biologix_ai.platform.database import SessionLocal
from biologix_ai.platform.queue import experiment_queue, redis_connection
from biologix_ai.platform.storage import ArtifactStorage


def main() -> int:
    checks = {}
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = str(exc)
    try:
        checks["redis"] = "ok" if redis_connection().ping() else "failed"
        checks["queued_jobs"] = experiment_queue().count
    except Exception as exc:
        checks["redis"] = str(exc)
    try:
        ArtifactStorage().ensure_bucket()
        checks["artifact_storage"] = "ok"
    except Exception as exc:
        checks["artifact_storage"] = str(exc)
    print(json.dumps(checks, sort_keys=True))
    return 0 if checks.get("database") == checks.get("redis") == checks.get("artifact_storage") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
