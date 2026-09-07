#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
pkill -f "next dev -p 3020" 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.verify.yml down
echo "stopped (database volume kept; add -v to the down command to wipe it)"
