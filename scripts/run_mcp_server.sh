#!/usr/bin/env bash
# Launch biologix-ai MCP server (for OpenCode).
# Uses biologix-ai-sim env when mamba/conda available; else falls back to python3.
# Run from project root; OpenCode invokes this from .opencode/opencode.jsonc.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"

# RetroSynAgent: OpenCode agent supplies extractions via submit_retro_extractions
export RETRO_LLM_BACKEND="${RETRO_LLM_BACKEND:-skip}"

# AiZynthFinder config default
if [[ -z "${BIOLOGIX_AI_AIZYNTH_CONFIG:-}" ]] && [[ -f "$REPO_ROOT/data/aizynthfinder/config.yml" ]]; then
  export BIOLOGIX_AI_AIZYNTH_CONFIG="$REPO_ROOT/data/aizynthfinder/config.yml"
fi

if command -v mamba &>/dev/null; then
  exec mamba run -n biologix-ai-sim --no-capture-output python biologix_ai_mcp_server.py
elif command -v conda &>/dev/null; then
  exec conda run -n biologix-ai-sim --no-capture-output python biologix_ai_mcp_server.py
else
  exec python3 biologix_ai_mcp_server.py
fi
