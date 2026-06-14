#!/usr/bin/env bash
# In-container CPU defaults for OpenMM (GHCR image entrypoint).
# Auto-detects logical CPUs via nproc and configures parallel workers + OMP threads.
# Sourced from docker/entrypoint.sh when BIOLOGIX_AI_EVAL_MAX_WORKERS / OMP_NUM_THREADS unset.

_container_logical_cpus() {
  if command -v nproc &>/dev/null; then
    nproc 2>/dev/null || nproc --all 2>/dev/null || echo 1
  elif [[ -f /proc/cpuinfo ]]; then
    grep -c ^processor /proc/cpuinfo
  else
    echo 1
  fi
}

docker_default_eval_max_workers() {
  local cpus frac
  cpus="$(_container_logical_cpus)"
  # 1.0 = use every CPU Docker exposes to this container (default for GHCR images).
  frac="${BIOLOGIX_AI_EVAL_CPU_FRACTION:-1.0}"
  python3 - <<PY
import math
cpus = int("${cpus}")
frac = float("${frac}")
print(max(1, math.floor(cpus * frac)))
PY
}

docker_default_omp_num_threads() {
  local cpus workers batch
  cpus="$(_container_logical_cpus)"
  workers="${1:-1}"
  batch="${BIOLOGIX_AI_OPENMM_EXPECTED_BATCH:-3}"
  python3 - <<PY
cpus = int("${cpus}")
workers = max(1, int("${workers}"))
batch = max(1, int("${batch}"))
if workers == 1:
    # One candidate at a time — give OpenMM all container CPUs.
    print(max(1, cpus))
else:
    # Agent runs up to 3 candidates in parallel; split threads across active jobs.
    active = min(workers, batch, cpus)
    print(max(1, cpus // active))
PY
}
