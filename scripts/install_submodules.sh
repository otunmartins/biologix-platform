#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

cd "$REPO_ROOT"

echo "=== Initializing git submodules ==="
if ! git submodule update --init --recursive; then
  echo "Git metadata is incomplete; using pinned clone fallbacks."
fi

ensure_submodule_checkout() {
  local path="$1"
  local url="$2"
  local revision="$3"

  # Docker build contexts contain the checked-out files but may omit the
  # submodule's backing .git/modules objects. In that case the files are ready
  # to install even though `git -C <path>` cannot inspect them.
  if [[ -d "$path" ]] && find "$path" -mindepth 1 -maxdepth 1 ! -name .git -print -quit | grep -q .; then
    echo "  Found checked-out content: $path"
    return 0
  fi

  echo "  Cloning pinned fallback: $path @ $revision"
  rm -rf "$path"
  git clone --filter=blob:none --no-checkout "$url" "$path"
  git -C "$path" checkout --detach "$revision"
}

ensure_submodule_checkout \
  "extern/RetroSynthesisAgent" \
  "https://github.com/otunmartins/RetroSynthesisAgent" \
  "7060c75a2450bd8f3ce82637d2806f0e15f020a1"
ensure_submodule_checkout \
  "extern/aizynthfinder" \
  "https://github.com/MolecularAI/aizynthfinder.git" \
  "21ff546d5f22331b078390a2f12dc04defc3f39c"
ensure_submodule_checkout \
  "extern/admet_ai" \
  "https://github.com/swansonk14/admet_ai.git" \
  "c65bf0418e19c65d7228f9e40da5d0152aade756"

echo ""
echo "=== Installing RetroSynthesisAgent ==="
echo "  Using vendored source via PYTHONPATH (avoids incompatible package metadata)."
echo "  Installing RetroSynthesisAgent Python deps..."
pip_in_env install \
  graphviz pubchempy pyvis "scholarly>=1.7,<1.8" "bibtexparser>=1.4,<2" \
  jsonpickle "fake-useragent>=1.4" "selenium>=4,<4.27" \
  "networkx>=2.8,<3" "urllib3>=1.26.19,<2" loguru openai \
  "PyMuPDF>=1.22" "python-dotenv>=1.0" \
  2>/dev/null || pip_in_env install \
  graphviz pubchempy pyvis "scholarly<1.8" "bibtexparser<2" jsonpickle \
  fake-useragent "selenium<4.27" "networkx<3" "urllib3<2" loguru \
  openai PyMuPDF python-dotenv

echo ""
echo "=== Installing AiZynthFinder from submodule ==="
pip_in_env install paretoset rdchiral
pip_in_env install -e "extern/aizynthfinder"

echo ""
echo "=== Installing ADMET-AI in isolated conda environment ==="
# ADMET-AI 2.x needs RDKit >=2025.9, while AiZynthFinder 4.4 needs RDKit <2024.
# A separate environment is required; sharing one produces a silently broken solve.
ADMET_ENV_NAME="biologix-admet"
MAMBA_MAX_ATTEMPTS=3
create_admet_environment() {
  local attempt
  for attempt in $(seq 1 "$MAMBA_MAX_ATTEMPTS"); do
    conda env remove -n "$ADMET_ENV_NAME" -y >/dev/null 2>&1 || true
    if mamba create -n "$ADMET_ENV_NAME" -y -c conda-forge \
      python=3.11 pip "pytorch>=2.8,<2.12" \
      numpy pandas scipy scikit-learn seaborn; then
      return 0
    fi
    echo "ADMET environment attempt ${attempt}/${MAMBA_MAX_ATTEMPTS} failed; clearing package cache" >&2
    mamba clean --all --yes || true
  done
  return 1
}
if ! conda run -n "$ADMET_ENV_NAME" python -c \
  "import torch; assert (2, 8) <= tuple(map(int, torch.__version__.split('.')[:2])) < (2, 12)" \
  >/dev/null 2>&1; then
  create_admet_environment
fi
ADMET_PYTHON="$(conda run -n "$ADMET_ENV_NAME" python -c 'import sys; print(sys.executable)')"
PYTHONPATH="" "$ADMET_PYTHON" -m pip install \
  "rdkit>=2025.9.5" "chemprop>=2.2.2" lightning "tqdm>=4.66.3" \
  "typed-argument-parser>=1.11.0"
PYTHONPATH="" "$ADMET_PYTHON" -m pip install --no-deps -e "extern/admet_ai"
PYTHONPATH="" "$ADMET_PYTHON" -m pip check

echo ""
echo "=== Ensuring biologix-ai retro + admet extras ==="
pip_in_env install -e ".[retro,dev]"
pip_in_env install -U "pydantic>=2.10" "pydantic-core>=2.27" "mcp[cli]>=1.30,<2"
pip_in_env install \
  "numpy>=1.24,<2" "pandas>=2,<3" "scipy>=1.11,<1.15" \
  "networkx>=2.8,<3" "rdkit>=2023.9.1,<2024" \
  "urllib3>=1.26.19,<2" "selenium>=4,<4.27" \
  "bibtexparser>=1.4,<2" "paramz<0.10"
PYTHONPATH="" pip_in_env check

echo ""
echo "=== Installing precursor database dependencies ==="
pip_in_env install h5py requests "datasets>=2.0" hf_transfer "huggingface-hub>=0.25"

echo ""
echo "=== RetroSynthesisAgent bootstrap (emol.json) ==="
conda_run python -c "from biologix_ai.retrosynthesis.retrosyn_bootstrap import ensure_retrosyn_agent_ready; ensure_retrosyn_agent_ready(); print('RetroSyn bootstrap OK')"

echo ""
echo "=== Building precursor database (tiers 1–3) ==="
echo "  Tier 1: manual polymer-chemistry essentials (offline)"
echo "  Tier 2: SMiPoly 1,083 polymer monomers (GitHub)"
echo "  Tier 3: Molport InChIKey set — resumable HuggingFace snapshot + local parse"
echo "  Tier 4 runs after the Dockerfile downloads the AiZynthFinder ZINC stock."
conda_run python scripts/build_precursor_db.py --tiers 1,2,3

echo ""
echo "=== Submodule install done ==="
