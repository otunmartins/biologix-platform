#!/usr/bin/env bash
# Clone third-party benchmark repos into extern/benchmarks/ (see docs/THIRD_PARTY_BENCHMARKS.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG="$ROOT/extern/benchmarks/polymer-generative-models"
IBM="$ROOT/extern/benchmarks/ibm-logical-agent-polymer"

clone_if_missing() {
  local url="$1" dest="$2" name="$3"
  if [[ -d "$dest/$name/.git" ]]; then
    echo "Already present: $dest/$name"
    return 0
  fi
  mkdir -p "$dest"
  git clone --depth 1 "$url" "$dest/$name"
}

clone_if_missing "https://github.com/ytl0410/Polymer-Generative-Models-Benchmark.git" "$PG" "Polymer-Generative-Models-Benchmark"
clone_if_missing "https://github.com/IBM/logical-agent-driven-polymer-discovery.git" "$IBM" "logical-agent-driven-polymer-discovery"
echo "Done. Record commits in extern/benchmarks/PINNED_VERSIONS.md"
