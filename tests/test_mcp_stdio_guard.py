"""Tests for MCP stdio serialization guard."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.mcp_stdio_guard import (  # noqa: E402
    MCP_BUSY_ERROR,
    install_stdio_guards,
    mcp_busy_json,
)


def test_mcp_busy_json_shape() -> None:
    payload = json.loads(mcp_busy_json())
    assert payload["ok"] is False
    assert payload["error"] == MCP_BUSY_ERROR
    assert "hint" in payload


def test_install_stdio_guards_rejects_concurrent_calls() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-stdio-guard")
    started = threading.Event()
    release = threading.Event()

    @mcp.tool()
    def slow_tool(run_dir: str = "") -> str:
        started.set()
        release.wait(timeout=5.0)
        return json.dumps({"ok": True, "run_dir": run_dir})

    install_stdio_guards(mcp)
    tool = mcp._tool_manager._tools["slow_tool"]

    def run_slow() -> str:
        return tool.fn(run_dir="/tmp/session-a")

    t = threading.Thread(target=run_slow)
    t.start()
    assert started.wait(timeout=2.0)

    busy = json.loads(tool.fn())
    assert busy["error"] == MCP_BUSY_ERROR

    release.set()
    t.join(timeout=2.0)
    assert not t.is_alive()


def test_install_stdio_guards_serializes_sequential_calls() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-stdio-guard-seq")

    @mcp.tool()
    def add_one(x: int) -> str:
        return json.dumps({"ok": True, "x": x})

    install_stdio_guards(mcp)
    fn = mcp._tool_manager._tools["add_one"].fn
    assert json.loads(fn(x=1))["x"] == 1
    assert json.loads(fn(x=2))["x"] == 2
