---
description: Active learning feedback loop for materials discovery
---

# Active Learning Cycle Skill

The discovery loop is agent-orchestrated: you drive each step through individual tool calls. **Human steering happens between iterations, not in the middle of iteration 1.**

## Complete iteration 1 in one run

Unless the user asked for a subset (e.g. "only mine"):

- Run **mine → validate → evaluate → mutate → save → report → archive chat** (`import_chat_transcript_file` or `save_session_transcript`) in **one continuous flow** with **no** questions between steps.
- Pick **3–8** PSMILES yourself if many candidates exist; do not ask which to evaluate.
- Only stop mid-flow if a tool error cannot be fixed without user input.

## Loop

1. **Mine** – `mine_literature(query=..., iteration=N, ...)` (includes PaperQA2 when indexed)
2. **Translate** – Convert material names to PSMILES (polymer chemistry knowledge)
3. **Validate** – `validate_psmiles(psmiles, material_name="...")` for each (always pass the name; check `name_consistency.consistent`; fix PSMILES if false — see `docs/PSMILES_GUIDE.md`)
4. **Evaluate** – `openmm_evaluate_psmiles(psmiles_list)` — use a **comma-separated string** or **JSON array of strings** for `psmiles_list`. OpenMM Packmol matrix (requires **packmol** on PATH)
5. **Mutate** – `mutate_psmiles(feedback_json=...)` with high performers and problematic PSMILES from evaluation
6. **Save** – `save_discovery_state(iteration=N, feedback_json=..., query_used=..., notes=...)`
7. **Report** – Summarize to user; write `SUMMARY_REPORT.md` / PDF per `docs/SUMMARY_REPORT_STYLE.md`
8. **Archive chat** – **Required:** `import_chat_transcript_file` (read JSONL source path — often under `~/.cursor/.../agent-transcripts/` — and **copy into** `runs/<session>/`) or `save_session_transcript` (full recap **into** `runs/<session>/` **only**). **Never** treat `.cursor/` as the destination for the session archive. Every time.
9. **Refine** – Use feedback to build a better query for the next iteration
10. Repeat from step 1

## Feedback Flow

MD evaluation returns:
- `high_performers` -- materials with good stability metrics
- `effective_mechanisms` -- what's working (hydrogen bonding, hydrophobic interactions, etc.)
- `problematic_features` -- what to avoid (high crystallinity, poor water retention, etc.)

Feed these back into `mine_literature` and `mutate_psmiles` to narrow the search.

## Bash Fallback

Use MCP `run_autonomous_discovery` or orchestrate `mine_literature` / `openmm_evaluate_psmiles` / `mutate_psmiles`.

## Results

- `discovery_state/` -- per-iteration state (agent loop)
- `cycle_results/` -- batch CLI results
- `iterative_results/` -- per-iteration mining

## Summary report (human-readable)

The **agent** writes `SUMMARY_REPORT.md` in the session run folder, embedding **`structures/`** monomer and complex PNGs from evaluation (`*_monomer.png`, `*_complex_preview.png`, `*_complex_chemviz.png`; see **`docs/SUMMARY_REPORT_STYLE.md`**), using **`render_psmiles_png`** only when needed for extra 2D figures, then **`compile_discovery_markdown_to_pdf`** for the PDF. Optional **`write_discovery_summary_report`** auto-builds a skeleton from JSON and embeds those evaluate-style PNGs if present. **Always** archive the conversation into the **session run folder** (same as SUMMARY_REPORT / iteration outputs) via **`import_chat_transcript_file`** or **`save_session_transcript`** — **not** under `.cursor/` (required by default).

**Prose and citations:** follow **`docs/SUMMARY_REPORT_STYLE.md`** (research-paper structure; references with journal abbrev., volume, pages, year; avoid em dashes, colon chains, and common LLM filler patterns). See also `docs/DEPENDENCIES.md` (MCP — discovery figures & PDF reports).
