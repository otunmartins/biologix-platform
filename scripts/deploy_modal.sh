#!/usr/bin/env bash
# Deploy the Biologix MCP app to Modal, then run the image verifier against the
# deployed image. The base image rebuilds only when a dependency input changes
# (see modal_app.py); code-only changes deploy in minutes.
#
#   scripts/deploy_modal.sh                 # deploy + verify_runtime
#   scripts/deploy_modal.sh --skip-verify   # deploy only
#   BIOLOGIX_GPU=A10G scripts/deploy_modal.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

modal deploy modal_app.py
if [[ "${1:-}" != "--skip-verify" ]]; then
  modal run modal_app.py::verify_runtime
fi
