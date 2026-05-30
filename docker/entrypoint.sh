#!/usr/bin/env bash
# Biologix AI container entrypoint.
# - Activates biologix-ai-sim conda env.
# - Warns if no LLM provider key is set (still opens OpenCode — user can auth inside).
# - Handles SLIM-mode first-run data initialisation.
# - Execs opencode . (or bash if OPENCODE_DISABLE=1 for debugging).

set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
conda activate biologix-ai-sim

# ── LLM key check ────────────────────────────────────────────────────────────
HAS_KEY=false
for var in ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY; do
  if [[ -n "${!var:-}" ]]; then
    HAS_KEY=true
    break
  fi
done

if [[ "$HAS_KEY" == "false" ]]; then
  echo ""
  echo "╔════════════════════════════════════════════════════════════════════╗"
  echo "║  No LLM provider key found.                                       ║"
  echo "║  Set one in your .env file (see .env.example) or pass it with:   ║"
  echo "║    docker run -e ANTHROPIC_API_KEY=sk-ant-...                     ║"
  echo "║  You can also run  opencode auth login  inside this session.      ║"
  echo "╚════════════════════════════════════════════════════════════════════╝"
  echo ""
fi

# ── SLIM: initialise data on first run ───────────────────────────────────────
if [[ ! -f /app/data/aizynthfinder/config.yml ]]; then
  echo "First run: downloading AiZynthFinder models (~800 MB) …"
  bash /app/scripts/setup_aizynthfinder.sh
fi

PRECURSOR_DB=/app/src/python/biologix_ai/data/precursors.json
if [[ ! -f "$PRECURSOR_DB" ]]; then
  echo "First run: building precursor database (needs network) …"
  python /app/scripts/build_precursor_db.py --tiers 1,2,3,4
fi

# ── Debug override ────────────────────────────────────────────────────────────
if [[ "${OPENCODE_DISABLE:-0}" == "1" ]]; then
  echo "OPENCODE_DISABLE=1 — dropping into bash. Run 'opencode .' to start manually."
  exec bash
fi

# ── Launch OpenCode with the biologics-delivery-discovery agent ───────────────
cd /app
exec opencode .
