---
description: Active learning feedback loop for materials discovery
---

# Active Learning Cycle Skill

The discovery loop is agent-orchestrated via **`biologics-delivery-discovery`**. **Human steering happens at Step 7 (iteration checkpoint), not in the middle of Steps 1–6.**

## Complete each iteration in one run

Unless the user asked for a subset (e.g. "only mine"):

- Run **Steps 1–6** (mine → validate → screen → OpenMM → retro → report) in **one continuous flow** with **no** questions between steps.
- Pick **3–8** PSMILES yourself if many candidates exist; do not ask which to evaluate.
- Only stop mid-flow if a tool error cannot be fixed without user input.
- **Always finish with Step 7:** `save_discovery_state`, archive chat, then ask whether to run **Iteration 2** (see `.opencode/agent/biologics-delivery-discovery.md`).

## Loop

1. **Mine** – `mine_literature(query=..., iteration=N, ...)` (includes PaperQA2 when indexed)
2. **Translate** – Convert material names to PSMILES (polymer chemistry knowledge)
3. **Validate** – `validate_psmiles(psmiles, material_name="...")` for each (always pass the name; check `name_consistency.consistent`; fix PSMILES if false — see `docs/PSMILES_GUIDE.md`)
4. **Screen** – `screen_candidate_library`, then `openmm_evaluate_psmiles(psmiles_list)` (comma-separated or JSON array; requires **packmol** on PATH for matrix builds)
5. **Retrosynthesis** – full Step 5 from `biologics-delivery-discovery` (prepare → extract → submit → plan → ADMET/compliance)
6. **Report** – `assemble_retrosynthesis_report`, `SUMMARY_REPORT.md` / PDF per `docs/SUMMARY_REPORT_STYLE.md`
7. **Checkpoint (Step 7)** – `save_discovery_state`, `save_funnel_context`, archive chat (`import_chat_transcript_file` or `save_session_transcript`), **ask user: Iteration N+1?** — **stop until they answer**
8. **Refine (if user accepts)** – `mutate_psmiles(feedback_json=...)` and/or `mine_literature(iteration=N+1, top_candidates=..., ...)`
9. Repeat from Step 3 on the same `run_dir`

## Feedback Flow

MD evaluation returns:
- `high_performers` -- materials with good stability metrics
- `effective_mechanisms` -- what's working (hydrogen bonding, hydrophobic interactions, etc.)
- `problematic_features` -- what to avoid (high crystallinity, poor water retention, etc.)

Feed these back into `mine_literature` and `mutate_psmiles` to narrow the search.

## Bash Fallback (benchmark only)

For scripted overnight runs without LLM planning, use MCP `run_autonomous_discovery` or the CLI in `scripts/run_autonomous_discovery.py`. For normal biologics campaigns, follow the **`biologics-delivery-discovery`** agent pipeline step-by-step instead.

## Results

- `discovery_state/` -- per-iteration state (agent loop)
- `cycle_results/` -- batch CLI results
- `iterative_results/` -- per-iteration mining

## Summary report (human-readable)

The **agent** writes `SUMMARY_REPORT.md` in the session run folder, embedding **`structures/`** monomer and complex PNGs from evaluation (`*_monomer.png`, `*_complex_preview.png`, `*_complex_chemviz.png`; see **`docs/SUMMARY_REPORT_STYLE.md`**), using **`render_psmiles_png`** only when needed for extra 2D figures, then **`compile_discovery_markdown_to_pdf`** for the PDF. Optional **`write_discovery_summary_report`** auto-builds a skeleton from JSON and embeds those evaluate-style PNGs if present. **Always** archive the conversation into the **session run folder** (same as SUMMARY_REPORT / iteration outputs) via **`import_chat_transcript_file`** or **`save_session_transcript`** — **not** under `.cursor/` (required by default).

**Prose and citations:** follow **`docs/SUMMARY_REPORT_STYLE.md`** (research-paper structure; references with journal abbrev., volume, pages, year; avoid em dashes, colon chains, and common LLM filler patterns). See also `docs/DEPENDENCIES.md` (MCP — discovery figures & PDF reports).
