"""Regression: OpenMM/MCP paths must not write to stdout (JSON-RPC pipe)."""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.mcp_tool_guard import run_guarded_tool, run_instant_mcp_tool  # noqa: E402


def _bare_stdout_prints(source: str) -> list[str]:
    """Lines with print( that are not explicitly stderr."""
    hits: list[str] = []
    for i, line in enumerate(source.splitlines(), start=1):
        if "print(" not in line:
            continue
        if "file=sys.stderr" in line.replace(" ", ""):
            continue
        if "_progress_log" in line or "_stderr" in line:
            continue
        hits.append(f"line {i}: {line.strip()}")
    return hits


def test_md_simulator_and_openmm_complex_no_bare_stdout_prints() -> None:
    sim = (ROOT / "src/python/biologix_ai/simulation/md_simulator.py").read_text(encoding="utf-8")
    omm = (ROOT / "src/python/biologix_ai/simulation/openmm_complex.py").read_text(encoding="utf-8")
    assert _bare_stdout_prints(sim) == [], sim
    assert _bare_stdout_prints(omm) == [], omm


def test_run_guarded_tool_redirects_stdout_to_stderr(tmp_path) -> None:
    sess = tmp_path / "run_stdout"
    buf = io.StringIO()

    def _writes_stdout() -> dict:
        print("leaked-to-stdout")
        return {"ok": True}

    old_stderr = sys.stderr
    sys.stderr = buf
    try:
        r = run_guarded_tool("test_tool", sess, _writes_stdout)
    finally:
        sys.stderr = old_stderr

    assert r["ok"] is True
    assert "leaked-to-stdout" in buf.getvalue()


def test_instant_timeout_returns_without_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S", "0.15")
    sess = tmp_path / "run_timeout"
    t0 = time.perf_counter()

    def _slow() -> dict:
        time.sleep(2.0)
        return {"ok": True}

    r = run_instant_mcp_tool("save_pipeline_stage", sess, _slow)
    elapsed = time.perf_counter() - t0
    assert r["ok"] is False
    assert r["stage"] == "timeout"
    assert elapsed < 1.5
