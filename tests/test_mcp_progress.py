"""Tests for MCP progress reporter and JSON truncation helpers."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.mcp_tool_guard import (  # noqa: E402
    McpProgressReporter,
    truncate_mcp_json,
)


def test_mcp_progress_reporter_throttles_and_calls_ctx() -> None:
    ctx = MagicMock()
    reporter = McpProgressReporter(ctx, interval_s=0.05, tool="openmm_evaluate_psmiles")
    reporter.heartbeat("packmol", progress=1.0, total=5.0)
    reporter.heartbeat("packmol again")
    time.sleep(0.06)
    reporter.heartbeat("minimize", progress=2.0, total=5.0)
    assert ctx.report_progress.call_count >= 2


def test_mcp_progress_reporter_without_ctx_still_logs_stderr(capsys) -> None:
    reporter = McpProgressReporter(None, interval_s=0.0, tool="test_tool")
    reporter.heartbeat("stage=test")
    err = capsys.readouterr().err
    assert "test_tool" in err


def test_truncate_mcp_json_strips_heavy_fields() -> None:
    big = {
        "ok": True,
        "evaluation_progress": [{"index": i, "data": "x" * 5000} for i in range(20)],
        "structure_artifact_paths": [{"path": "/tmp/" + "p" * 2000}],
        "candidate_outcomes": [{"index": 0, "status": "completed"}],
    }
    trimmed = truncate_mcp_json(big, max_bytes=4096)
    assert trimmed.get("truncated") is True
    assert "evaluation_progress" not in trimmed
    assert "structure_artifact_paths" not in trimmed
    assert trimmed["candidate_outcomes"]
    assert len(json.dumps(trimmed).encode()) <= 4096


def test_truncate_mcp_json_passthrough_when_small() -> None:
    payload = {"ok": True, "value": 1}
    assert truncate_mcp_json(payload, max_bytes=65536) == payload
