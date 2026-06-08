#!/usr/bin/env bash
# Biologix AI container entrypoint.
# - Activates biologix-ai-sim conda env.
# - Custom command (e.g. CI smoke test): exec "$@" — no OpenCode, no API keys.
# - Default: SLIM/first-run data init, then opencode . (MCP; auth via opencode auth login).
# - OPENCODE_DISABLE=1 drops to bash for debugging.

set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
conda activate biologix-ai-sim

# Prefer conda libstdc++/libgcc (GLIBCXX_3.4.29+) over the Debian base image.
# Without this, RetroSynAgent treeBuilder fails loading libLerc.so.4 via the MCP server.
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Headless matplotlib + RDKit drawing (psmiles savefig may otherwise write SVG to .png paths)
export MPLBACKEND=Agg

# Interactive safety profile for Docker/OpenCode sessions (override with docker run -e …)
export BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S="${BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S:-900}"
export BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS="${BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS:-1500}"
export BIOLOGIX_AI_EVAL_MAX_WORKERS="${BIOLOGIX_AI_EVAL_MAX_WORKERS:-1}"
export BIOLOGIX_PDF_TIMEOUT="${BIOLOGIX_PDF_TIMEOUT:-30}"
export BIOLOGIX_TREE_TIMEOUT="${BIOLOGIX_TREE_TIMEOUT:-90}"

# Non-interactive / CI / custom command: run the requested command, not OpenCode.
if [[ $# -gt 0 ]]; then
  cd /app
  exec "$@"
fi

# ── Data: seed from image when /app/data is an empty volume mount ─────────────
PRECURSOR_DB=/app/data/retrosynthesis/precursors.json
MOLPORT_DB=/app/data/retrosynthesis/molport_inchikeys.pkl
SEED=/app/.data-seed

if [[ -d "$SEED" ]]; then
  if [[ ! -f "$PRECURSOR_DB" && -f "$SEED/retrosynthesis/precursors.json" ]]; then
    echo "Seeding precursor database from image …"
    mkdir -p /app/data/retrosynthesis
    cp -a "$SEED/retrosynthesis/." /app/data/retrosynthesis/
  fi
  if [[ ! -f /app/data/aizynthfinder/config.yml && -f "$SEED/aizynthfinder/config.yml" ]]; then
    echo "Seeding AiZynthFinder models from image …"
    mkdir -p /app/data/aizynthfinder
    cp -a "$SEED/aizynthfinder/." /app/data/aizynthfinder/
  fi
fi

# ── SLIM / first run: initialise data when not baked into the image ───────────
if [[ ! -f /app/data/aizynthfinder/config.yml ]]; then
  echo "First run: downloading AiZynthFinder models (~800 MB) …"
  bash /app/scripts/setup_aizynthfinder.sh
fi

if [[ ! -f "$PRECURSOR_DB" ]]; then
  echo "First run: building precursor database (needs network) …"
  python /app/scripts/build_precursor_db.py --tiers 1,2,3,4
elif [[ ! -f "$MOLPORT_DB" ]]; then
  echo "First run: building Molport InChIKey cache (tier 3, needs network) …"
  python /app/scripts/build_precursor_db.py --tiers 3
fi

# ── Debug override ────────────────────────────────────────────────────────────
if [[ "${OPENCODE_DISABLE:-0}" == "1" ]]; then
  echo "OPENCODE_DISABLE=1 — dropping into bash. Run 'opencode .' to start manually."
  exec bash
fi

# ── Launch OpenCode (MCP tools; no API keys required at container start) ───────
# Configure a provider inside the session when needed: opencode auth login
cd /app
exec opencode .
