#!/usr/bin/env python3
"""
Resolve OpenMM matrix CLI kwargs from explicit flags and BIOLOGIX_AI_* env vars.

Shared by ``scripts/run_openmm_matrix.py`` and parity tests so MCP and CLI fallback
use the same defaults when flags are omitted.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _matrix_fixed_mode() -> bool:
    return _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE", False)


def resolve_openmm_cli_kwargs(
    *,
    density_driven_flag: Optional[bool],
    n_polymers_flag: Optional[int],
    target_density_flag: Optional[float],
    no_npt_flag: Optional[bool],
    max_minimize_steps_flag: Optional[int],
    candidate_timeout_flag: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build keyword arguments for ``run_openmm_matrix_relax_and_energy`` from CLI flags
    (when not None) and environment defaults matching the MCP / MDSimulator path.
    """
    kw: Dict[str, Any] = {}

    use_density = density_driven_flag
    if use_density is None:
        use_density = not _matrix_fixed_mode()

    if use_density:
        density = (
            target_density_flag
            if target_density_flag is not None
            else _env_float("BIOLOGIX_AI_OPENMM_MATRIX_DEFAULT_DENSITY_G_CM3", 0.52)
        )
        kw["target_density_g_cm3"] = density
    else:
        n_pol = (
            n_polymers_flag
            if n_polymers_flag is not None
            else _env_int("BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS", 8)
        )
        kw["n_polymers"] = n_pol

    if no_npt_flag is not None:
        kw["run_npt"] = not no_npt_flag
    else:
        kw["run_npt"] = _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_NPT", False)

    if max_minimize_steps_flag is not None:
        kw["max_minimize_steps"] = max_minimize_steps_flag
    else:
        kw["max_minimize_steps"] = _env_int("BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS", 2000)

    if candidate_timeout_flag is not None:
        kw["candidate_timeout_s"] = candidate_timeout_flag
    else:
        raw = os.environ.get("BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S", "540").strip()
        if raw and float(raw) > 0:
            kw["candidate_timeout_s"] = float(raw)

    return kw
