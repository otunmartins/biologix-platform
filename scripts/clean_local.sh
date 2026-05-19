#!/usr/bin/env bash
# Remove temp run outputs and optional nested clones (safe; outputs regenerate).
# Nested clones are gitignored and not required for insulin-ai MCP.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Cleaning under $(pwd) ..."
rm -rf cycle_results iterative_results discovery_state runs chat_memory
rm -rf opencode_src FRIDGEFREENET mcp_servers
rm -rf src/python/*.egg-info
echo "Done. (papers/, .opencode/, src/ unchanged)"
