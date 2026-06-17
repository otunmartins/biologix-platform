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
#
# Recommended (no clone):
#   curl -fsSL https://raw.githubusercontent.com/otunmartins/biologix-platform/main/scripts/docker_ghcr_run.sh -o docker_ghcr_run.sh
#   bash docker_ghcr_run.sh
#
# Avoid `curl … | bash` — stdin is a pipe and Docker `-it` may fail with
# "the input device is not a TTY" unless /dev/tty is available.
#
# Optional:
#   BIOLOGIX_AI_IMAGE=ghcr.io/otunmartins/biologix-ai:0.5.23 bash docker_ghcr_run.sh
#   bash docker_ghcr_run.sh -e ANTHROPIC_API_KEY=sk-ant-...
set -euo pipefail

IMAGE="${BIOLOGIX_AI_IMAGE:-ghcr.io/otunmartins/biologix-ai:0.5.23}"
RUN_DIR="${PWD}"

_host_logical_cpus() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sysctl -n hw.logicalcpu 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1
  elif [[ -f /proc/cpuinfo ]]; then
    grep -c ^processor /proc/cpuinfo
  elif command -v nproc &>/dev/null; then
    nproc --all 2>/dev/null || nproc
  else
    echo 1
  fi
}

if [[ -z "${DOCKER_CPU_LIMIT:-}" ]]; then
  _host="$(_host_logical_cpus)"
  _pct="${DOCKER_CPU_PCT:-75}"
  CPUS=$(( _host * _pct / 100 ))
  [[ "$CPUS" -lt 1 ]] && CPUS=1
else
  CPUS="${DOCKER_CPU_LIMIT}"
fi

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

# Clear leftover OpenTUI mouse-tracking from a prior hung/killed session.
_restore_host_tty

mkdir -p "${RUN_DIR}/runs" "${RUN_DIR}/papers"

echo "Image: ${IMAGE}" >&2
echo "Container CPU quota: --cpus=${CPUS} (${DOCKER_CPU_PCT:-75}% of host logical CPUs)" >&2
echo "Host TTY will be restored when this session ends (including after docker kill)." >&2

# curl | bash connects stdin to a pipe, not the terminal — docker run -it then fails with
# "the input device is not a TTY". Attach /dev/tty explicitly when stdin is not a TTY.
_run_docker() {
  docker run --platform linux/amd64 -it --rm --init \
    --cpus "${CPUS}" \
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
}

if [[ -t 0 ]]; then
  _run_docker "$@"
else
  if [[ ! -r /dev/tty ]]; then
    echo "error: stdin is not a TTY (e.g. curl | bash) and /dev/tty is unavailable." >&2
    echo "Save and run directly: curl -fsSL ... -o docker_ghcr_run.sh && bash docker_ghcr_run.sh" >&2
    exit 1
  fi
  _run_docker "$@" < /dev/tty
fi
