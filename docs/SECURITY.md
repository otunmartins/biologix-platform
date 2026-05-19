# Security

This page covers API keys, secrets, and safe configuration for insulin-ai. For first-time setup, see [MCP Getting Started](MCP_GETTING_STARTED.md).

---

## API keys and MCP

- **Never commit** real API keys, GitHub PATs, or Asta keys into the repo.
- Use **`.cursor/mcp.json.example`** as a template; copy to **`.cursor/mcp.json`** locally (gitignored) and set secrets via **environment variables** only.
- If keys were ever pasted into chat, a ticket, or a committed file, **rotate them** at the provider (Brave, GitHub, Ai2 Asta, etc.).

## OpenCode vs Cursor

- OpenCode: [`.opencode/opencode.jsonc`](../.opencode/opencode.jsonc) — use `{env:ASTA_API_KEY}` style headers where supported.
- Cursor: project MCP — see [`.cursor/mcp.json.example`](../.cursor/mcp.json.example).
