"""External benchmark wrappers (no MCP); skip when clones absent."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_polymer_generative_benchmark_reports_status():
    from benchmarks.polymer_generative_models_benchmark import run_check

    out = run_check()
    assert "benchmark" in out
    assert out["benchmark"] == "polymer_generative_models"
    if out.get("ok"):
        assert out.get("path")
    else:
        assert out.get("error") == "clone_missing"


def test_ibm_polymer_rl_benchmark_reports_status():
    from benchmarks.ibm_polymer_rl_benchmark import run_check

    out = run_check()
    assert out["benchmark"] == "ibm_polymer_rl"
    if out.get("ok"):
        assert out.get("cli_help_returncode") == 0
    else:
        assert out.get("error") in ("clone_missing",) or "missing" in str(out.get("error", ""))
