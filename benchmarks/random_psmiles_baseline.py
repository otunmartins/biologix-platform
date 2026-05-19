#!/usr/bin/env python3
"""
Random PSMILES baseline: cheminformatic mutation with **no** MD feedback loop.

Each batch draws a random ``mutator_seed`` and calls
:func:`insulin_ai.mutation.feedback_guided_mutation` with ``feedback_fraction=0``
(pure :class:`~insulin_ai.mutation.generator.MaterialMutator` draws). Feedback state
is **not** updated from OpenMM results, so exploration is memoryless aside from the
initial validated seed (used only for parity with other benchmarks).

Stops after ``n_evaluations`` **successful** OpenMM evaluations (interaction energy
present), matching :func:`benchmarks.ibm_insulin_rl_benchmark.test_model` counting.
Default CLI budget **160** matches **20** discovery iterations × **8** evals/iteration
(IBM / paper study parity in ``run_paper_study.sh``).

References
----------
See ``docs/BENCHMARK_AND_REPRO_STUDY.md`` and ``docs/THIRD_PARTY_BENCHMARKS.md``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from benchmarks.ibm_insulin_rl_benchmark import (  # noqa: E402
    _running_best_interaction_energy,
    append_comparison_tsv,
    make_comparison_row,
)


def run_random_psmiles_baseline(
    seed_psmiles: str,
    n_evaluations: int,
    *,
    library_size: int = 4,
    random_seed: int = 42,
    mutator_seed_high: int = 1_000_000,
    md_steps: int = 5000,
    verbose_eval: bool = False,
    evaluate_candidates_fn: Optional[
        Callable[[List[Dict[str, Any]], int], Dict[str, Any]]
    ] = None,
) -> Dict[str, Any]:
    """
    Run random baseline until ``n_evaluations`` successful MD evaluations.

    Args:
        seed_psmiles: Initial PSMILES (validated; canonical form logged).
        n_evaluations: Target count of completed evaluations with interaction energy.
        library_size: Candidates proposed per batch (before validation).
        random_seed: RNG for choosing ``mutator_seed`` each batch.
        mutator_seed_high: Upper bound for mutator RNG (inclusive).
        md_steps: Passed to :class:`~insulin_ai.simulation.md_simulator.MDSimulator`.
        verbose_eval: Forwarded to ``evaluate_candidates``.
        evaluate_candidates_fn: Inject mock for tests; default live OpenMM.

    Returns:
        JSON-serialisable dict including ``evaluation_trace`` and aggregate metrics.
    """
    from insulin_ai.material_mappings import validate_psmiles
    from insulin_ai.mutation import feedback_guided_mutation
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.scoring import discovery_score

    vr = validate_psmiles(seed_psmiles.strip())
    if not vr.get("valid"):
        return {
            "error": vr.get("error", "invalid seed PSMILES"),
            "seed_psmiles": seed_psmiles,
        }

    seed_canonical = str(vr.get("canonical") or seed_psmiles.strip())
    use_openmm = evaluate_candidates_fn is None
    if use_openmm and not openmm_available():
        return {
            "error": "OpenMM stack not available. Install with pip install -e '.[openmm]' "
            "or use evaluate_candidates_fn for tests.",
            "seed_psmiles": seed_psmiles,
        }

    sim: Any = None
    if use_openmm:
        from insulin_ai.simulation import MDSimulator

        sim = MDSimulator(n_steps=md_steps, random_seed=random_seed)

    # Memoryless feedback: no high performers / problems from MD (plan: no feedback).
    feedback_state: Dict[str, Any] = {
        "high_performer_psmiles": [],
        "problematic_psmiles": [],
        "top_candidates": [],
        "stability_mechanisms": [],
        "limitations": [],
        "target_properties": {},
    }

    rng = random.Random(random_seed)
    evaluation_trace: List[Dict[str, Any]] = []
    all_md_raw: List[Optional[Dict[str, Any]]] = []
    all_names: List[str] = []
    n_done = 0
    batch_idx = 0
    max_batches = max(500, n_evaluations * 20)

    t0 = time.perf_counter()
    while n_done < n_evaluations and batch_idx < max_batches:
        batch_idx += 1
        mut_seed = rng.randint(0, mutator_seed_high)
        mutated = feedback_guided_mutation(
            feedback_state,
            library_size=library_size,
            feedback_fraction=0.0,
            random_seed=mut_seed,
        )
        valid_cands: List[Dict[str, Any]] = []
        for c in mutated:
            psm = c.get("chemical_structure") or c.get("psmiles")
            if not psm:
                continue
            v = validate_psmiles(str(psm))
            if not v.get("valid"):
                continue
            c2 = dict(c)
            c2["chemical_structure"] = str(v.get("canonical", psm))
            valid_cands.append(c2)

        if not valid_cands:
            continue

        remaining = n_evaluations - n_done
        max_c = min(len(valid_cands), remaining)
        if evaluate_candidates_fn is not None:
            md_results = evaluate_candidates_fn(valid_cands, max_c)
        else:
            assert sim is not None
            md_results = sim.evaluate_candidates(
                valid_cands, max_candidates=max_c, verbose=verbose_eval
            )

        raw = md_results.get("md_results_raw") or []
        for i, res in enumerate(raw):
            if i >= len(valid_cands):
                break
            name = str(
                valid_cands[i].get("material_name")
                or valid_cands[i].get("candidate_id")
                or f"batch{batch_idx}_{i}"
            )
            all_md_raw.append(res)
            all_names.append(name)
            if res and res.get("interaction_energy_kj_mol") is not None:
                psm = res.get("psmiles") or valid_cands[i].get("chemical_structure")
                evaluation_trace.append(
                    {
                        "batch": batch_idx,
                        "interaction_energy_kj_mol": float(
                            res["interaction_energy_kj_mol"]
                        ),
                        "psmiles": str(psm) if psm else "",
                        "phase": "random",
                    }
                )
                n_done += 1
                if n_done >= n_evaluations:
                    break

    wall_time = time.perf_counter() - t0

    # Aggregate metrics (align with IBM benchmark post-processing)
    feedback_final = {"property_analysis": {}}
    if sim is not None and all_md_raw:
        feedback_final = sim.extractor.extract_feedback(all_md_raw, all_names)
    elif evaluate_candidates_fn is not None:
        # Mock path: build minimal property_analysis from trace
        pa: Dict[str, Any] = {}
        for e in evaluation_trace:
            psm = e.get("psmiles") or "x"
            pa[psm] = {"interaction_energy_kj_mol": e["interaction_energy_kj_mol"]}
        feedback_final = {
            "high_performers": [],
            "effective_mechanisms": [],
            "problematic_features": [],
            "property_analysis": pa,
        }

    d_score = discovery_score(
        {
            "high_performers": feedback_final.get("high_performers") or [],
            "effective_mechanisms": feedback_final.get("effective_mechanisms") or [],
            "problematic_features": feedback_final.get("problematic_features") or [],
            "property_analysis": feedback_final.get("property_analysis") or {},
        }
    )

    energies = [
        float(e["interaction_energy_kj_mol"])
        for e in evaluation_trace
        if e.get("interaction_energy_kj_mol") is not None
    ]
    uniq_psmiles = {e.get("psmiles") for e in evaluation_trace if e.get("psmiles")}
    hp_names = feedback_final.get("high_performers") or []

    return {
        "method": "random_psmiles",
        "seed_psmiles": seed_psmiles,
        "seed_canonical": seed_canonical,
        "n_evaluations_target": n_evaluations,
        "n_evaluations": len(evaluation_trace),
        "n_batches": batch_idx,
        "library_size": library_size,
        "random_seed": random_seed,
        "best_discovery_score": round(float(d_score), 4),
        "best_interaction_energy_kj_mol": round(min(energies), 4) if energies else None,
        "n_high_performers_found": len(set(hp_names)) if hp_names else 0,
        "n_unique_psmiles_evaluated": len(uniq_psmiles),
        "wall_time_s": round(wall_time, 1),
        "evaluation": "injected" if evaluate_candidates_fn else "live_openmm",
        "evaluation_trace": evaluation_trace,
        "running_best_interaction_energy_kj_mol": _running_best_interaction_energy(
            evaluation_trace
        ),
        "energy_stats": {
            "mean": round(sum(energies) / len(energies), 4) if energies else None,
            "min": round(min(energies), 4) if energies else None,
            "max": round(max(energies), 4) if energies else None,
            "n": len(energies),
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Random PSMILES baseline (no MD feedback); fixed evaluation budget."
    )
    p.add_argument("--seed", type=str, required=True, help="Seed PSMILES with [*]")
    p.add_argument(
        "--n-evaluations",
        type=int,
        default=160,
        help="Target successful OpenMM evaluations (default: 160 = 20 iter × 8 evals)",
    )
    p.add_argument(
        "--library-size",
        type=int,
        default=8,
        help="Candidates per batch before validation (default: 8, matches evals/iteration)",
    )
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--md-steps", type=int, default=5000)
    p.add_argument("--verbose-eval", action="store_true")
    p.add_argument("--output", type=str, default=None, help="Write full JSON results here")
    p.add_argument(
        "--comparison-tsv",
        type=str,
        default=None,
        help="Append one row to shared comparison TSV (IBM schema)",
    )
    args = p.parse_args()

    out = run_random_psmiles_baseline(
        args.seed,
        args.n_evaluations,
        library_size=args.library_size,
        random_seed=args.random_seed,
        md_steps=args.md_steps,
        verbose_eval=args.verbose_eval,
    )

    if args.output:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    if args.comparison_tsv and "error" not in out:
        row = make_comparison_row(
            method="random_psmiles",
            n_evaluations=out.get("n_evaluations"),
            best_discovery_score=out.get("best_discovery_score"),
            best_interaction_energy_kj_mol=out.get("best_interaction_energy_kj_mol"),
            n_high_performers_found=out.get("n_high_performers_found"),
            n_unique_psmiles_evaluated=out.get("n_unique_psmiles_evaluated"),
            wall_time_s=out.get("wall_time_s"),
            algorithm="",
            n_timesteps_trained=None,
            avg_episode_reward=None,
            avg_targets_per_episode=None,
            avg_steps_to_first_target=None,
            avg_episode_length=None,
            seed_psmiles=args.seed.strip(),
            n_proposals=args.library_size,
            target_energy_kj=None,
            notes=f"live_openmm,random_seed={args.random_seed}"
            if out.get("evaluation") == "live_openmm"
            else f"injected,random_seed={args.random_seed}",
        )
        append_comparison_tsv(args.comparison_tsv, row)

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
