#!/usr/bin/env bash
# Download AiZynthFinder public models (~800MB) into data/aizynthfinder/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

DEST="$REPO_ROOT/data/aizynthfinder"
mkdir -p "$DEST"

if [[ -f "$DEST/config.yml" ]]; then
  echo "AiZynthFinder config already present at $DEST/config.yml"
  exit 0
fi

if ! conda_run python -c "import aizynthfinder" 2>/dev/null; then
  echo "Installing AiZynthFinder package into ${ENV_NAME}..."
  pip_in_env install paretoset
  pip_in_env install -e "$REPO_ROOT/extern/aizynthfinder"
fi

echo "Downloading public data to $DEST ..."
conda_run python -m aizynthfinder.tools.download_public_data "$DEST"

echo "Done. Set BIOLOGIX_AI_AIZYNTH_CONFIG=$DEST/config.yml"
