---
description: Biologics delivery discovery — human-in-the-loop MCP pipeline
---

# Biologics Discovery Skill (HITL)

Use the **`biologics-delivery-discovery`** agent (default). The platform is **human-in-the-loop only**: run Steps 1–6 per iteration, **always** Step 7 checkpoint, then wait for the user before Iteration 2+.

Full protocol: `.opencode/agent/biologics-delivery-discovery.md`

## Pipeline overview

1. **Onboard** — biologic target, polymer target (or "suggest")
2. **Session** — `resolve_biologic_target`, `start_biologics_session`
3. **Literature** — `mine_literature`, `validate_psmiles`
4. **Screen** — `screen_candidate_library`, `openmm_evaluate_psmiles`
5. **Retrosynthesis** — `prepare_retrosynthesis` → extract reactions → `submit_retro_extractions` → `plan_retrosynthesis` → ADMET/compliance
6. **Report** — `assemble_retrosynthesis_report`, `SUMMARY_REPORT.md`, PDF
7. **Iteration checkpoint** — `save_discovery_state`, transcript archive, ask user: **Iteration 2?** (refine via `mutate_psmiles` + feedback mining). **Stop until they answer.**

## Per-step rules

- Call MCP tools only; do not read `src/` or `scripts/` to work around failures.
- On `abort: true` or missing deps: stop and tell the user **Run `./install`**.
- `submit_retro_extractions` requires `Products:` to include the polymer name (capitalized field labels).

## Scripted benchmark (optional, not the main workflow)

For overnight subprocess runs without LLM planning (paper/benchmarks only):

- MCP: `run_autonomous_discovery(budget_minutes=..., run_in_background=true)`
- CLI: `python scripts/run_autonomous_discovery.py --budget-minutes 480`

Use the **`autoresearch-materials`** agent for that path, not the default biologics agent.
