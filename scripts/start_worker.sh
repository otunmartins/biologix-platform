#!/usr/bin/env bash
set -euo pipefail

python -m biologix_ai.platform.maintenance
exec rq worker biologix --url "${REDIS_URL:-redis://localhost:6379/0}"
