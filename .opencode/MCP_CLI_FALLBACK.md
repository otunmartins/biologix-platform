# MCP timeout → CLI fallback (platform rule)

Apply to **every** `biologix-ai` MCP tool call in OpenCode/Docker sessions.

## Golden rule

**If an MCP tool call times out for any reason**, switch to the **equivalent bash CLI command** in the table below. Do not retry the same operation via MCP.

Timeout-like symptoms (all mean: use CLI for that operation):

- OpenCode red icon / `mcp_timeout` / `-32001` / "Request timed out"
- Tool hangs with no JSON while the same work via bash would show progress
- Empty or partial MCP response after the wait limit
- Host step timeout (e.g. AI SDK ~120s) even when the server is still running
- Any transport/pipe error on a tool call

**Do not** retry the same MCP call (batched, parallel, or sequential) after a timeout. **Do** run bash CLI once (`2>&1`), parse stdout/JSON, then continue the pipeline.

Note in the report: *"Step X via CLI fallback (MCP timeout)."*

## When to switch (also applies)

If **any** MCP call **once**:

- times out (any layer — see above),
- hangs with no response while bash would show progress,
- fails with transport/pipe errors after you already waited, or
- was issued in parallel with other MCP calls and the batch failed,

then **stop using MCP for that operation**. Treat it as **MCP transport failure**, not failed science.

**Do not:** retry the same batched MCP call, fire parallel MCP tools to "speed up," or call the same long-running MCP tool again hoping for a different outcome.

**Do:** run the **equivalent CLI** via `bash` (one job at a time, append `2>&1`), parse stdout/JSON, then optionally record audit state with **single** instant MCP calls (`save_pipeline_stage`, `save_funnel_context`) when those return immediately.

**`MCP_BUSY`** (parallel call rejected) is not a timeout — retry **one** MCP call sequentially. If **that** call times out → CLI fallback per this rule.

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
| **`save_pipeline_stage`** | No CLI needed — instant JSONL append. If this "times out", MCP is blocked; wait, then **one** retry. |
| **`mine_literature`**, **`screen_candidate_library`**, **`plan_retrosynthesis`** | No single script; retry MCP **once** sequentially, or invoke `biologix_ai` Python modules via `-c` if you know the API. |

## OpenMM CLI template (most common fallback)

```bash
cd /app && python3 scripts/run_openmm_matrix.py '<PSMILES>' \
  --run-dir runs/SESSION --material-name 'Candidate_N' \
  --density-driven --target-density 0.52 \
  --n-repeats 4 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1
```

Parse trailing JSON for `interaction_energy_kj_mol` and structure paths (`complex_chemviz_png_path`, `structure_artifacts_dir`). When the session env is already set, `--run-dir` may be omitted.

`save_pipeline_stage(candidate_psmiles=…, stage="openmm", disposition="pass", detail=<energy JSON>, run_dir=<session>)`
