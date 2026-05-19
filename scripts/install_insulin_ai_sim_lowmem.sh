#!/usr/bin/env bash
# Reliable conda env installer: uses micromamba (fast, stable libmamba 2.x) when available,
# falls back to conda --solver classic. Installs in waves to keep peak RAM low.
#
# Usage (from repo root):
#   ./install                    # default conda path runs this script
#   bash scripts/install_insulin_ai_sim_lowmem.sh
#
# Requires: micromamba (preferred), conda, or mamba on PATH.
# Remove legacy "omnia" channel first — see scripts/fix_conda_channels_for_insulin_ai.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_NAME="${INSULIN_AI_CONDA_ENV:-insulin-ai-sim}"

if conda config --show channels 2>/dev/null | grep -Fqi omnia; then
  echo "ERROR: conda still lists channel 'omnia'. Run:  bash scripts/fix_conda_channels_for_insulin_ai.sh" >&2
  exit 1
fi

cd "$REPO_ROOT"

# --- Detect package manager + conda prefix path ---
CONDA_PREFIX_PATH=""
if command -v conda &>/dev/null; then
  CONDA_PREFIX_PATH="$(conda info --base 2>/dev/null)/envs/$ENV_NAME"
fi

wave_install() {
  if command -v micromamba &>/dev/null && [[ -n "$CONDA_PREFIX_PATH" ]]; then
    micromamba install -p "$CONDA_PREFIX_PATH" -c conda-forge -y "$@"
  elif command -v conda &>/dev/null; then
    conda install -n "$ENV_NAME" --override-channels -c conda-forge -y --solver classic "$@"
  else
    echo "ERROR: need micromamba or conda on PATH" >&2
    exit 1
  fi
}

create_env() {
  if command -v conda &>/dev/null; then
    conda create -n "$ENV_NAME" --override-channels -c conda-forge -y --solver classic python=3.11 pip
    CONDA_PREFIX_PATH="$(conda info --base 2>/dev/null)/envs/$ENV_NAME"
  elif command -v micromamba &>/dev/null; then
    micromamba create -n "$ENV_NAME" -c conda-forge -y python=3.11 pip
    CONDA_PREFIX_PATH="$(micromamba info 2>/dev/null | grep 'base environment' | awk '{print $NF}')/envs/$ENV_NAME"
  else
    echo "ERROR: need conda or micromamba on PATH" >&2
    exit 1
  fi
}

# --- Auto-install micromamba if missing (fast, ~10 MB) ---
if ! command -v micromamba &>/dev/null; then
  echo "micromamba not found — installing standalone binary to ~/.local/bin ..."
  mkdir -p "$HOME/.local/bin"
  if curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C "$HOME/.local/bin" --strip-components=1 bin/micromamba 2>/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
    echo "Installed micromamba $(micromamba --version 2>&1)"
  else
    echo "WARNING: could not install micromamba; falling back to conda --solver classic." >&2
  fi
fi

# --- Create or reuse env ---
if ! conda env list 2>/dev/null | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Creating env $ENV_NAME (python 3.11 + pip)..."
  create_env
else
  echo "Env $ENV_NAME exists — adding missing conda packages in waves."
fi

# --- Conda waves ---
echo "Wave 1/4: openmm, pdbfixer, packmol..."
wave_install openmm pdbfixer packmol

echo "Wave 2/4: rdkit..."
wave_install rdkit

echo "Wave 3/4: OpenFF toolkit + units..."
wave_install "openff-toolkit>=0.18.0" "openff-units>=0.2"

# --- Pip wave ---
echo "Wave 4/4: pip packages + editable insulin-ai..."
RUN=(conda run -n "$ENV_NAME" --no-capture-output)
if ! command -v conda &>/dev/null; then
  RUN=(micromamba run -p "$CONDA_PREFIX_PATH")
fi

"${RUN[@]}" python -m pip install -U pip setuptools wheel
"${RUN[@]}" python -m pip install -r "$REPO_ROOT/requirements.txt"
"${RUN[@]}" python -m pip install -e "$REPO_ROOT"

echo ""
echo "Done. Verify:"
echo "  conda activate $ENV_NAME"
echo "  python -c \"import openmm, rdkit; from openff.toolkit import Molecule; print('OK')\""
