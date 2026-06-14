"""CLI / MCP OpenMM default parity via shared env resolver."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.simulation.openmm_cli_config import (  # noqa: E402
    resolve_openmm_cli_kwargs,
)


def test_density_driven_default_from_env(monkeypatch) -> None:
    monkeypatch.delenv("BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE", raising=False)
    kw = resolve_openmm_cli_kwargs(
        density_driven_flag=None,
        n_polymers_flag=None,
        target_density_flag=None,
        no_npt_flag=None,
        max_minimize_steps_flag=None,
    )
    assert kw.get("target_density_g_cm3") == 0.52
    assert "n_polymers" not in kw


def test_fixed_mode_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE", "1")
    monkeypatch.setenv("BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS", "6")
    kw = resolve_openmm_cli_kwargs(
        density_driven_flag=None,
        n_polymers_flag=None,
        target_density_flag=None,
        no_npt_flag=None,
        max_minimize_steps_flag=None,
    )
    assert kw.get("n_polymers") == 6
    assert "target_density_g_cm3" not in kw


def test_npt_off_when_env_zero(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_AI_OPENMM_MATRIX_NPT", "0")
    kw = resolve_openmm_cli_kwargs(
        density_driven_flag=None,
        n_polymers_flag=None,
        target_density_flag=None,
        no_npt_flag=None,
        max_minimize_steps_flag=None,
    )
    assert kw.get("run_npt") is False


def test_max_minimize_steps_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS", "1500")
    kw = resolve_openmm_cli_kwargs(
        density_driven_flag=None,
        n_polymers_flag=None,
        target_density_flag=None,
        no_npt_flag=None,
        max_minimize_steps_flag=None,
    )
    assert kw.get("max_minimize_steps") == 1500


def test_explicit_flags_override_env(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE", "1")
    kw = resolve_openmm_cli_kwargs(
        density_driven_flag=True,
        n_polymers_flag=None,
        target_density_flag=0.6,
        no_npt_flag=True,
        max_minimize_steps_flag=500,
    )
    assert kw.get("target_density_g_cm3") == 0.6
    assert kw.get("run_npt") is False
    assert kw.get("max_minimize_steps") == 500
