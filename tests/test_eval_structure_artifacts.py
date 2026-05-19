"""Tests for evaluate_candidates structure artifact directory resolution."""

import os
from pathlib import Path


def test_resolve_eval_structure_artifacts_dir_explicit(tmp_path):
    from insulin_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    sub = tmp_path / "custom_structures"
    d = resolve_eval_structure_artifacts_dir(str(sub))
    assert d == sub.resolve()
    assert d.is_dir()


def test_resolve_eval_structure_artifacts_dir_session_env(monkeypatch, tmp_path):
    from insulin_ai.run_paths import ENV_SESSION
    from insulin_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    session = tmp_path / "run1"
    session.mkdir()
    monkeypatch.setenv(ENV_SESSION, str(session))
    monkeypatch.delenv("INSULIN_AI_EVAL_ARTIFACTS_DIR", raising=False)
    monkeypatch.delenv("INSULIN_AI_EVAL_NO_STRUCTURE_ARTIFACTS", raising=False)
    d = resolve_eval_structure_artifacts_dir(None)
    assert d == (session / "structures").resolve()
    assert d.is_dir()


def test_resolve_eval_structure_artifacts_dir_opt_out(monkeypatch, tmp_path):
    from insulin_ai.run_paths import ENV_SESSION
    from insulin_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    session = tmp_path / "run1"
    session.mkdir()
    monkeypatch.setenv(ENV_SESSION, str(session))
    monkeypatch.setenv("INSULIN_AI_EVAL_NO_STRUCTURE_ARTIFACTS", "1")
    assert resolve_eval_structure_artifacts_dir(None) is None
