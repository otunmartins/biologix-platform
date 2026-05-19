"""Tests for benchmarks/append_optuna_comparison_rows.py."""

from pathlib import Path

import pytest


def test_comparison_row_from_optuna_json_minimal():
    from benchmarks.append_optuna_comparison_rows import comparison_row_from_optuna_json

    data = {
        "seed_canonical": "[*]CC[*]",
        "n_trials": 5,
        "random_seed": 99,
        "library_size_per_trial": 8,
        "evaluation": "live_openmm",
        "n_evaluations": 12,
        "best_discovery_score": 10.5,
        "best_interaction_energy_kj_mol": -100.0,
        "n_high_performers_found": 2,
        "n_unique_psmiles_evaluated": 5,
        "wall_time_s": 123.4,
    }
    row = comparison_row_from_optuna_json(data)
    assert row["method"] == "optuna_tpe"
    assert row["algorithm"] == "tpe"
    assert row["seed_psmiles"] == "[*]CC[*]"
    assert row["n_proposals"] == 8
    assert "random_seed=99" in (row.get("notes") or "")
    assert row["n_evaluations"] == 12


def test_comparison_row_from_optuna_json_rejects_error_payload():
    from benchmarks.append_optuna_comparison_rows import comparison_row_from_optuna_json

    with pytest.raises(ValueError, match="error"):
        comparison_row_from_optuna_json({"error": "failed", "seed_canonical": "[*]C[*]"})


def test_append_optuna_comparison_rows_integration(tmp_path):
    """Append one row from real artifact if present (repo checkout)."""
    from benchmarks.append_optuna_comparison_rows import comparison_row_from_optuna_json
    from benchmarks.ibm_insulin_rl_benchmark import _COMPARISON_COLUMNS, append_comparison_tsv

    repo = Path(__file__).resolve().parents[1]
    js = repo / "results" / "optuna_seed42.json"
    if not js.is_file():
        pytest.skip("results/optuna_seed42.json not in checkout")
    import json

    data = json.loads(js.read_text(encoding="utf-8"))
    row = comparison_row_from_optuna_json(data)
    tsv = tmp_path / "cmp.tsv"
    append_comparison_tsv(str(tsv), row)
    text = tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 2
    header = text[0].split("\t")
    assert header == _COMPARISON_COLUMNS
