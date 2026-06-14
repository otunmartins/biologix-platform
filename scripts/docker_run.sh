#!/usr/bin/env bash
# Run the published Biologix AI image with 75% host CPU quota and standard mounts.
#
# Usage (from repo root or any directory — creates ./runs and ./papers here):
#   ./scripts/docker_run.sh
#   ./scripts/docker_run.sh -e BIOLOGIX_AI_OPENMM_AUTO=skip
#   BIOLOGIX_AI_IMAGE=ghcr.io/otunmartins/biologix-ai:0.5.7 ./scripts/docker_run.sh
#
# Requires Docker Desktop (Mac/Windows) to allocate at least as many CPUs as the
# computed limit (Settings → Resources → CPUs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPUS="$("${SCRIPT_DIR}/docker_cpu_limit.sh")"
IMAGE="${BIOLOGIX_AI_IMAGE:-ghcr.io/otunmartins/biologix-ai:0.5.10}"
RUN_DIR="${PWD}"

mkdir -p "${RUN_DIR}/runs" "${RUN_DIR}/papers"

echo "Container CPU quota: --cpus=${CPUS} (${DOCKER_CPU_PCT:-75}% of host logical CPUs)"
echo "Ensure Docker Desktop → Resources → CPUs is at least ${CPUS}."

exec docker run --platform linux/amd64 -it --rm --init \
  --cpus "${CPUS}" \
  -e TERM=xterm-256color \
  -e LC_ALL=C.UTF-8 \
  -v "${RUN_DIR}/runs:/app/runs" \
  -v "${RUN_DIR}/papers:/app/papers" \
  -v biologix-data:/app/data \
  "$@" \
  "${IMAGE}"
