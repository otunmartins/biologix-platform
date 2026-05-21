#!/usr/bin/env bash
# Install or refresh standalone micromamba (libmamba 2.x) to ~/.local/bin.
# Used by ./install for fast conda-forge wave solves into biologix-ai-sim.
#
# Usage: bash scripts/install_micromamba.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=install_lib.sh
source "$SCRIPT_DIR/install_lib.sh"

remove_broken_micromamba

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) plat="osx-arm64" ;;
  Darwin-x86_64) plat="osx-64" ;;
  Linux-aarch64) plat="linux-aarch64" ;;
  *) plat="linux-64" ;;
esac

mkdir -p "${HOME}/.local/bin"
echo "Downloading micromamba (${plat}) to ~/.local/bin/micromamba ..."
if curl -Ls "https://micro.mamba.pm/api/micromamba/${plat}/latest" \
  | tar -xvj -C "${HOME}/.local/bin" --strip-components=1 bin/micromamba; then
  chmod +x "${HOME}/.local/bin/micromamba"
  export PATH="${HOME}/.local/bin:${PATH}"
  echo "Installed: $(micromamba --version 2>&1)"
  echo "Ensure ~/.local/bin is on your PATH, then re-run: ./install"
else
  echo "ERROR: download failed" >&2
  exit 1
fi
