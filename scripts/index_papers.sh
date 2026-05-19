#!/usr/bin/env bash
# Build or update the PaperQA2 search index over papers in papers/.
#
# Uses the same settings as insulin_ai_mcp_server (paper_qa_config.py).
# Supports Ollama: PQA_EMBEDDING=ollama/nomic-embed-text (no OpenAI key).
# Or OpenAI: OPENAI_API_KEY=sk-... with default text-embedding-3-small.
#
# Index cached at ~/.pqa/indexes/. Re-run when adding new papers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Check for OpenAI or Ollama config
if [[ -z "${OPENAI_API_KEY:-}" ]] && [[ -z "${PQA_EMBEDDING:-}" ]]; then
  echo "Set one of:"
  echo "  OPENAI_API_KEY=sk-...           # OpenAI embeddings"
  echo "  PQA_EMBEDDING=ollama/nomic-embed-text   # Ollama (run: ollama pull nomic-embed-text)"
  exit 1
fi

export PYTHONPATH="$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "
from insulin_ai.paper_qa_config import build_index
print(build_index())
"
