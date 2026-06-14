# MCP timeout → CLI fallback (platform rule)

Apply to **every** `biologix-ai` MCP tool call in OpenCode/Docker sessions.

## When to switch

If **any** MCP call **once**:

- times out (OpenCode `mcp_timeout`, red icon, no JSON),
- hangs with no response while bash would show progress,
- fails with transport/pipe errors, or
- was issued in parallel with other MCP calls and any of them failed,

then **stop using MCP for that operation**. Treat it as **MCP stdio failure**, not failed science.

**Do not:** retry the same batched MCP call, or fire parallel MCP tools to "speed up."

**Do:** run the **equivalent CLI** via `bash` (one job at a time, append `2>&1`), parse stdout/JSON, then optionally record audit state with **single** MCP calls (`save_pipeline_stage`, `save_funnel_context`) when those succeed instantly.

Note in the report: *"Step X via CLI fallback (MCP timeout)."*

## CLI equivalents

Run from repo root `/app` in Docker. Set `PYTHONPATH=src/python` when using Python `-c`.

| MCP tool | CLI fallback |
|----------|----------------|
| **`openmm_evaluate_psmiles`** | `python3 scripts/run_openmm_matrix.py '<PSMILES>' --n-repeats 4 --n-polymers 8 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1` (one polymer per invocation) |
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
  --n-repeats 4 --n-polymers 8 --box-nm 7.5 --packing-mode bulk --no-npt 2>&1
```

Parse trailing JSON for `interaction_energy_kj_mol`. Then:

`save_pipeline_stage(candidate_psmiles=…, stage="openmm", disposition="pass", detail=<energy JSON>, run_dir=<session>)`
