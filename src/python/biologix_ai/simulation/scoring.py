#!/usr/bin/env python3
"""
Scalar discovery score for autoresearch and screening.

Balances:
- Stability: more negative interaction energy E_int (kJ/mol) is better.
- Insulin preservation: lower Kabsch RMSD (nm) of insulin before vs after minimize
  is better (proxy for matrix not distorting native structure / release).
"""

from __future__ import annotations

from typing import Any, Mapping


def composite_screening_score(
    interaction_energy_kj_mol: float,
    insulin_rmsd_to_initial_nm: float,
    weight_stability: float = 0.5,
    weight_preservation: float = 0.5,
    e_scale_kj: float = 150.0,
    rmsd_soft_nm: float = 0.08,
) -> float:
    """
    Single score (higher = better). Linear blend of normalized terms.

    stability_term: (-E_int) / e_scale — favors binding / host stabilization.
    preservation_term: 1 / (rmsd + rmsd_soft) — favors rigid insulin geometry.

    Args:
        interaction_energy_kj_mol: E_complex - E_ins - E_poly.
        insulin_rmsd_to_initial_nm: Kabsch RMSD of insulin after minimize vs initial.
        weight_stability, weight_preservation: non-negative; normalized to sum to 1.
        e_scale_kj: ~typical |E_int| scale for normalization.
        rmsd_soft_nm: floor so division stable; ~0.05–0.1 nm typical good fold.
    """
    if not (weight_stability >= 0 and weight_preservation >= 0):
        raise ValueError("weights must be non-negative")
    wsum = weight_stability + weight_preservation
    ws, wp = weight_stability / wsum, weight_preservation / wsum
    stab = -float(interaction_energy_kj_mol) / float(e_scale_kj)
    rmsd = float(insulin_rmsd_to_initial_nm)
    if rmsd != rmsd or rmsd < 0:
        rmsd = 1.0
    pres = 1.0 / (rmsd + float(rmsd_soft_nm))
    return ws * stab + wp * pres


def discovery_score(
    feedback: Mapping[str, Any],
    high_performer_weight: float = 2.0,
    mechanism_weight: float = 0.5,
    problematic_weight: float = 1.0,
    interaction_scale: float = 0.02,
    use_composite: bool = True,
    composite_scale: float = 3.0,
) -> float:
    """
    Autoresearch score (higher = better), **normalized by batch size**.

    The per-candidate energy/composite bonus is averaged over all candidates with
    valid data, so the score is comparable across batches of different sizes.
    """
    hp = _len_safe(feedback.get("high_performers"))
    mech = _len_safe(feedback.get("effective_mechanisms"))
    bad = _len_safe(feedback.get("problematic_features"))
    base = (
        hp * high_performer_weight
        + mech * mechanism_weight
        - bad * problematic_weight
    )
    bonus_sum = 0.0
    n_scored = 0
    pa = feedback.get("property_analysis") or {}
    if isinstance(pa, dict):
        for _name, row in pa.items():
            if not isinstance(row, dict):
                continue
            e_int = row.get("interaction_energy_kj_mol")
            rmsd = row.get("insulin_rmsd_to_initial_nm")
            if (
                use_composite
                and e_int is not None
                and rmsd is not None
                and isinstance(e_int, (int, float))
                and isinstance(rmsd, (int, float))
            ):
                bonus_sum += composite_screening_score(e_int, rmsd) * composite_scale
                n_scored += 1
            elif e_int is not None and isinstance(e_int, (int, float)):
                bonus_sum += (-float(e_int)) * interaction_scale
                n_scored += 1
    avg_bonus = bonus_sum / n_scored if n_scored > 0 else 0.0
    return base + avg_bonus


def _len_safe(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, str):
        return 1 if value.strip() else 0
    return 1
