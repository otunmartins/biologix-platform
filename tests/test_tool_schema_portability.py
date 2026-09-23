"""Every client (Gemini-based Antigravity, Grok, ChatGPT, Claude, Cursor) must accept the tool surface."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "python"))

from biologix_ai.protocol_gate import REMOTE_PROTOCOL_TOOLS  # noqa: E402

_UNPORTABLE = ("anyOf", "oneOf", "allOf", "$ref", "$defs")


def _http_server():
    spec = importlib.util.spec_from_file_location(
        "biologix_ai_mcp_portability_test", REPO_ROOT / "biologix_ai_mcp_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.create_http_app(token="test-token")
    return module


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def test_protocol_tool_schemas_use_only_portable_json_schema() -> None:
    module = _http_server()
    tools = asyncio.run(module.mcp.list_tools())
    assert [t.name for t in tools] == list(REMOTE_PROTOCOL_TOOLS)
    for tool in tools:
        bad = set(_keys(tool.inputSchema)) & set(_UNPORTABLE)
        assert not bad, f"{tool.name}: {bad}"
        for name, prop in tool.inputSchema.get("properties", {}).items():
            assert "type" in prop, f"{tool.name}.{name} has no type"


def test_protocol_tools_have_titles_and_annotations() -> None:
    module = _http_server()
    for tool in asyncio.run(module.mcp.list_tools()):
        assert tool.annotations is not None, tool.name
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.title
    names = {t.name: t for t in asyncio.run(module.mcp.list_tools())}
    assert names["validate_psmiles"].annotations.readOnlyHint is True
    assert names["openmm_evaluate_psmiles"].annotations.readOnlyHint is False


def test_advertised_string_still_accepts_a_json_array() -> None:
    """Portability only rewrites the advertised schema, not validation."""
    module = _http_server()
    tool = module.mcp._tool_manager._tools["screen_candidate_library"]
    parsed = tool.fn_metadata.arg_model.model_validate({"psmiles_list": ["[*]CC[*]", "[*]OCC[*]"]})
    assert parsed.psmiles_list == ["[*]CC[*]", "[*]OCC[*]"]


def test_portable_schema_rewrites_unions_and_refs() -> None:
    module = _http_server()
    schema = {
        "$defs": {"Mode": {"type": "string", "enum": ["a", "b"]}},
        "properties": {
            "x": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
            "y": {"anyOf": [{"type": "string"}, {"type": "array", "items": {}}], "default": ""},
            "z": {"$ref": "#/$defs/Mode"},
        },
    }
    out = module.portable_schema(schema)
    assert out["properties"]["x"] == {"type": "integer"}
    assert out["properties"]["y"] == {"type": "string", "default": ""}
    assert out["properties"]["z"] == {"type": "string", "enum": ["a", "b"]}
    assert "$defs" not in json.dumps(out)
