# Repository layout

| Path | Purpose |
|------|---------|
| `insulin_ai_mcp_server.py` | FastMCP — **only supported user entry** |
| `scripts/run_mcp_server.sh` | Launch MCP (conda env `insulin-ai-sim`) |
| `src/python/insulin_ai/` | Package: literature, llm, simulation, mutation, `autonomous_discovery`, `paper_qa_config`, … |
| `.opencode/` | Agents, MCP JSON |
| `scripts/` | MCP launcher, PaperQA index, autonomous subprocess |
| `papers/` | User PDFs (gitignored) |
| `tests/`, `benchmarks/`, `docs/` | QA / perf / docs |

Runtime: `runs/` (gitignored). No CLI entrypoint; use MCP tools.
