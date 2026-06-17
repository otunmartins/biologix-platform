# MCP timeout → CLI fallback (platform rule)

Apply to **every** `biologix-ai` MCP tool call in OpenCode/Docker sessions.

## Golden rule — session latch

**If any MCP tool call times out for any reason**, the session **latches to CLI-only mode** for **all remaining steps** in that run (current iteration and any later steps). Do **not** return to MCP for any tool — not even for steps that worked via MCP earlier, and not for "instant" audit saves.

Timeout-like symptoms (all latch the session):

- OpenCode red icon / `mcp_timeout` / `-32001` / "Request timed out"
- Tool hangs with no JSON while the same work via bash would show progress
- Empty or partial MCP response after the wait limit
- Host step timeout (e.g. AI SDK ~120s) even when the server is still running
- Any transport/pipe error on a tool call after you already waited

**After latch:**

1. **Stop** calling `biologix-ai` MCP tools for the rest of the session.
2. **Run** every step from the CLI table below via `bash` (one job at a time, append `2>&1`).
3. Parse stdout/JSON and continue the pipeline from CLI results only.
4. Note in the report: *"MCP transport latched — remaining steps via CLI fallback (first timeout at Step X)."*

**Before latch:** one MCP tool at a time; wait for JSON before the next. **`MCP_BUSY`** (parallel call rejected) is not a timeout — retry **one** MCP call sequentially. If **that** call times out → latch engages per this rule.

**Do not:** retry MCP after latch, call MCP for "quick" saves, or mix MCP and CLI in the same session after the first timeout.

## CLI equivalents

Run from repo root `/app` in Docker. Set `PYTHONPATH=src/python` when using Python `-c`.

| MCP tool | CLI fallback |
|----------|----------------|
| **`openmm_evaluate_psmiles`** | `python3 scripts/run_openmm_matrix.py '<PSMILES>' --run-dir runs/SESSION --material-name 'Candidate_N' --density-driven --target-density 0.52 --n-repeats 4 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1` (writes `<session>/structures/*_complex_chemviz.png` like MCP; omit `--run-dir` when `BIOLOGIX_AI_SESSION_DIR` is already set) |
| **`generate_psmiles_from_name`** | `python3 -c "from biologix_ai.material_mappings import name_to_psmiles; import json; print(json.dumps(name_to_psmiles('NAME'), indent=2))"` |
| **`validate_psmiles`** | `python3 -c "from biologix_ai.services.psmiles_service import validate_psmiles; import json; print(json.dumps(validate_psmiles('PSMILES','NAME',crosscheck_web=False), indent=2))"` |
| **`compile_discovery_markdown_to_pdf`** | `python3 -c "from pathlib import Path; from biologix_ai.discovery_report import compile_markdown_to_pdf; import json; print(json.dumps(compile_markdown_to_pdf(Path('runs/SESSION')), indent=2))"` |
| **`run_autonomous_discovery`** | `python3 scripts/run_autonomous_discovery.py …` (see script `--help`) |
| **`run_biologics_discovery`** | `python3 scripts/run_biologics_discovery.py --biologic-target … --session-dir runs/SESSION` |
| **`render_psmiles_png`** | `python3 scripts/generate_psmiles_images.py` or `biologix_ai.psmiles_drawing.save_psmiles_png` via `-c` |
| **`save_pipeline_stage`** | `python3 -c "from pathlib import Path; from biologix_ai.services.pipeline_audit import save_pipeline_stage; import json; print(json.dumps(save_pipeline_stage(Path('runs/SESSION'), 'PSMILES', 'STAGE', 'pass', 'detail'), indent=2))"` |
| **`save_funnel_context`** | `python3 -c "from pathlib import Path; from biologix_ai.services.funnel_context import save_funnel_context; import json; print(json.dumps({'path': str(save_funnel_context('STAGE', {}, Path('runs/SESSION')))}, indent=2))"` |
| **`mine_literature`**, **`screen_candidate_library`** | No single script — invoke the matching `biologix_ai` Python module via `python3 -c` or a one-off script; **do not** call MCP after latch. |
| **`plan_retrosynthesis`** | `python3 scripts/run_plan_retrosynthesis.py '<TARGET>' --biologic-target insulin --run-dir runs/SESSION --max-routes 3 2>&1` (wall-clock cap `BIOLOGIX_PLAN_TIMEOUT_S`, stderr heartbeats; omit `--run-dir` when `BIOLOGIX_AI_SESSION_DIR` is set) |

## OpenMM CLI template (most common latch trigger)

```bash
cd /app && python3 scripts/run_openmm_matrix.py '<PSMILES>' \
  --run-dir runs/SESSION --material-name 'Candidate_N' \
  --density-driven --target-density 0.52 \
  --n-repeats 4 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1
```

Parse trailing JSON for `interaction_energy_kj_mol` and structure paths (`complex_chemviz_png_path`, `structure_artifacts_dir`). When the session env is already set, `--run-dir` may be omitted.

**Report figures:** embed `structures/*_complex_chemviz.png` (insulin ribbon + polymer bonded sticks via PyMOL). Do **not** use `*_complex_preview.png` — that file is a matplotlib 3D dot cloud for quick debugging only. Docker image **≥ 0.5.23** ships PyMOL in a separate `pymol-viz` conda env (on PATH in the container).

Re-render chemviz from an existing minimized PDB (no OpenMM rerun):

```bash
cd /app && python3 scripts/run_openmm_matrix.py --render-chemviz-only \
  --run-dir runs/SESSION --material-name 'Candidate_N' 2>&1
```

Or batch: `python3 scripts/render_complex_chemviz.py runs/SESSION/structures/`

**Retrosynthesis CLI template (Step 5 after latch):**

```bash
cd /app && python3 scripts/run_plan_retrosynthesis.py 'Chitosan' \
  --biologic-target insulin --run-dir runs/SESSION --max-routes 3 2>&1
```

Parse trailing JSON for `polymer_routes`. Heartbeats print to stderr every 30s; hard cap `BIOLOGIX_PLAN_TIMEOUT_S` (default 420).

Record audit via CLI (not MCP) after latch:

```bash
python3 -c "from pathlib import Path; from biologix_ai.services.pipeline_audit import save_pipeline_stage; import json; print(json.dumps(save_pipeline_stage(Path('runs/SESSION'), 'PSMILES', 'openmm', 'pass', '<energy JSON>'), indent=2))"
```
