# MCP tool reference

This page describes the biologix-ai MCP tools and what each one needs. For setup (install, Cursor config, Windows/WSL), go to [MCP Getting Started](MCP_GETTING_STARTED.md).

---

## How to run the server

| Command | `bash scripts/run_mcp_server.sh` |
|---------|----------------------------------|

The launcher uses the `biologix-ai-sim` conda env. Session outputs go to `runs/<session_id>/`. Screening details: [OPENMM_SCREENING.md](OPENMM_SCREENING.md).

### MCP timeout → CLI latch

**Golden rule:** If **any** MCP tool times out for **any reason**, the session **latches to CLI-only mode** for **all remaining steps**. Do **not** call any `biologix-ai` MCP tool again in that session. Use bash CLI from [`.opencode/MCP_CLI_FALLBACK.md`](../.opencode/MCP_CLI_FALLBACK.md).

### Stdio serialization and progress (v0.5.11+)

- **One tool at a time:** [`mcp_stdio_guard.py`](../src/python/biologix_ai/mcp_stdio_guard.py) wraps every handler. A second concurrent call returns `{"ok": false, "error": "MCP_BUSY"}` immediately.
- **Progress keepalive:** long tools (`openmm_evaluate_psmiles`, `generate_psmiles_from_name`, guarded PDF paths) emit MCP `notifications/progress` every ~15s plus `runs/<session>/tool_events.jsonl` entries.
- **Stdout rule:** MCP protocol uses stdout only; all logs go to stderr (`PYTHONUNBUFFERED=1` in `scripts/run_mcp_server.sh`).
- **Response cap:** large OpenMM JSON is truncated (`truncated: true`); full detail remains in `tool_events.jsonl`.

---

## Tool prerequisites

| Tool | What it needs |
|------|---------------|
| **`mine_literature`** | Asta MCP when `ASTA_API_KEY` is set; otherwise Semantic Scholar (no key). |
| **`openmm_evaluate_psmiles`**, **`run_autonomous_discovery`** | OpenMM stack (openmm, openmmforcefields, openff-toolkit, rdkit, pdbfixer), Packmol on PATH, insulin PDB (`data/4F1C.pdb` or `ensure_insulin_pdb`). See [OPENMM_SCREENING.md](OPENMM_SCREENING.md). |
| **`generate_psmiles_from_name`** | Known-polymer table (~60 entries) or PubChem monomer SMILES; auto-detects polymerization mechanism (vinyl, ester condensation, amide condensation) and places `[*]`. Returns `{ok, psmiles, source, confidence, mechanism, md_compatible}`. |
| **`validate_psmiles`** | RDKit, optional PubChem (PUG REST) and DuckDuckGo (`crosscheck_web=true`, needs `duckduckgo-search`). [PSMILES primer](PSMILES_GUIDE.md). |
| **`render_psmiles_png`** | [psmiles](https://github.com/FermiQ/psmiles) — 2D monomer PNG under `<session>/structures/`. |
| **`compile_discovery_markdown_to_pdf`** | Agent-authored `SUMMARY_REPORT.md` → PDF (markdown + fpdf2). |
| **`write_discovery_summary_report`** | Batch from `agent_iteration_*.json` only—skeleton MD + PNG + PDF. Prefer agent-written MD + `compile_discovery_markdown_to_pdf`. |
| **`save_session_transcript`** | Writes text into `runs/<session>/` only. Call this each run if you don't use `import_chat_transcript_file`. |
| **`import_chat_transcript_file`** | Reads JSONL (e.g. from `~/.cursor/.../agent-transcripts/`) and copies it into `runs/<session>/`. Do not use `.cursor/` as the archive destination. |

### Discovery world model (`discovery_world.json`)

Structured cross-iteration rollup (Kosmos-style shared state) in the same folder as `agent_iteration_*.json`. List fields use stable **`id`** keys; patches merge by id.

| Tool | Purpose |
|------|---------|
| **`get_discovery_world_state`** | Read the world file. Pass **`summary=true`** for JSON containing only **`planning_context`** (smaller). Missing file behaves like an empty schema. |
| **`patch_discovery_world`** | Merge a JSON object into `discovery_world.json` (creates file if needed). |
| **`discovery_world_planning_context`** | Bounded text for prompts (objective, hypotheses, open questions, directives, recent lit/sim). Prefer over full JSON during discovery. |

After **`save_discovery_state`**, if `discovery_world.json` already exists, the server updates **`meta.last_iteration`** and **`links.last_agent_iteration_file`** only.

Chat is not mirrored into `runs/` automatically; agents must call `save_session_transcript` or `import_chat_transcript_file` to archive each run. See [DEPENDENCIES.md](DEPENDENCIES.md) for reporting libs and [OpenCode_PLATFORM.md](OpenCode_PLATFORM.md) for OpenCode specifics.

### Retrosynthesis (agent-backed, no extra API key)

| Tool | Purpose |
|------|---------|
| **`prepare_retrosynthesis`** | Resolve PSMILES → material name, download PDFs to `run_dir`, return `extraction_schema`. Requires `run_dir`. |
| **`submit_retro_extractions`** | Agent writes `llm_res.json` into session workspace (JSON: paper → reaction text). |
| **`plan_retrosynthesis`** | Build polymer KG routes from session extractions; AiZynthFinder for monomers. Check `metadata.route_provenance`. |
| **`assemble_retrosynthesis_report`** | Build `retrosynthesis/RETROSYNTHESIS_REPORT.md` from `plan_*.json` for SUMMARY_REPORT § Retrosynthesis. |

| Component | Extra API key? |
|-----------|----------------|
| OpenCode agent (retro extraction) | No (already configured) — `submit_retro_extractions` |
| AiZynthFinder | No (one-time model download via `scripts/setup_aizynthfinder.sh`) |

Default MCP env: `RETRO_LLM_BACKEND=skip`, `BIOLOGIX_AI_AIZYNTH_CONFIG=./data/aizynthfinder/config.yml`.

---

## `openmm_evaluate_psmiles` input format

`psmiles_list` can be a **comma-separated string** or a **JSON array of strings** (some MCP clients send one or the other). Empty or unparseable input returns a JSON error instead of aborting the tool call.
