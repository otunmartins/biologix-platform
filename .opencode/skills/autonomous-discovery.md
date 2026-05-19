---
description: Run materials discovery autonomously, step by step
---

# Autonomous Discovery Skill

When the user asks to discover materials (e.g. "discover materials for fridge-free insulin patches"), **start immediately without asking for confirmation**. You orchestrate the loop yourself through individual tool calls.

## Mode selection

At the start, ask the user which mode they want (or infer from their prompt):

1. **Autonomous** — run N iterations continuously without pausing. Same tool-call sequence per iteration, but you decide query refinements and error recovery yourself.
2. **Human-in-the-loop** — complete one iteration, report, then wait for feedback.

Full details (tool-call sequence, per-iteration persistence, early stopping, final report) are in the **materials-discovery** agent instructions under **"Autonomous mode rules"** and **"Discovery Protocol"**.

## Protocol (per iteration, both modes)

1. `mine_literature(query=..., iteration=N, top_candidates=..., stability_mechanisms=..., limitations=...)` — includes PaperQA2 synthesis when papers indexed
2. Translate returned material names to PSMILES (use chemistry knowledge, `generate_psmiles_from_name`, `lookup_material`, or `web_search`)
3. `validate_psmiles(psmiles, material_name=...)` for each; fix failures
4. `openmm_evaluate_psmiles(psmiles_list)` — MD evaluation
5. `mutate_psmiles(feedback_json=...)` — generate variants of high performers; evaluate those too
6. `save_discovery_state(iteration=N, feedback_json=..., query_used=..., notes=...)` — include **all** evaluated candidates with energies in `high_performers` (not just top 3)
7. Update `ALL_ITERATIONS_BEST_CANDIDATES.tsv` in the session folder (best candidate per iteration, all iterations so far)
8. Update `SUMMARY_REPORT.md` (cumulative) and `compile_discovery_markdown_to_pdf`
9. Archive transcript (`import_chat_transcript_file` or `save_session_transcript`)

**Autonomous mode:** after step 8, proceed directly to the next iteration (load state, refine query, mine again). Do not pause or ask for input.

**Human-in-the-loop mode:** after step 8, report results and wait for user input before proceeding.

### Iteration 1

Use broad queries: `"hydrogels insulin delivery transdermal"`, `"polymer protein stabilization thermal"`.

### Iteration 2+

Load `load_discovery_state(iteration=N-1)`. Refine the query based on high performers and mechanisms. Incorporate any user directions (human-in-the-loop) or your own reasoning (autonomous).

### Stopping

- **Human-in-the-loop:** run up to 5 iterations. Stop early if the user says stop or no new high performers appear.
- **Autonomous:** run exactly N iterations (user-specified, default 5). Early-stop if 2 consecutive iterations produce no new high performer and the candidate pool is saturated.

After the final iteration, produce a cumulative summary across all iterations and archive the chat.

## Scripted fallback (no LLM reasoning)

For maximum throughput without LLM planning, use the scripted loop:
- MCP: `run_autonomous_discovery(budget_minutes=..., run_in_background=true)`
- CLI: `python scripts/run_autonomous_discovery.py --budget-minutes 480`

This uses a fixed pipeline (Scholar mine -> mutate -> OpenMM evaluate) with no LLM reasoning between steps.

## Results

- `runs/<session_id>/` — per-iteration state, TSV, reports, structures
