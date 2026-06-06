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

# URLs from aizynthfinder.tools.download_public_data (curl is more reliable in CI than
# streaming requests inside conda run / non-TTY docker builds).
# Format: filename|url (bash 3 compatible — macOS /bin/bash)
AIZYNTH_FILES=(
  "uspto_model.onnx|https://zenodo.org/record/7797465/files/uspto_model.onnx"
  "uspto_templates.csv.gz|https://zenodo.org/record/7341155/files/uspto_unique_templates.csv.gz"
  "uspto_ringbreaker_model.onnx|https://zenodo.org/record/7797465/files/uspto_ringbreaker_model.onnx"
  "uspto_ringbreaker_templates.csv.gz|https://zenodo.org/record/7341155/files/uspto_ringbreaker_unique_templates.csv.gz"
  "zinc_stock.hdf5|https://ndownloader.figshare.com/files/23086469"
  "uspto_filter_model.onnx|https://zenodo.org/record/7797465/files/uspto_filter_model.onnx"
)

download_file() {
  local url="$1"
  local dest="$2"
  local name="${3:-$(basename "$dest")}"
  local attempt=1
  local max_attempts=5

  while (( attempt <= max_attempts )); do
    echo "Downloading ${name} (attempt ${attempt}/${max_attempts}) ..."
    if curl -fL --retry 5 --retry-delay 5 --retry-all-errors \
      --connect-timeout 30 --max-time 7200 \
      -o "$dest" "$url"; then
      return 0
    fi
    rm -f "$dest"
    echo "  Download failed; retrying in $((attempt * 20))s ..."
    sleep $((attempt * 20))
    ((attempt++))
  done

  echo "ERROR: failed to download ${name} from ${url}" >&2
  return 1
}

echo "Downloading public data to $DEST ..."
for entry in "${AIZYNTH_FILES[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  download_file "$url" "$DEST/$name" "$name"
done

abs_dest="$(cd "$DEST" && pwd)"
cat > "$DEST/config.yml" <<EOF
expansion:
  uspto:
    - ${abs_dest}/uspto_model.onnx
    - ${abs_dest}/uspto_templates.csv.gz
  ringbreaker:
    - ${abs_dest}/uspto_ringbreaker_model.onnx
    - ${abs_dest}/uspto_ringbreaker_templates.csv.gz
filter:
  uspto: ${abs_dest}/uspto_filter_model.onnx
stock:
  zinc: ${abs_dest}/zinc_stock.hdf5
EOF

echo "Configuration file written to $DEST/config.yml"
echo "Done. Set BIOLOGIX_AI_AIZYNTH_CONFIG=$DEST/config.yml"
