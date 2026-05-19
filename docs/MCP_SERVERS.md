# MCP tool reference

This page describes the insulin-ai MCP tools and what each one needs. For setup (install, Cursor config, Windows/WSL), go to [MCP Getting Started](MCP_GETTING_STARTED.md).

---

## How to run the server

| Command | `bash scripts/run_mcp_server.sh` |
|---------|----------------------------------|

The launcher uses the `insulin-ai-sim` conda env. Session outputs go to `runs/<session_id>/`. Screening details: [OPENMM_SCREENING.md](OPENMM_SCREENING.md).

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

---

## `openmm_evaluate_psmiles` input format

`psmiles_list` can be a **comma-separated string** or a **JSON array of strings** (some MCP clients send one or the other). Empty or unparseable input returns a JSON error instead of aborting the tool call.
