#!/usr/bin/env bash
# Drop legacy "omnia" from conda channel list (it breaks packmol / libgfortran solves).
# Run from repo root:  bash scripts/fix_conda_channels_for_insulin_ai.sh
#
# Then update the env (run each line separately in your shell):
#   conda config --set channel_priority flexible
#   conda env update -f environment-simulation.yml --prune --solver classic

set -euo pipefail

if ! command -v conda &>/dev/null; then
  echo "conda not found on PATH" >&2
  exit 1
fi

echo "Removing channel 'omnia' until it no longer appears (repeat-safe)..."
removed=0
while conda config --show channels 2>/dev/null | grep -Fqi omnia; do
  if ! conda config --remove channels omnia 2>/dev/null; then
    echo "Could not remove omnia automatically. Edit ~/.condarc and delete the omnia entry." >&2
    exit 1
  fi
  removed=$((removed + 1))
  if [[ "$removed" -gt 20 ]]; then
    echo "Too many remove attempts; check ~/.condarc manually." >&2
    exit 1
  fi
done

if [[ "$removed" -eq 0 ]]; then
  echo "No 'omnia' channel found (nothing to do)."
else
  echo "Removed omnia ($removed time(s))."
fi

echo ""
echo "Current channels:"
conda config --show channels

echo ""
REPO="$(cd "$(dirname "$0")/.." && pwd)"
echo "Next — pick one:"
echo "  A) Default (chunked conda + pip):  ./install   or   bash \"$REPO/scripts/install_insulin_ai_sim_lowmem.sh\""
echo "  B) Full YAML solve (needs RAM):     ./install --conda-yml"
echo "       or: conda config --set channel_priority flexible"
echo "           conda env update -f \"$REPO/environment-simulation.yml\" --prune --solver classic"
