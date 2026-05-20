#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

cd "$REPO_ROOT"

echo "=== Initializing git submodules ==="
git submodule update --init --recursive

require_dir() {
  if [[ ! -d "$1" ]]; then
    echo "ERROR: missing $1 after git submodule update --init --recursive" >&2
    exit 1
  fi
}

require_dir "extern/RetroSynthesisAgent"
require_dir "extern/aizynthfinder"
require_dir "extern/admet_ai"

echo ""
echo "=== Installing RetroSynthesisAgent ==="
echo "  Installing RetroSynthesisAgent as editable package..."
pip_in_env install -e "extern/RetroSynthesisAgent" || echo "  WARNING: editable install failed, falling back to PYTHONPATH"
echo "  Installing RetroSynthesisAgent Python deps..."
pip_in_env install \
  graphviz pubchempy pyvis scholarly jsonpickle "fake-useragent>=1.4" \
  selenium networkx loguru openai "PyMuPDF>=1.22" "python-dotenv>=1.0" \
  2>/dev/null || pip_in_env install \
  graphviz pubchempy pyvis scholarly jsonpickle fake-useragent \
  selenium networkx loguru openai PyMuPDF python-dotenv

echo ""
echo "=== Installing AiZynthFinder from submodule ==="
pip_in_env install paretoset rdchiral
pip_in_env install -e "extern/aizynthfinder"

echo ""
echo "=== Installing ADMET-AI from submodule ==="
# Pin torch below 2.12 — 2.12+ can hit circular-import failures in torch.utils._pytree on import.
pip_in_env install "torch>=2.8.0,<2.12"
pip_in_env install -e "extern/admet_ai"

echo ""
echo "=== Ensuring biologix-ai retro + admet extras ==="
pip_in_env install -e ".[retro,admet,dev]"
pip_in_env install -U "pydantic>=2.10" "pydantic-core>=2.27" mcp[cli]

echo ""
echo "=== Submodule install done ==="
