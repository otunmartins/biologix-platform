"""
Smoke tests for MCP servers.

Ensures all MCP servers load without decorator errors (e.g. @mcp.tool must be @mcp.tool()).
FastMCP raises TypeError if decorator is not called: "Use @tool() instead of @tool"
"""

import json
import os
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Single MCP server (biologix_ai_mcp_server.py); literature lives in biologix_ai.literature
MCP_SERVERS = [("biologix_ai_mcp_server", "biologix_ai_mcp_server.py")]


def _load_mcp_server(path: str):
    """Load MCP server module. Fails if @mcp.tool (no parens) is used."""
    full_path = os.path.join(ROOT, path)
    spec = importlib.util.spec_from_file_location("mcp_server", full_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_biologix_ai_mcp_server_loads():
    """biologix-ai MCP server must load (run_autonomous_discovery, mine_literature, etc.)."""
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest
        pytest.skip(f"MCP dependencies unavailable: {e}")
    assert hasattr(mod, "mcp")
    assert hasattr(mod.mcp, "run")


def test_all_mcp_servers_load():
    """All OpenCode-enabled MCP servers must load without @tool decorator errors."""
    try:
        _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest
        pytest.skip(f"MCP dependencies unavailable: {e}")
    for _name, path in MCP_SERVERS:
        _load_mcp_server(path)


def test_normalize_psmiles_list_for_eval():
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest

        pytest.skip(f"MCP dependencies unavailable: {e}")
    n = mod._normalize_psmiles_list_for_eval
    assert n("[*]A[*],[*]B[*]") == ["[*]A[*]", "[*]B[*]"]
    assert n(["[*]A[*]", "[*]B[*]"]) == ["[*]A[*]", "[*]B[*]"]
    assert n('["[*]A[*]", "[*]B[*]"]') == ["[*]A[*]", "[*]B[*]"]
    assert n("[*]A[*]") == ["[*]A[*]"]
    assert n("") == []
    assert n(None) == []


def test_openmm_evaluate_psmiles_empty_returns_json_error():
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest

        pytest.skip(f"MCP dependencies unavailable: {e}")
    out = json.loads(mod.openmm_evaluate_psmiles("", verbose=False))
    assert out.get("error")
    assert "empty" in out["error"].lower() or "parsed" in out["error"].lower()


def test_validate_psmiles_json_shape():
    """validate_psmiles returns JSON with valid; optional name_crosscheck when enabled."""
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest

        pytest.skip(f"MCP dependencies unavailable: {e}")
    out = json.loads(mod.validate_psmiles("[*]OCC[*]", material_name="", crosscheck_web=False))
    assert "valid" in out
    assert out.get("valid") is True
    assert "name_crosscheck" not in out
    out2 = json.loads(
        mod.validate_psmiles("[*]OCC[*]", material_name="polyethylene glycol", crosscheck_web=True)
    )
    assert out2.get("valid") is True
    assert "name_crosscheck" in out2
    nc = out2["name_crosscheck"]
    assert nc.get("material_name") == "polyethylene glycol"
    assert "snippets" in nc
    assert "disclaimer" in nc


def test_no_mcp_tool_without_parens():
    """Fail if any MCP file uses @mcp.tool instead of @mcp.tool()."""
    import re

    bad_files = []
    for _name, path in MCP_SERVERS:
        full_path = os.path.join(ROOT, path)
        content = open(full_path, "r").read()
        # Match @mcp.tool at end of line or followed by non-open-paren
        if re.search(r"@mcp\.tool\s*(?!\()", content):
            bad_files.append(path)
    assert not bad_files, (
        f"Use @mcp.tool() not @mcp.tool in: {bad_files}. "
        "FastMCP requires parentheses: @mcp.tool()"
    )


def test_discovery_world_mcp_patch_and_get():
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest

        pytest.skip(f"MCP dependencies unavailable: {e}")
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        patch = {
            "objective": "Test objective",
            "literature_entries": [
                {"id": "L1", "title": "Paper", "claim": "Claim text", "iteration": 1},
            ],
        }
        out = json.loads(mod.patch_discovery_world(json.dumps(patch), run_dir=td))
        assert out.get("ok") is True
        full = json.loads(mod.get_discovery_world_state(run_dir=td, summary=False))
        assert "world" in full
        assert full["world"]["objective"] == "Test objective"
        summ = json.loads(mod.get_discovery_world_state(run_dir=td, summary=True))
        assert "planning_context" in summ
        assert "Test objective" in summ["planning_context"]
        ctx = json.loads(mod.discovery_world_planning_context(max_chars=4000, run_dir=td))
        assert "planning_context" in ctx


def test_save_discovery_state_updates_world_meta_when_world_exists():
    try:
        mod = _load_mcp_server("biologix_ai_mcp_server.py")
    except (ImportError, ModuleNotFoundError) as e:
        import pytest

        pytest.skip(f"MCP dependencies unavailable: {e}")
    import tempfile

    sys.path.insert(0, os.path.join(ROOT, "src", "python"))
    from biologix_ai.discovery_world import empty_world, save_world, world_path_for_session

    with tempfile.TemporaryDirectory() as td:
        wp = world_path_for_session(td)
        save_world(wp, empty_world())
        fb = json.dumps({"high_performers": []})
        out = json.loads(mod.save_discovery_state(2, fb, run_dir=td))
        assert "saved" in out
        data = json.loads(mod.get_discovery_world_state(run_dir=td, summary=False))
        assert data["world"]["meta"]["last_iteration"] == 2
        assert data["world"]["meta"]["links"].get("last_agent_iteration_file") == "agent_iteration_2.json"
