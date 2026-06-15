# OpenCode Platform — Any-biologic delivery materials discovery

- **Creates conda env `biologix-ai-sim`** — RDKit, OpenMM, openmmforcefields, OpenFF Toolkit, pdbfixer, packmol, psmiles, mcp, paper-qa, `-e .`

## Platform overview

The platform discovers **formulation and delivery materials for any biologic** (insulin, mAbs, enzymes, vaccines, peptides). Retrosynthesis + monomer ADMET is a **default deliverable** of every campaign, not an optional branch.

### One session, one biologic, one ordered pipeline

```
Onboard biologic → resolve_biologic_target (PDB)
  → start_biologics_session (seed discovery_world.json)
  → [get_funnel_context — resume if prior checkpoint exists]
  → mine_literature / generate_psmiles_from_name (candidate generation)
  → screen_candidate_library (batch: validate + ADMET + compliance)
  → openmm_evaluate_psmiles (OpenMM energy screening, optional)
  → plan_retrosynthesis (synthesis routes, top K)
  → check_monomers_batch (residual monomer ADMET)
  → check_excipient_compliance (EMA/FDA/GRAS + immunogenicity)
  → compile_results (ranked report)
  → save_funnel_context (checkpoint for resumption)
  → save_pipeline_stage (per-candidate audit at every step)
  → SUMMARY_REPORT.md + compile_discovery_markdown_to_pdf
  → save_discovery_state + import_chat_transcript_file (archive transcript)
  → **Iteration checkpoint (Step 7): ask user whether to run Iteration 2+** (mutate_psmiles / mine_literature with feedback)
```

### Primary agent

**`biologics-delivery-discovery`** is the default OpenCode agent and owns the full pipeline above (Steps 1–6 per iteration, **Step 7 iteration checkpoint** before any Iteration 2+). The legacy `materials-discovery` agent was removed; **`biologics-retrosynthesis`** remains a specialist legacy agent. The platform is **human-in-the-loop only**. Clients connecting via MCP can also drive the pipeline tool-by-tool.

### MCP timeout → CLI latch (agents)

If **any** biologix-ai MCP call **times out for any reason**, the session **latches to CLI-only mode**: **all remaining steps** use documented **bash CLI** equivalents only — **no further MCP tool calls** in that session. See [`.opencode/MCP_CLI_FALLBACK.md`](../.opencode/MCP_CLI_FALLBACK.md).

### MCP timeout stack (v0.5.11+)

| Layer | Default | Notes |
|-------|---------|-------|
| OpenCode `experimental.mcp_timeout` | **600000 ms** | Whole tool call (`.opencode/opencode.jsonc`) |
| `BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S` | **540 s** | In-process per candidate; fails before transport |
| MCP progress keepalive | **~15 s** | `Context.report_progress` + `tool_events.jsonl` |
| Stdio serialization | **`MCP_BUSY`** | Second parallel call rejected immediately |

**OpenCode pin:** Docker records installed version in `/app/.opencode-version`. Verify with
`bash scripts/verify_opencode_mcp_host.sh` (fast) or `… full` for sleep-server import check.
Build with `docker build --build-arg OPENCODE_VERSION=x.y.z` after a release passes the >130s MCP smoke.
Documented minimum: **`OPENCODE_MIN_VERSION=1.14.31`** (needs `onprogress` + stepMs fixes).

If the model provider still cuts long steps, set provider `"timeout": false` in OpenCode config
(see OpenCode #8701).

### NovoMCP-inspired MCP tools added (May 2026)

| Tool | Pattern | Purpose |
|------|---------|---------|
| `get_candidate_profile` | NovoMCP `get_molecule_profile` | Single-call dossier: validate + ADMET + retro + compliance + score |
| `screen_candidate_library` | NovoMCP `screen_library` | Batch screen up to N candidates with optional ADMET/retro/compliance |
| `check_excipient_compliance` | NovoMCP FAVES / `check_compliance` | EMA/FDA/GRAS lookup, GRAS status, immunogenicity SMARTS alerts |
| `save_funnel_context` | NovoMCP `save_funnel_context` | Named pipeline checkpoint for session resumption |
| `get_funnel_context` | NovoMCP `get_funnel_context` | Retrieve a checkpoint; use at session start to skip completed phases |
| `save_pipeline_stage` | NovoMCP `save_funnel_stage` | Append-only per-candidate GxP audit record |
| `get_pipeline_audit` | NovoMCP `get_pipeline_audit` | Retrieve full audit trail for a candidate or session |

### Session resumption model

**On every session start**, the agent calls `get_funnel_context` before re-running any phase. If a "post_screening" or "post_retro" checkpoint exists, it loads those results and continues from where the session disconnected.

**After each major phase**, the agent calls `save_funnel_context(stage=..., checkpoint_data=<top_K_json>)` and `save_pipeline_stage` per candidate.

### OpenCode sub-agent note

OpenCode has **no sub-agent API**: there is no mechanism to programmatically invoke a second agent from within an agent tool call. Multi-step pipelines are coordinated via a **single session** using the `run_dir` / `BIOLOGIX_AI_SESSION_DIR` shared across all tool calls. The **`biologics-delivery-discovery`** agent orchestrates the full loop; **`biologics-retrosynthesis`** is an optional specialist fallback.

## Chat transcripts vs `runs/` session folders

**OpenCode does not automatically copy** the assistant chat into `runs/<session_id>/`. Discovery artifacts (`agent_iteration_*.json`, `SUMMARY_REPORT.md`, optional **`discovery_world.json`** structured rollup, etc.) are written by MCP tools because they know `BIOLOGIX_AI_SESSION_DIR`; the IDE keeps conversation history in its **own** store. The world file accumulates objectives, literature claims, simulation summaries, hypotheses, and human steering notes across iterations; see **`patch_discovery_world`** / **`discovery_world_planning_context`** in [MCP_SERVERS.md](MCP_SERVERS.md).

### Where the session archive must live (canonical)

- **Always** persist the iteration’s chat archive **in the same folder as the rest of that run** — i.e. `runs/<session_id>/` next to `SUMMARY_REPORT.md`, `structures/`, and other iteration outputs (or the explicit `run_dir` / `BIOLOGIX_AI_SESSION_DIR` for that job).
- **Never** use `~/.cursor/` (or any path under **`.cursor/`**) as the **destination** for a session transcript. Do **not** write copies there, do **not** leave the only archive there, and do **not** treat the IDE store as the project’s record of the run.

The IDE may still keep a **source** JSONL on disk (often under `~/.cursor/projects/<project-id>/agent-transcripts/<uuid>.jsonl`) — that path is only for **reading** when calling `import_chat_transcript_file`, which **copies** into `runs/<session_id>/`. Subagent JSONL may sit alongside; naming depends on the OpenCode/Cursor version.

## Archiving chat into the session (required by default)

**Every materials discovery iteration must** end with a transcript **file under** the same `runs/<session_id>/` as the rest of the run. This is **not** optional unless the user explicitly opted out.

1. **Prefer** **`import_chat_transcript_file`** — pass the **absolute path** to the current parent chat JSONL (often under `~/.cursor/.../agent-transcripts/` **as the read-only source**). The tool **writes** into the session folder (e.g. `CHAT_<uuid>.jsonl` under `runs/<session_id>/`).
2. **If** the path is unknown or the copy fails, **use** **`save_session_transcript`** — pass a **complete** Markdown recap of this session (tool calls, decisions, results) so it is stored **only** under `runs/<session_id>/`. Do not skip this step.

Call **after** `start_discovery_session` so `BIOLOGIX_AI_SESSION_DIR` matches, or pass **`run_dir`** to the same folder as `save_discovery_state`.

There is no extra dependency beyond the MCP server; the tools only read/write files you authorize.
