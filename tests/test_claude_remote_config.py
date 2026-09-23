"""Tests for Claude Code onboarding and token-safe remote MCP configuration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "biologix_ai_mcp_server.py"
sys.path.insert(0, str(REPO_ROOT / "src" / "python"))

from biologix_ai.protocol_gate import (  # noqa: E402
    FIRST_CONTACT_DIRECTIVE,
    REMOTE_PROTOCOL_TOOLS,
    REMOTE_TOOL_STEPS,
)


PROTOCOL_PATH = REPO_ROOT / "src" / "python" / "biologix_ai" / "protocol" / "PROTOCOL.md"


def _run(fn, **kwargs):
    """Call a registered tool function; the protocol profile makes tools async."""
    result = fn(**kwargs)
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


def _load_server():
    spec = importlib.util.spec_from_file_location("biologix_ai_mcp_instructions_test", SERVER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remote_mcp_instructions_are_the_first_contact_directive() -> None:
    module = _load_server()
    module.create_http_app(token="test-token")

    instructions = module.mcp.instructions
    assert instructions == FIRST_CONTACT_DIRECTIVE
    assert len(instructions) <= 512


def test_remote_server_lists_only_protocol_tools_as_pipeline_steps() -> None:
    module = _load_server()
    module.create_http_app(token="test-token")

    tools = asyncio.run(module.mcp.list_tools())
    assert [tool.name for tool in tools] == list(REMOTE_PROTOCOL_TOOLS)
    for tool in tools:
        assert tool.description.startswith(REMOTE_TOOL_STEPS[tool.name]), tool.name
        assert "CLI-only" not in tool.description
        assert "MCP_CLI_FALLBACK" not in tool.description


def test_create_http_app_twice_keeps_one_step_prefix() -> None:
    module = _load_server()
    module.create_http_app(token="test-token")
    module.create_http_app(token="test-token")
    tools = asyncio.run(module.mcp.list_tools())
    for tool in tools:
        assert tool.description.count(REMOTE_TOOL_STEPS[tool.name]) == 1


def test_local_stdio_server_keeps_every_tool() -> None:
    module = _load_server()
    names = {tool.name for tool in asyncio.run(module.mcp.list_tools())}
    assert set(REMOTE_PROTOCOL_TOOLS) <= names
    assert {"pubmed_search", "run_autonomous_discovery", "generate_psmiles_from_name"} <= names


def test_bootstrap_tool_returns_the_full_protocol_and_onboarding_questions() -> None:
    module = _load_server()
    module.create_http_app(token="test-token")
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8").strip()
    begin = module.mcp._tool_manager._tools["begin_biologix_discovery"].fn

    missing = json.loads(_run(begin))
    assert missing["needs_user_input"] is True
    assert len(missing["questions"]) == 2
    assert missing["discovery_protocol"] == protocol
    assert missing["protocol"]["user_stop_allowed"] is True

    ready = json.loads(_run(begin, biologic_target="insulin", polymer_target="suggest"))
    assert ready["needs_user_input"] is False
    assert ready["discovery_protocol"] == protocol
    assert ready["protocol"]["next_required_tool"] == "resolve_biologic_target"
    assert ready["protocol"]["user_stop_allowed"] is False


def test_protocol_prompt_and_resource_serve_the_project_protocol() -> None:
    module = _load_server()
    module.create_http_app(token="test-token")
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8").strip()

    prompts = asyncio.run(module.mcp.list_prompts())
    assert "biologix_discovery" in [prompt.name for prompt in prompts]
    prompt = asyncio.run(module.mcp.get_prompt("biologix_discovery"))
    assert protocol in prompt.messages[0].content.text

    resources = asyncio.run(module.mcp.list_resources())
    assert "biologix://protocol" in [str(resource.uri) for resource in resources]
    contents = list(asyncio.run(module.mcp.read_resource("biologix://protocol")))
    assert contents[0].content == protocol


def test_bootstrap_protocol_is_the_full_project_protocol() -> None:
    module = _load_server()
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8").strip()
    instructions = module.load_remote_mcp_instructions()
    assert instructions == protocol
    for step in (
        "Step 1 — Onboard",
        "Step 2 — Session",
        "Step 3 — Literature and validation",
        "Step 4 — Screen and simulate",
        "Step 5 — Retrosynthesis",
        "Step 6 — Report",
        "Step 7 — Iteration checkpoint",
        "await_biologix_job",
        "uniprot:",
        "sequence:",
    ):
        assert step in instructions
    assert "CLI-only" not in instructions
    assert "./install" not in instructions
    assert "MCP_CLI_FALLBACK" not in instructions
    assert "You are the Biologix discovery agent" in instructions
    assert "your first reply contains only these questions" in " ".join(instructions.split())


def test_repo_claude_md_is_a_developer_guide_not_the_agent_protocol() -> None:
    guide = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "follow this protocol and no other workflow" not in guide
    assert "PROTOCOL.md" in guide


def test_project_mcp_config_uses_environment_token() -> None:
    config = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["biologix"]

    assert server["type"] == "http"
    assert server["url"].startswith("${BIOLOGIX_MCP_URL:-https://")
    assert server["url"].endswith(".modal.run/mcp}")
    assert server["headers"]["Authorization"] == "Bearer ${BIOLOGIX_MCP_TOKEN:-}"


def test_protocol_preserves_linear_hitl_protocol() -> None:
    instructions = PROTOCOL_PATH.read_text(encoding="utf-8")
    instructions = " ".join(instructions.split())
    for requirement in (
        "Step 1 — Onboard",
        "no tool call until the user answers",
        "one Biologix tool call at a time",
        "Step 7 — Iteration checkpoint",
        "Wait for the user",
        "Never invent routes",
    ):
        assert requirement in instructions
    assert "MCP_CLI_FALLBACK" not in instructions
    assert "./install" not in instructions


def test_remote_setup_documentation_never_contains_a_literal_token() -> None:
    documentation = (REPO_ROOT / "docs" / "REMOTE_MCP.md").read_text(encoding="utf-8")
    assert "claude mcp add --transport http" in documentation
    assert "modal deploy modal_app.py" in documentation
    assert "BIOLOGIX_MCP_TOKEN" in documentation
    assert "BIOLOGIX_MCP_URL" in documentation
    assert "sk-ant-" not in documentation
    assert "Bearer test-" not in documentation


def test_remote_setup_documents_claude_web_dynamic_oauth_flow() -> None:
    documentation = (REPO_ROOT / "docs" / "REMOTE_MCP.md").read_text(encoding="utf-8")
    for requirement in (
        "biologix-mcp-auth",
        "BIOLOGIX_OAUTH_APPROVAL_TOKEN",
        "BIOLOGIX_OAUTH_STORAGE_KEY",
        "Dynamic Client Registration",
        "Claude Web",
        "Leave the OAuth Client ID field empty",
        "/oauth/approve",
        "refresh token",
        "revoke",
        "storage key",
        "begin_biologix_discovery",
        "Project instructions",
        "next_required_tool",
    ):
        assert requirement in documentation
