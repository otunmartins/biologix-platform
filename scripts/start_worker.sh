#!/usr/bin/env bash
set -euo pipefail

if [[ "${SCIENTIFIC_RETROSYNTHESIS_ENABLED:-true}" =~ ^(1|true|yes|on)$ ]]; then
  if ! python -c "from biologix_ai.retrosynthesis.aizynth_config import models_ready; raise SystemExit(0 if models_ready() else 1)"; then
    bash /app/scripts/setup_aizynthfinder.sh
  fi
fi

python -m biologix_ai.platform.maintenance
exec rq worker biologix --url "${REDIS_URL:-redis://localhost:6379/0}"
