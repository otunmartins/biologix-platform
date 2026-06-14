#!/usr/bin/env bash
# docker compose run with DOCKER_CPU_LIMIT = 75% of host logical CPUs (unless preset).
#
# Usage:
#   ./scripts/docker_compose_run.sh
#   ./scripts/docker_compose_run.sh bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export DOCKER_CPU_LIMIT="${DOCKER_CPU_LIMIT:-$("${SCRIPT_DIR}/docker_cpu_limit.sh")}"
echo "DOCKER_CPU_LIMIT=${DOCKER_CPU_LIMIT} (${DOCKER_CPU_PCT:-75}% of host logical CPUs)"

cd "${REPO_ROOT}"
exec docker compose run --rm --cpus "${DOCKER_CPU_LIMIT}" biologix "$@"
