"""Paths to optional third-party benchmark clones under extern/benchmarks/."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

POLYMER_GENERATIVE_MODELS_ROOT = (
    _REPO_ROOT
    / "extern"
    / "benchmarks"
    / "polymer-generative-models"
    / "Polymer-Generative-Models-Benchmark"
)

IBM_POLYMER_RL_ROOT = (
    _REPO_ROOT
    / "extern"
    / "benchmarks"
    / "ibm-logical-agent-polymer"
    / "logical-agent-driven-polymer-discovery"
)

# Shared comparison TSV for all benchmark methods
BENCHMARK_COMPARISON_TSV_DEFAULT = _REPO_ROOT / "benchmarks" / "comparison_results.tsv"
