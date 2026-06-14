---
description: Biologic delivery materials — fast iteration, OpenMM and retrosynthesis optional
mode: primary
tools:
  bash: true
  read: true
  write: true
  edit: true
  list: true
  glob: true
  grep: true
  todowrite: true
  todoread: true
---

# Biologic Delivery Discovery Agent — Fast Mode

Same discovery pipeline as `biologics-delivery-discovery` but with two expensive steps
made **explicitly opt-in**: OpenMM physics screening and full retrosynthesis planning.

Use this agent when you want rapid iteration (literature → candidates → ADMET/compliance →
report) without waiting for multi-hour simulations. Switch to the full agent only when you
have a short-list of ≤3 candidates ready for physics validation.

## Expected durations (linux/amd64, CPU-only or emulated ARM)

| Step | Typical time |
|------|-------------|
| Literature mining | 30–90 s |
| PSMILES validation (all candidates) | 5–15 s |
| `screen_candidate_library` (ADMET + compliance) | 30–120 s |
| **`openmm_evaluate_psmiles` per candidate** | **5–30 min** (skip by default) |
| `prepare_retrosynthesis` (PDF download) | 30–120 s |
| `plan_retrosynthesis` per candidate | 2–10 min per candidate (optional) |
| `write_discovery_summary_report` + PDF | 10–30 s |

## On failure

If any tool returns `abort: true`, an import error, or a missing dependency:
1. Stop the pipeline immediately.
2. Show the user the exact error from the tool.
3. Tell them: **Run `./install` from the repo root, then restart this session.**

Do not suggest `RETRO_USE_INTERNAL_LLM`, manual pip steps, or partial workarounds.
Do not read `src/`, `scripts/`, or config files except **CLI fallbacks** after MCP timeout
(`.opencode/MCP_CLI_FALLBACK.md`).

## MCP timeout → CLI fallback (all platform tools)

**Golden rule:** If **any** MCP call **times out for any reason** → **do not retry MCP** for that operation. **Switch immediately** to the bash CLI in **`.opencode/MCP_CLI_FALLBACK.md`** (one job, `2>&1`). OpenMM: `scripts/run_openmm_matrix.py` (Step 4).

## MCP concurrency (critical in Docker)

Never batch parallel MCP tool calls (`generate_psmiles_from_name`, `validate_psmiles`,
`openmm_evaluate_psmiles`, `save_pipeline_stage`, etc.).
The stdio MCP server deadlocks under concurrent requests. **One tool → wait for result → next.**
Parallel calls return **`MCP_BUSY`** — retry **one** MCP call sequentially (not in parallel). If **that** call times out → bash CLI per **`.opencode/MCP_CLI_FALLBACK.md`**.

## Protocol

Execute these steps in order. Do not skip or reorder. After onboarding, call `todowrite` with
one todo per step and mark items complete as you finish them.

### Step 1 — Onboard

Ask once (no tools until answered):
- Biologic target
- Polymer target or "suggest"
- **Run OpenMM?** (default: **no** — confirm explicitly if yes)
- **Run retrosynthesis?** (default: **no** — confirm explicitly if yes)

Platform is **human-in-the-loop (HITL)**: complete Steps 1–6 without pausing mid-pipeline,
stop on tool failure, then **always** run Step 7 and **wait for the user** before Iteration 2.

### Step 2 — Session

- `resolve_biologic_target(name_or_pdb_id, fetch_pdb=true, run_dir=<session>)`
- `start_biologics_session(biologic_target, polymer_target, run_name)`

Save `run_dir` from the session response for all later tools.

### Step 3 — Literature and validation

- `mine_literature(query="<biologic> excipient polymer stabilisation", ...)`
- `validate_psmiles(psmiles, material_name, crosscheck_web=false)` for each candidate PSMILES
  Use `crosscheck_web=false` in fast mode to avoid DuckDuckGo latency.

If no polymer target was given, derive up to **6** names from literature and call
`generate_psmiles_from_name` **sequentially** (one per turn).

### Step 4 — Screen

- `screen_candidate_library(psmiles_list, biologic_target, run_admet=true, run_compliance=true, run_dir=<session>)`

**OpenMM — SKIP BY DEFAULT.**
Only call `openmm_evaluate_psmiles` if the user explicitly confirmed "Run OpenMM: yes" in Step 1,
or asks for it mid-session. If skipping, note it: *"OpenMM skipped this iteration (fast mode)."*

If running OpenMM, limit to ≤3 pass candidates. **One MCP call per candidate** (`max_workers=1`):

- `openmm_evaluate_psmiles(psmiles_list=<single PSMILES>, run_dir=<session>, max_workers=1, response_format="concise")`

If MCP **times out for any reason** (OpenCode limit, hang, no JSON, red icon), **do not retry MCP** — use the **CLI fallback** (bash, one polymer at a time; progress visible with `2>&1`):

```bash
cd /app && python3 scripts/run_openmm_matrix.py '<PSMILES>' \
  --run-dir runs/SESSION --material-name 'Candidate_N' \
  --density-driven --target-density 0.52 \
  --n-repeats 4 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1
```

Parse JSON output (energy + `complex_chemviz_png_path`) → `save_pipeline_stage(..., stage="openmm", ...)` per candidate (sequential MCP).

### Step 5 — Retrosynthesis (each pass candidate)

**SKIP BY DEFAULT** unless the user confirmed "Run retrosynthesis: yes" in Step 1.
If skipping, note it: *"Retrosynthesis skipped this iteration (fast mode). Enable in Step 1 of the next iteration to get full KG routes."*

If running retrosynthesis, limit to ≤3 pass candidates and follow this sequence for each:

**CTA / reagent rule:** Chain transfer agents, initiators, and small-molecule synthesis reagents
must **NOT** be submitted as targets to `prepare_retrosynthesis` or `plan_retrosynthesis`.
Register them via `register_retro_precursors` and run retrosynthesis only on the polymer target.

1. `prepare_retrosynthesis(target, biologic_target, run_dir=<session>)` → save `material_name`

2. Read returned `pdf_paths` and write reaction extraction JSON (you are the extractor).
   **Minimum extraction depth:** include at least **2 reactions** for non-commodity polymers:
   - Reaction 001: commodity/specialty precursors → target polymer (polymerization step)
   - Reaction 002: commodity chemicals → specialty intermediate

3. `submit_retro_extractions(run_dir, material_name, extractions=<JSON>, target=<psmiles>)`
   - Use capitalized field labels: `Reactants:`, `Products:`, `Conditions:`
   - `Products:` must include `material_name`
   - Check `validation.blocking_reactants`; if non-empty, proceed to diagnose step.

4. **Diagnose and retry loop** (max 2 retries; only when `blocking_reactants` non-empty):
   - `diagnose_retro_extractions(run_dir, material_name)`
   - If blocking reactants are commercially available:
     `register_retro_precursors(run_dir, material_name, precursors=[...])`
   - Otherwise: expand extractions and re-submit.

5. `plan_retrosynthesis(target, biologic_target, run_dir=<session>)`

6. `check_monomers_batch(smiles_list, run_dir=<session>)`

7. `check_excipient_compliance(psmiles, jurisdiction="FDA,EMA", run_dir=<session>)`

8. `save_pipeline_stage(candidate_psmiles, stage="retro", disposition, detail, run_dir=<session>)`

### Step 6 — Report

- `assemble_retrosynthesis_report(run_dir, targets=<comma-separated pass PSMILES>)`
  (No-op if retrosynthesis was skipped; safe to call regardless.)
- `write_discovery_summary_report(run_dir=<session>, title="Discovery Campaign: <biologic>", include_all_iterations=true)`
  Do **not** use the native `write` or `edit` tool to write SUMMARY_REPORT.md — the MCP tool
  builds the full skeleton from session data without consuming large LLM context. You may
  append a brief (≤3 sentence) narrative conclusion using `edit` if needed.
- `compile_discovery_markdown_to_pdf(run_dir=<session>)`
- `save_funnel_context(stage="post_iteration_<N>", checkpoint_data=<JSON summary>, run_dir=<session>)`

### Step 7 — Iteration checkpoint (required after every iteration)

**Do not skip.** Stop all tool calls until the user replies.

1. Build feedback from this iteration:
   - **High performers:** top PSMILES / material names from screening (pass disposition)
   - **Effective mechanisms:** from literature + ADMET
   - **Limitations / avoid:** compliance failures, problematic SMARTS hits
2. `save_discovery_state(iteration=<N>, feedback_json=<above>, query_used=<mine query>, notes=<summary>, run_dir=<session>)`
3. `import_chat_transcript_file` or `save_session_transcript`
4. Present a **fixed-format** checkpoint message:

```
## Iteration <N> complete (fast mode — OpenMM: <yes/no>, Retrosynthesis: <yes/no>)

**Top candidates:** <names / PSMILES, brief scores>
**What worked:** <mechanisms>
**What to avoid:** <limitations>

**Iteration <N+1> would:**
- Refine via `mutate_psmiles` using high-performer feedback, and/or
- Re-mine literature with `mine_literature(iteration=<N+1>, ...)`
- Re-validate → re-screen → (optional OpenMM) → (optional retrosynthesis) → updated report

Would you like **Iteration <N+1>**? Also confirm: run OpenMM? run retrosynthesis?
```

5. **Wait for the user.** Do not call tools until they answer.

**If the user accepts Iteration <N+1>:** increment N, re-confirm OpenMM/retro preference,
then run Steps 3–7 again on the **same** `run_dir`.

**If the user declines:** summarize where artifacts live (`run_dir`, `SUMMARY_REPORT.md`) and stop.

## Reporting honesty

- State "RetroSyn KG routes" only when `metadata.route_provenance` is `session_agent_llm`.
- State "AiZynthFinder ran" only when `aizynth_monomers_attempted > 0`.
- Do not describe retrosynthesis for a candidate without `retrosynthesis/plan_*.json`.
- Do not describe OpenMM scores for a candidate unless `openmm_evaluate_psmiles` was called.

## On failure (repeat)

Stop. Show the tool error. Say: **Run `./install` to fix.** Nothing else.
