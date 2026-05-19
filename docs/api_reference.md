# Insulin AI API reference (MCP-only)

Discovery is driven **only** via the **insulin-ai** MCP server. There is no command-line `discover` / `mine` / `evaluate` app.

## Equivalent operations (MCP tools)

| Former CLI idea | MCP tool |
|-----------------|----------|
| Full batch loop | `run_autonomous_discovery` (background) or step-by-step tools below |
| Literature mining | `mine_literature` |
| Evaluate PSMILES | `openmm_evaluate_psmiles` |
| System status | `get_materials_status` |
| PaperQA index | `index_papers` |

## MCP Tools (insulin-ai server)

See [MCP_SERVERS.md](MCP_SERVERS.md) for the full table and environment variables.

## Session outputs

`start_discovery_session`, `save_discovery_state`, `run_autonomous_discovery` write under `runs/<session_id>/`.

## Status

`get_materials_status` returns MD, mutation, literature, and PaperQA2 readiness.
