#!/usr/bin/env bash
# Launch insulin-ai MCP server (for OpenCode).
# Uses insulin-ai-sim env when mamba/conda available; else falls back to python3.
# Run from project root; OpenCode invokes this from .opencode/opencode.jsonc.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"

if command -v mamba &>/dev/null; then
  exec mamba run -n insulin-ai-sim --no-capture-output python insulin_ai_mcp_server.py
elif command -v conda &>/dev/null; then
  exec conda run -n insulin-ai-sim --no-capture-output python insulin_ai_mcp_server.py
else
  exec python3 insulin_ai_mcp_server.py
fi
