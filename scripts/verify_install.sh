#!/usr/bin/env bash
# Verify biologix-ai install completeness. Exit 1 on failure.
#
# Usage:
#   bash scripts/verify_install.sh              # full check
#   bash scripts/verify_install.sh --conda-only # OpenMM/Packmol/OpenFF/RDKit only
#   bash scripts/verify_install.sh --skip-submodules --skip-aizynth-models

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

CONDA_ONLY=false
SKIP_SUBMODULES="${VERIFY_SKIP_SUBMODULES:-false}"
SKIP_AIZYNTH_MODELS="${VERIFY_SKIP_AIZYNTH_MODELS:-false}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --conda-only) CONDA_ONLY=true; shift ;;
    --skip-submodules) SKIP_SUBMODULES=true; shift ;;
    --skip-aizynth-models) SKIP_AIZYNTH_MODELS=true; shift ;;
    -h|--help)
      echo "Usage: bash scripts/verify_install.sh [--conda-only] [--skip-submodules] [--skip-aizynth-models]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if ! env_exists; then
  echo "FAIL: conda env ${ENV_NAME} does not exist" >&2
  repair_hint
  exit 1
fi

refresh_conda_prefix_path
export PYTHONPATH="${REPO_ROOT}/src/python${PYTHONPATH:+:${PYTHONPATH}}"

failures=0
pass() { echo "  OK: $1"; }
fail() { echo "  FAIL: $1" >&2; failures=$((failures + 1)); }

echo "=== Verifying biologix-ai install (${ENV_NAME}) ==="

# Conda simulation stack
if conda_run python -c "
import shutil
import openmm
from openff.toolkit import Molecule
from rdkit.Chem import AllChem
assert shutil.which('packmol'), 'packmol not on PATH'
assert shutil.which('antechamber'), 'antechamber not on PATH (conda-forge ambertools)'
assert shutil.which('parmchk2'), 'parmchk2 not on PATH (conda-forge ambertools)'
" 2>/dev/null; then
  pass "conda stack (openmm, packmol, openff, rdkit, ambertools)"
else
  fail "conda stack (openmm, packmol, openff, rdkit, ambertools)"
fi

if [[ "$CONDA_ONLY" == "true" ]]; then
  [[ "$failures" -eq 0 ]] && echo "=== All conda checks passed ===" && exit 0
  repair_hint
  exit 1
fi

# MCP + openmm_available
if conda_run python -c "
import sys
sys.path.insert(0, '${REPO_ROOT}')
import biologix_ai_mcp_server  # noqa: F401
from biologix_ai.simulation.openmm_compat import openmm_available
assert openmm_available(), 'openmm_available() is False'
" 2>/dev/null; then
  pass "MCP server import + openmm_available()"
else
  fail "MCP server import + openmm_available()"
fi

if [[ "$SKIP_SUBMODULES" != "true" ]]; then
  if conda_run python -c "
import sys
sys.path.insert(0, '${REPO_ROOT}/extern/RetroSynthesisAgent')
sys.path.insert(0, '${REPO_ROOT}/src/python')
from biologix_ai.retrosynthesis.retrosyn_bootstrap import ensure_retrosyn_agent_ready
ensure_retrosyn_agent_ready()
from RetroSynAgent.treeBuilder import Tree
result_dict = {
    'test': (
        'Reaction 001:\n'
        'Reactants: acrylic acid\n'
        'Products: poly(acrylic acid)\n'
        'Conditions: RAFT'
    ),
}
tree = Tree('poly(acrylic acid)', result_dict=result_dict)
assert len(tree.reactions) >= 1, 'RetroSyn parse produced no reactions'
" 2>/dev/null; then
    pass "RetroSynthesisAgent (bootstrap + tree parse)"
  else
    fail "RetroSynthesisAgent (bootstrap + tree parse)"
  fi

  # Precursor database smoke test: Tier 1+2 critical names
  if conda_run python -c "
import sys
sys.path.insert(0, '${REPO_ROOT}/src/python')
from biologix_ai.retrosynthesis.precursor_registry import (
    get_bundled_precursors, reload_bundled_precursors
)
reload_bundled_precursors()
bundled = get_bundled_precursors()
missing = [n for n in ('lactide','glycolide','chitin','lactic acid','ethylene','carbon monoxide','aibn') if n not in bundled]
assert not missing, f'Missing from precursor DB: {missing}'
" 2>/dev/null; then
    pass "Precursor database Tier 1+2 (lactide, glycolide, chitin, CO, AIBN)"
  else
    fail "Precursor database Tier 1+2 (run: python scripts/build_precursor_db.py --tiers 1,2)"
  fi

  # Tier 3: Molport InChIKey pkl
  if [[ -f "${REPO_ROOT}/data/retrosynthesis/molport_inchikeys.pkl" ]]; then
    if conda_run python -c "
import sys, pickle
sys.path.insert(0, '${REPO_ROOT}/src/python')
with open('${REPO_ROOT}/data/retrosynthesis/molport_inchikeys.pkl','rb') as f:
    keys = pickle.load(f)
assert len(keys) > 10000, f'Expected >10k InChIKeys, got {len(keys)}'
print(f'Molport InChIKey set: {len(keys):,} entries')
" 2>/dev/null; then
      pass "Precursor database Tier 3 (Molport InChIKey set)"
    else
      fail "Precursor database Tier 3 pkl corrupted (re-run: python scripts/build_precursor_db.py --tiers 3)"
    fi
  else
    fail "Precursor database Tier 3 missing (run: python scripts/build_precursor_db.py --tiers 3)"
  fi

  # Tier 4: ZINC bridge (h5py + zinc_stock.hdf5)
  # zinc_stock.hdf5 is a Pandas HDFStore; 'table/axis1' holds the row index
  if conda_run python -c "
import h5py, sys
with h5py.File('${REPO_ROOT}/data/aizynthfinder/zinc_stock.hdf5', 'r') as f:
    n = int(f['table']['axis1'].shape[0])
assert n > 1_000_000, f'Expected >1M ZINC InChIKeys, got {n}'
print(f'ZINC bridge: {n:,} InChIKeys')
" 2>/dev/null; then
    pass "Precursor database Tier 4 (ZINC InChIKey bridge, h5py)"
  else
    fail "Precursor database Tier 4 (run: pip install h5py && bash scripts/setup_aizynthfinder.sh)"
  fi

  if conda_run python -c "from aizynthfinder.aizynthfinder import AiZynthFinder" 2>/dev/null; then
    pass "AiZynthFinder package"
  else
    fail "AiZynthFinder package"
  fi

  if conda_run python -c "from admet_ai import ADMETModel" 2>/dev/null; then
    pass "ADMET-AI"
  else
    fail "ADMET-AI"
  fi
fi

if [[ "$SKIP_AIZYNTH_MODELS" != "true" ]] && [[ "$SKIP_SUBMODULES" != "true" ]]; then
  if [[ -f "${REPO_ROOT}/data/aizynthfinder/config.yml" ]]; then
    pass "AiZynthFinder models (data/aizynthfinder/config.yml)"
  else
    fail "AiZynthFinder models (run: bash scripts/setup_aizynthfinder.sh)"
  fi
fi

if [[ "$failures" -eq 0 ]]; then
  echo "=== All checks passed ==="
  exit 0
fi

echo "=== ${failures} check(s) failed ===" >&2
repair_hint
exit 1
