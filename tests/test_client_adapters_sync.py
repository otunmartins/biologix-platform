"""The generated client setup files must match PROTOCOL.md."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_clients_directory_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_client_adapters.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_client_configs_point_at_the_server_and_fit_client_limits() -> None:
    clients = REPO_ROOT / "clients"
    cursor = json.loads((clients / "cursor/.cursor/mcp.json").read_text())
    assert cursor["mcpServers"]["biologix"]["url"].endswith("/mcp")
    antigravity = json.loads((clients / "antigravity/.agents/mcp_config.json").read_text())
    assert "serverUrl" in antigravity["mcpServers"]["biologix"]  # Antigravity rejects "url"
    rule = (clients / "antigravity/.agents/rules/biologix-discovery.md").read_text()
    assert len(rule) <= 12_000  # Antigravity rule size limit
    plugin = json.loads((clients / "claude-code-plugin/.claude-plugin/plugin.json").read_text())
    assert plugin["name"] == "biologix"
    for text in (clients / "chatgpt/README.md", clients / "grok/README.md"):
        assert "begin_biologix_discovery" in text.read_text()
    assert "sk-" not in "".join(p.read_text() for p in clients.rglob("*") if p.is_file())
