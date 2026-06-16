#!/usr/bin/env bash
# Standalone launcher for the published GHCR image (no full repo clone required).
#
# OpenCode OpenTUI enables xterm mouse tracking; after docker kill the host TTY
# prints gibberish (35;90;1M) until mouse modes are disabled.  This script
# installs a host EXIT trap so cleanup runs when the container session ends.
#
# Also passes OPENMM_CPU_THREADS=1, BIOLOGIX_SKIP_ZINC_BRIDGE=1, and
# BIOLOGIX_TREE_TIMEOUT=300 for Rosetta (linux/amd64 on Apple Silicon) stability.
#
# Usage (from any directory — creates ./runs and ./papers here):
#   bash docker_ghcr_run.sh
#   curl -fsSL https://raw.githubusercontent.com/otunmartins/biologix-platform/biologix-main/scripts/docker_ghcr_run.sh | bash
#
# Optional:
#   BIOLOGIX_AI_IMAGE=ghcr.io/otunmartins/biologix-ai:0.5.18 bash docker_ghcr_run.sh
#   bash docker_ghcr_run.sh -e ANTHROPIC_API_KEY=sk-ant-...
set -euo pipefail

IMAGE="${BIOLOGIX_AI_IMAGE:-ghcr.io/otunmartins/biologix-ai:0.5.18}"
RUN_DIR="${PWD}"
CPUS="${DOCKER_CPU_LIMIT:-}"

_restore_host_tty() {
  local tty_dev=/dev/tty
  local disable_mouse=$'\e[?1000l\e[?1002l\e[?1003l\e[?1006l\e[?1015l'
  local reset_attrs=$'\e[0m\e[?25h\e[?1049l'
  if [[ -w "$tty_dev" ]]; then
    printf '%s%s' "$disable_mouse" "$reset_attrs" >"$tty_dev" 2>/dev/null || true
    stty sane <"$tty_dev" 2>/dev/null || true
  else
    printf '%s%s' "$disable_mouse" "$reset_attrs" 2>/dev/null || true
    stty sane 2>/dev/null || true
  fi
}

trap '_restore_host_tty' EXIT INT TERM HUP

mkdir -p "${RUN_DIR}/runs" "${RUN_DIR}/papers"

cpu_args=()
if [[ -n "$CPUS" ]]; then
  cpu_args=(--cpus "$CPUS")
fi

echo "Image: ${IMAGE}"
echo "Host TTY will be restored when this session ends (including after docker kill)."

docker run --platform linux/amd64 -it --rm --init \
  "${cpu_args[@]}" \
  -e TERM=xterm-256color \
  -e LC_ALL=C.UTF-8 \
  -e OPENMM_CPU_THREADS="${OPENMM_CPU_THREADS:-1}" \
  -e BIOLOGIX_SKIP_ZINC_BRIDGE="${BIOLOGIX_SKIP_ZINC_BRIDGE:-1}" \
  -e BIOLOGIX_PDF_TIMEOUT="${BIOLOGIX_PDF_TIMEOUT:-60}" \
  -e BIOLOGIX_TREE_TIMEOUT="${BIOLOGIX_TREE_TIMEOUT:-300}" \
  -e BIOLOGIX_AIZYNTH_TIMEOUT="${BIOLOGIX_AIZYNTH_TIMEOUT:-180}" \
  -v "${RUN_DIR}/runs:/app/runs" \
  -v "${RUN_DIR}/papers:/app/papers" \
  -v biologix-data:/app/data \
  "$@" \
  "${IMAGE}"
