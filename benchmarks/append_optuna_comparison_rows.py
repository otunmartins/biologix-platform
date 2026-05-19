#!/usr/bin/env python3
"""
Append one comparison TSV row per completed Optuna benchmark JSON.

Use after parallel ``optuna_psmiles_discovery`` runs that omitted ``--comparison-tsv``
to avoid interleaved writes. Rows match :func:`optuna_psmiles_discovery.main` logic.

Example::

    python benchmarks/append_optuna_comparison_rows.py \\
        --tsv benchmarks/comparison_results_study.tsv \\
        results/optuna_seed123.json results/optuna_seed456.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from benchmarks.ibm_insulin_rl_benchmark import (  # noqa: E402
    append_comparison_tsv,
    make_comparison_row,
)


def comparison_row_from_optuna_json(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the same row dict as ``optuna_psmiles_discovery.main`` after a run."""
    if "error" in data and data["error"]:
        raise ValueError(f"JSON has error: {data.get('error')}")
    seed_psmiles = (data.get("seed_canonical") or "").strip()
    if not seed_psmiles:
        raise ValueError("JSON missing seed_canonical (cannot build TSV row)")
    n_trials = int(data.get("n_trials", 0))
    random_seed = data.get("random_seed")
    library_size = int(data.get("library_size_per_trial", 0))
    evaluation = data.get("evaluation", "live_openmm")
    notes = (
        f"live_openmm,n_trials={n_trials},random_seed={random_seed}"
        if evaluation == "live_openmm"
        else f"injected,n_trials={n_trials},random_seed={random_seed}"
    )
    return make_comparison_row(
        method="optuna_tpe",
        n_evaluations=data.get("n_evaluations"),
        best_discovery_score=data.get("best_discovery_score"),
        best_interaction_energy_kj_mol=data.get("best_interaction_energy_kj_mol"),
        n_high_performers_found=data.get("n_high_performers_found"),
        n_unique_psmiles_evaluated=data.get("n_unique_psmiles_evaluated"),
        wall_time_s=data.get("wall_time_s"),
        algorithm="tpe",
        n_timesteps_trained=None,
        avg_episode_reward=None,
        avg_targets_per_episode=None,
        avg_steps_to_first_target=None,
        avg_episode_length=None,
        seed_psmiles=seed_psmiles,
        n_proposals=library_size,
        target_energy_kj=None,
        notes=notes,
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Append Optuna benchmark rows to comparison TSV from JSON artifacts."
    )
    p.add_argument(
        "--tsv",
        type=str,
        required=True,
        help="Target comparison TSV (same schema as ibm_insulin_rl_benchmark)",
    )
    p.add_argument(
        "json_paths",
        nargs="+",
        type=str,
        help="One or more results/optuna_seed*.json files",
    )
    args = p.parse_args()
    tsv_path = Path(args.tsv)
    for raw in args.json_paths:
        path = Path(raw)
        if not path.is_file():
            print(f"error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            row = comparison_row_from_optuna_json(data)
        except ValueError as e:
            print(f"error: {path}: {e}", file=sys.stderr)
            sys.exit(1)
        append_comparison_tsv(str(tsv_path), row)
        print(f"appended row from {path} -> {tsv_path}")


if __name__ == "__main__":
    main()
