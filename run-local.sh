#!/usr/bin/env bash
# Bring up the full Biologix stack locally and open the UI.
#   ./run-local.sh                      -> start everything
#   ADMIN_EMAILS=you@example.com ./run-local.sh
set -euo pipefail
cd "$(dirname "$0")"

export WORKER_REPLICAS="${WORKER_REPLICAS:-2}"
export ADMIN_EMAILS="${ADMIN_EMAILS:-matrix@example.com}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.verify.yml)

command -v colima >/dev/null && ! colima status >/dev/null 2>&1 && {
  echo "starting docker vm..."; colima start --cpu 4 --memory 5; }

mkdir -p .verify/runs
echo "starting postgres, redis, minio, api and ${WORKER_REPLICAS} worker(s)..."
"${COMPOSE[@]}" up -d postgres redis minio api worker

echo "waiting for the api..."
for _ in $(seq 1 40); do
  curl -sf -o /dev/null http://localhost:8000/health && break || sleep 3
done

echo "starting the ui on http://localhost:3020"
( cd frontend && API_URL=http://localhost:8000 npx next dev -p 3020 ) &

sleep 8
echo
echo "  UI     http://localhost:3020"
echo "  Admin  http://localhost:3020/admin   (sign in as ${ADMIN_EMAILS})"
echo "  API    http://localhost:8000/docs"
echo
echo "stop with: ./stop-local.sh"
wait
