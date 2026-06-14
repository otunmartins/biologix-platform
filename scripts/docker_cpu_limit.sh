#!/usr/bin/env bash
# Print a CPU count: floor(DOCKER_CPU_PCT% of host logical CPUs), minimum 1.
# Used by scripts/docker_run.sh and docker compose (DOCKER_CPU_LIMIT).
#
# Override percent: DOCKER_CPU_PCT=50 ./scripts/docker_cpu_limit.sh
set -euo pipefail

_pct="${DOCKER_CPU_PCT:-75}"

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

host="$(_host_logical_cpus)"
python3 - <<PY
import math
host = int("${host}")
pct = float("${_pct}") / 100.0
print(max(1, math.floor(host * pct)))
PY
