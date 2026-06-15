#!/usr/bin/env bash
# Launch biologix-ai MCP server (for OpenCode).
# Docker: exec conda env python directly (no mamba run — avoids stdio pollution).
# Local dev: falls back to mamba/conda run or python3 when conda env is not on disk.
# Run from project root; OpenCode invokes this from .opencode/opencode.jsonc.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"

_conda_lib_path() {
  if [[ -n "${CONDA_PREFIX:-}" && -d "${CONDA_PREFIX}/lib" ]]; then
    echo "${CONDA_PREFIX}/lib"
    return 0
  fi
  local env_name="${BIOLOGIX_AI_CONDA_ENV:-biologix-ai-sim}"
  for root in /opt/conda/envs "$HOME/miniforge3/envs" "$HOME/miniconda3/envs" "$HOME/anaconda3/envs"; do
    if [[ -d "${root}/${env_name}/lib" ]]; then
      echo "${root}/${env_name}/lib"
      return 0
    fi
  done
  return 1
}
if _lib="$(_conda_lib_path)"; then
  export LD_LIBRARY_PATH="${_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export BIOLOGIX_AI_MCP_TIMEOUT_MS="${BIOLOGIX_AI_MCP_TIMEOUT_MS:-600000}"
export BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S="${BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S:-30}"

export RETRO_LLM_BACKEND="${RETRO_LLM_BACKEND:-skip}"

if [[ -z "${BIOLOGIX_AI_AIZYNTH_CONFIG:-}" ]] && [[ -f "$REPO_ROOT/data/aizynthfinder/config.yml" ]]; then
  export BIOLOGIX_AI_AIZYNTH_CONFIG="$REPO_ROOT/data/aizynthfinder/config.yml"
fi

_resolve_python() {
  if [[ -n "${BIOLOGIX_AI_PYTHON:-}" && -x "${BIOLOGIX_AI_PYTHON}" ]]; then
    echo "${BIOLOGIX_AI_PYTHON}"
    return 0
  fi
  local env_name="${BIOLOGIX_AI_CONDA_ENV:-biologix-ai-sim}"
  local candidates=(
    "/opt/conda/envs/${env_name}/bin/python"
    "${CONDA_PREFIX:-}/bin/python"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -n "$c" && -x "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

if _py="$(_resolve_python)"; then
  exec "$_py" "$REPO_ROOT/biologix_ai_mcp_server.py"
fi

if command -v mamba &>/dev/null; then
  exec mamba run -n biologix-ai-sim --no-capture-output python "$REPO_ROOT/biologix_ai_mcp_server.py"
elif command -v conda &>/dev/null; then
  exec conda run -n biologix-ai-sim --no-capture-output python "$REPO_ROOT/biologix_ai_mcp_server.py"
else
  exec python3 "$REPO_ROOT/biologix_ai_mcp_server.py"
fi
