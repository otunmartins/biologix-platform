"""
Tests for benchmarks/random_psmiles_baseline.py (no OpenMM in fast path).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))
sys.path.insert(0, ROOT)

from benchmarks.random_psmiles_baseline import run_random_psmiles_baseline  # noqa: E402


def _mock_evaluate(candidates, max_candidates):
    slice_c = candidates[:max_candidates]
    raw = []
    for c in slice_c:
        psm = c.get("chemical_structure") or "[*]CC[*]"
        raw.append(
            {
                "ok": True,
                "psmiles": psm,
                "interaction_energy_kj_mol": -200.0,
                "insulin_rmsd_to_initial_nm": 0.05,
            }
        )
    name = slice_c[0].get("material_name", "m0") if slice_c else "m0"
    return {
        "high_performers": [name],
        "effective_mechanisms": ["OpenMM_merged_screening"],
        "problematic_features": [],
        "successful_materials": [name],
        "property_analysis": {
            name: {
                "interaction_energy_kj_mol": -200.0,
                "insulin_rmsd_to_initial_nm": 0.05,
            }
        },
        "md_results_raw": raw,
    }


def test_random_baseline_mock_reaches_budget():
    out = run_random_psmiles_baseline(
        "[*]OCC[*]",
        n_evaluations=8,
        library_size=2,
        random_seed=0,
        evaluate_candidates_fn=_mock_evaluate,
    )
    assert "error" not in out
    assert out["n_evaluations"] == 8
    assert out["best_interaction_energy_kj_mol"] == -200.0
    assert len(out["evaluation_trace"]) == 8
