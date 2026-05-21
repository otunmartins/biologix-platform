#!/usr/bin/env bash
# Shared helpers for biologix-ai install scripts.
# Source from install, install_biologix_ai_sim_lowmem.sh, install_submodules.sh, etc.

set -euo pipefail

ENV_NAME="${BIOLOGIX_AI_CONDA_ENV:-biologix-ai-sim}"
CONDA_PREFIX_PATH=""

micromamba_usable() {
  command -v micromamba &>/dev/null && micromamba --version &>/dev/null 2>&1
}

remove_broken_micromamba() {
  if [[ -x "${HOME}/.local/bin/micromamba" ]] && ! "${HOME}/.local/bin/micromamba" --version &>/dev/null 2>&1; then
    echo "Removing broken micromamba at ~/.local/bin/micromamba (wrong platform)."
    rm -f "${HOME}/.local/bin/micromamba"
  fi
}

refresh_conda_prefix_path() {
  CONDA_PREFIX_PATH=""
  if command -v conda &>/dev/null; then
    CONDA_PREFIX_PATH="$(conda info --base 2>/dev/null)/envs/${ENV_NAME}"
  elif micromamba_usable; then
    local base
    base="$(micromamba info 2>/dev/null | grep 'base environment' | awk '{print $NF}')"
    CONDA_PREFIX_PATH="${base}/envs/${ENV_NAME}"
  fi
}

env_exists() {
  conda env list 2>/dev/null | awk '{print $1}' | grep -qx "${ENV_NAME}"
}

conda_run() {
  if command -v conda &>/dev/null && env_exists; then
    conda run -n "${ENV_NAME}" --no-capture-output "$@"
  elif micromamba_usable && [[ -n "${CONDA_PREFIX_PATH}" ]] && [[ -d "${CONDA_PREFIX_PATH}" ]]; then
    micromamba run -p "${CONDA_PREFIX_PATH}" "$@"
  else
    echo "ERROR: conda env ${ENV_NAME} not found; run ./install first" >&2
    return 1
  fi
}

pip_in_env() {
  conda_run python -m pip "$@"
}

wave_install() {
  remove_broken_micromamba
  refresh_conda_prefix_path
  # Prefer standalone micromamba (libmamba 2.x): fast solves into existing Miniforge envs.
  # Fall back to conda --solver classic when micromamba is missing; avoid conda's default
  # libmamba on conda 24.x (slow/hangy with strict channel_priority) and old mamba 1.5.x crashes.
  if micromamba_usable && [[ -n "${CONDA_PREFIX_PATH}" ]] && [[ -d "${CONDA_PREFIX_PATH}" ]]; then
    micromamba install -p "${CONDA_PREFIX_PATH}" --override-channels -c conda-forge -y "$@"
  elif command -v conda &>/dev/null; then
    conda install -n "${ENV_NAME}" --override-channels -c conda-forge -y --solver classic "$@"
  else
    echo "ERROR: need working micromamba or conda on PATH" >&2
    return 1
  fi
}

maybe_install_micromamba() {
  remove_broken_micromamba
  if micromamba_usable; then
    return 0
  fi
  if command -v conda &>/dev/null; then
    echo "Note: micromamba not on PATH — wave installs use conda --solver classic (slower)."
    echo "      Install fast solver: bash scripts/install_micromamba.sh"
    return 0
  fi
  echo "micromamba not found — installing standalone binary to ~/.local/bin ..."
  mkdir -p "${HOME}/.local/bin"
  local plat
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) plat="osx-arm64" ;;
    Darwin-x86_64) plat="osx-64" ;;
    Linux-aarch64) plat="linux-aarch64" ;;
    *) plat="linux-64" ;;
  esac
  if curl -Ls "https://micro.mamba.pm/api/micromamba/${plat}/latest" \
    | tar -xvj -C "${HOME}/.local/bin" --strip-components=1 bin/micromamba 2>/dev/null; then
    export PATH="${HOME}/.local/bin:${PATH}"
    echo "Installed micromamba $(micromamba --version 2>&1)"
  else
    echo "ERROR: could not install micromamba and conda is not available" >&2
    return 1
  fi
}

create_env() {
  if command -v conda &>/dev/null; then
    conda create -n "${ENV_NAME}" --override-channels -c conda-forge -y --solver classic python=3.11 pip
  elif micromamba_usable; then
    micromamba create -n "${ENV_NAME}" -c conda-forge -y python=3.11 pip
  else
    echo "ERROR: need conda or micromamba on PATH" >&2
    return 1
  fi
  refresh_conda_prefix_path
}

repair_hint() {
  echo "Repair: rm -f ~/.local/bin/micromamba && ./install" >&2
}

verify_conda_stack() {
  echo "Verifying conda simulation stack in ${ENV_NAME}..."
  conda_run python -c "
import shutil
import openmm
from openff.toolkit import Molecule
from rdkit.Chem import AllChem

packmol = shutil.which('packmol')
if not packmol:
    raise SystemExit('packmol not on PATH in conda env')
for exe in ('antechamber', 'parmchk2'):
    if not shutil.which(exe):
        raise SystemExit(f'{exe} not on PATH in conda env (install ambertools via ./install)')
print(
    'conda stack OK (openmm', openmm.__version__,
    ', packmol', packmol,
    ', antechamber', shutil.which('antechamber'), ')',
)
"
}
