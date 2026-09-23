# Biologix platform: developer guide

This file is for working **on** the platform. The discovery agent's instructions,
which every MCP client receives, live in
`src/python/biologix_ai/protocol/PROTOCOL.md`. Edit them there, then run
`python scripts/build_client_adapters.py` to regenerate `clients/`.

## Layout

- `biologix_ai_mcp_server.py`: the FastMCP server. It has two profiles, set with
  `BIOLOGIX_MCP_PROFILE`:
  - `protocol` (default, and always used over HTTP): the 24 protocol tools, the gate,
    and the job runner.
  - `full`: every tool, ungated. OpenCode uses this through `.opencode/opencode.jsonc`.
- `src/python/biologix_ai/protocol_gate.py`: the step order and the per-client state.
  Every result carries a `protocol` envelope with `next_required_tool`,
  `next_arguments`, `step_instructions`, and `user_stop_allowed`.
- `src/python/biologix_ai/mcp_jobs.py`: moves tools off the event loop. A call longer
  than `BIOLOGIX_TOOL_WAIT_S` (90 s over HTTP, unlimited over stdio). ChatGPT's proxy
  completed a 144.5 s call but returned HTTP 504 at 240 s, so its ceiling is between
  them; change this only with a measurement returns a job
  that the agent waits on with `await_biologix_job`.
- `src/python/biologix_ai/mcp_stdio_guard.py`: a per-client `MCP_BUSY` lock.
- `src/python/biologix_ai/mcp_client.py`: the client id: the OAuth client, else a hash of
  the bearer token, else the `Mcp-Session-Id` header. Don't key on the session alone:
  ChatGPT opens a new MCP session for every tool call.
- `src/python/biologix_ai/services/biologic_resolver.py`: resolves any biologic. It
  accepts a name, `PDB:chains`, `uniprot:ACC[:a-b]`, or `sequence:`. It prepares the
  structure with PDBFixer, then runs an AMBER14 check. The bundled insulin
  (4F1C chains A+B) keeps its original, un-PDBFixed path.
- `src/python/biologix_ai/compute/`: where OpenMM runs. That is either in the MCP
  process (`local`) or on the Modal `openmm_worker_cpu` / `openmm_worker_gpu`
  functions (`modal`).
- `src/python/biologix_ai/simulation/`: the OpenMM matrix. A charged polymer is
  packed with counterions (`counterions_for_matrix`) so the matrix subsystem is
  neutral; a neutral polymer builds the identical force field it always did, so
  do not load the ion file unconditionally. Platform selection is in
  `select_openmm_platform` (`BIOLOGIX_AI_OPENMM_PLATFORM=CPU|CUDA|OpenCL|auto`). GPU
  candidates run in a fresh interpreter (`matrix_subprocess.py`).
- `modal_app.py`: the web function `serve` plus the CPU and GPU OpenMM workers.
  `verify_runtime` and `verify_gpu` check a deployed image.
- `clients/`: generated setup for Cursor, Claude Code, Antigravity, ChatGPT, and Grok.

## Tests

```bash
PYTHONPATH=src/python:. conda run -n insulin-ai-sim python -m pytest -q
```

The Docker and Modal image pins `mcp>=1.30`. Server-level tests expect that version.

## Rules

- Keep the default insulin simulation byte-identical. `tests/test_openmm_target_harness.py`
  checks this.
- A requested GPU platform must never fall back to CPU without saying so. Every
  OpenMM result records `openmm_platform`.
- Advertised tool schemas must stay portable: no `anyOf`, `$ref`, or `$defs`.
  `tests/test_tool_schema_portability.py` checks this.
- Deploy with `scripts/deploy_modal.sh` (detached: `setsid nohup scripts/deploy_modal.sh > /tmp/modal_deploy.log 2>&1 &`).
  Code-only changes deploy in minutes. Changing a base-context input rebuilds the
  base image (about an hour): see `BASE_CONTEXT_FILES` in `modal_app.py`. A new
  file that the Dockerfile build reads must be added there.
