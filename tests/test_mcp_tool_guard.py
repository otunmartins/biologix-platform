"""Tests for MCP tool event logging helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))

from biologix_ai.mcp_tool_guard import run_guarded_tool  # noqa: E402


def test_run_guarded_tool_success(tmp_path):
    sess = tmp_path / "run1"

    def _ok() -> dict:
        return {"ok": True, "pdf": str(sess / "out.pdf")}

    r = run_guarded_tool("compile_discovery_markdown_to_pdf", sess, _ok, artifact_key="pdf")
    assert r["ok"] is True
    assert r["tool"] == "compile_discovery_markdown_to_pdf"
    assert r["status"] == "completed"
    assert "elapsed_seconds" in r
    events = (sess / "tool_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(events) == 2
    assert json.loads(events[0])["status"] == "started"
    assert json.loads(events[1])["status"] == "completed"


def test_run_guarded_tool_failure(tmp_path):
    sess = tmp_path / "run2"

    def _fail() -> dict:
        return {"ok": False, "error": "fpdf2 write_html failed"}

    r = run_guarded_tool("compile_discovery_markdown_to_pdf", sess, _fail)
    assert r["ok"] is False
    assert r["status"] == "failed"
    assert "hint" in r
    assert (sess / "tool_errors.log").is_file()


def test_truncate_mcp_json_in_guard_module() -> None:
    from biologix_ai.mcp_tool_guard import truncate_mcp_json

    big = {"ok": True, "evaluation_progress": [{"x": "y" * 8000}]}
    out = truncate_mcp_json(big, max_bytes=512)
    assert out.get("truncated") is True
