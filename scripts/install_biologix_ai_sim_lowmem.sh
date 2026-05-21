#!/usr/bin/env bash
# Reliable conda env installer: conda-first waves + pip; fail-fast on partial installs.
#
# Usage (from repo root):
#   ./install                    # default conda path runs this script
#   bash scripts/install_biologix_ai_sim_lowmem.sh
#
# Requires: conda (preferred) or working micromamba on PATH.
# Remove legacy "omnia" channel first — see scripts/fix_conda_channels_for_biologix_ai.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

if conda config --show channels 2>/dev/null | grep -Fqi omnia; then
  echo "ERROR: conda still lists channel 'omnia'. Run:  bash scripts/fix_conda_channels_for_biologix_ai.sh" >&2
  exit 1
fi

cd "$REPO_ROOT"

maybe_install_micromamba || true
refresh_conda_prefix_path

if ! env_exists; then
  echo "Creating env ${ENV_NAME} (python 3.11 + pip)..."
  create_env
else
  echo "Env ${ENV_NAME} exists — adding missing conda packages in waves."
  refresh_conda_prefix_path
fi

check_conda_pkg() {
  local pkg="$1"
  conda list -n "${ENV_NAME}" "${pkg}" 2>/dev/null | grep -qE "^${pkg}[[:space:]]" || return 1
}

echo "Wave 1/5: openmm, pdbfixer, packmol..."
wave_install openmm pdbfixer packmol
for pkg in openmm pdbfixer packmol; do
  if ! check_conda_pkg "${pkg}"; then
    echo "ERROR: Wave 1 failed — ${pkg} not installed in ${ENV_NAME}" >&2
    repair_hint
    exit 1
  fi
done

echo "Wave 2/5: rdkit (conda; remove pip rdkit first)..."
pip_in_env uninstall -y rdkit rdkit-pypi 2>/dev/null || true
wave_install rdkit
if ! check_conda_pkg rdkit; then
  echo "ERROR: Wave 2 failed — rdkit not installed in ${ENV_NAME}" >&2
  repair_hint
  exit 1
fi

echo "Wave 3/5: OpenFF toolkit + units..."
if ! wave_install "openff-units>=0.2" "openff-toolkit-base>=0.18.0" 2>/dev/null; then
  echo "Retrying OpenFF wave with minimal packages..."
  wave_install "openff-units>=0.2" || {
    echo "ERROR: Wave 3 failed — openff-units not installed" >&2
    repair_hint
    exit 1
  }
  wave_install "openff-toolkit-base>=0.18.0" || {
    echo "ERROR: Wave 3 failed — openff-toolkit-base not installed" >&2
    repair_hint
    exit 1
  }
fi
if ! check_conda_pkg openff-toolkit-base && ! check_conda_pkg openff-toolkit; then
  echo "ERROR: Wave 3 failed — no OpenFF toolkit package in ${ENV_NAME}" >&2
  repair_hint
  exit 1
fi

echo "Wave 4/5: AmberTools (antechamber + parmchk2 for GAFF templates)..."
echo "      Large conda solve — install fast solver first: bash scripts/install_micromamba.sh"
if ! wave_install "ambertools>=24.8=*nompi*" 2>/dev/null; then
  wave_install ambertools
fi
if ! check_conda_pkg ambertools; then
  echo "ERROR: Wave 4 failed — ambertools not installed in ${ENV_NAME}" >&2
  repair_hint
  exit 1
fi
conda_run python -c "
import shutil
for exe in ('antechamber', 'parmchk2'):
    if not shutil.which(exe):
        raise SystemExit(f'{exe} not on PATH in conda env')
" || {
  echo "ERROR: Wave 4 failed — antechamber/parmchk2 not on PATH" >&2
  repair_hint
  exit 1
}

echo "Wave 5/5: pip packages + editable biologix-ai..."
pip_in_env install -U pip setuptools wheel
pip_in_env install -r "$REPO_ROOT/requirements.txt"
pip_in_env install -e "$REPO_ROOT[retro,admet,dev]"
pip_in_env install -U "pydantic>=2.10" "pydantic-core>=2.27"

verify_conda_stack

echo ""
echo "Done. Activate: conda activate ${ENV_NAME}"
