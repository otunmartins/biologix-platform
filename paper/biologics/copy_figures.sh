#!/usr/bin/env bash
# Copy/generate figures for paper/biologics showcase PDF.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIG="$ROOT/paper/biologics/figures"
PY=""
if [[ -x "$HOME/miniforge3/envs/biologix-ai-sim/bin/python" ]]; then
  PY="$HOME/miniforge3/envs/biologix-ai-sim/bin/python"
fi
mkdir -p "$FIG"
cp "$ROOT/runs/insulin-stabilize-iter1/structures/polycarbonate_alt_lactide.png" \
   "$FIG/insulin_pc_alt_lactide.png"
if [[ -n "$PY" ]]; then
  "$PY" -c "
from rdkit import Chem
from rdkit.Chem import Draw
mol = Chem.MolFromSmiles('O=C(NC(=O)CC)CC')
Draw.MolToFile(mol, '${FIG}/adalimumab_amide_ketone.png', size=(420, 320))
"
  "$PY" "$ROOT/scripts/generate_biologics_showcase_chemviz.py"
fi
echo "Figures ready in $FIG"
