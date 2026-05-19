#!/usr/bin/env bash
# Download AiZynthFinder public models (~800MB) into data/aizynthfinder/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$REPO_ROOT/data/aizynthfinder"
mkdir -p "$DEST"

if [[ -f "$DEST/config.yml" ]]; then
  echo "AiZynthFinder config already present at $DEST/config.yml"
  exit 0
fi

echo "Installing AiZynthFinder package..."
if command -v mamba &>/dev/null; then
  mamba run -n insulin-ai-sim pip install -e "$REPO_ROOT/extern/aizynthfinder" paretoset
elif command -v conda &>/dev/null; then
  conda run -n insulin-ai-sim pip install -e "$REPO_ROOT/extern/aizynthfinder" paretoset
else
  pip install -e "$REPO_ROOT/extern/aizynthfinder" paretoset
fi

echo "Downloading public data to $DEST ..."
python -m aizynthfinder.tools.download_public_data "$DEST"

echo "Done. Set INSULIN_AI_AIZYNTH_CONFIG=$DEST/config.yml"
