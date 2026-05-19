#!/usr/bin/env python3
"""
Optuna-driven PSMILES discovery benchmark (agent-free, MCP-free, LLM-free).

**Environment:** Run with the repo's simulation conda env ``insulin-ai-sim`` (see
``environment-simulation.yml``) — the same stack as OpenMM screening and MCP, with Optuna
included. Example::

    mamba run -n insulin-ai-sim python benchmarks/optuna_psmiles_discovery.py --seed '[*]OCC[*]' --n-trials 5

Using a bare system Python without OpenMM/RDKit/psmiles will fail real evaluation; the
``evaluate_candidates_fn`` hook is for tests only.

This script optimizes the same scalar :func:`insulin_ai.simulation.scoring.discovery_score`
used when screening candidates in the insulin-ai stack. Each Optuna trial proposes
polymer candidates via cheminformatic mutation (:func:`insulin_ai.mutation.feedback_guided_mutation`),
validates PSMILES, runs OpenMM merged minimize + interaction energy
(:class:`insulin_ai.simulation.md_simulator.MDSimulator`), then updates feedback state for
the next trial. It does **not** use literature mining, MCP tools, or any language model.

**Search space.** PSMILES live in a discrete/combinatorial space. Optuna does not embed
strings into R^n here; trials expose *discrete* knobs (e.g. ``mutator_seed``,
``feedback_fraction``) that deterministically drive the existing mutator—standard
*black-box* optimization over expensive simulations.

**References**

1. Akiba, T., Sano, S., Yanase, T., Ohta, T., & Koyama, M. (2019). Optuna: A Next-generation
   Hyperparameter Optimization Framework. *KDD '19*, 2623--2631.
   https://doi.org/10.1145/3292500.3330701

2. Bergstra, J., Bardenet, R., Bengio, Y., & Kégl, B. (2011). Algorithms for Hyper-Parameter
   Optimization. *NeurIPS* — Tree-structured Parzen Estimator (TPE); Optuna's
   :class:`optuna.samplers.TPESampler` implements this family.

3. Korovina, K., et al. (2020). ChemBO: Bayesian Optimization of Small Organic Molecules with
   Synthesizable Recommendations. *AISTATS*, PMLR 108:1773--1783 — sample-efficient
   black-box optimization over structured chemical spaces (conceptual parallel; this benchmark
   uses rule-based mutation + physics, not synthesis-graph kernels).

4. Griffiths, R.-R., & Hernández-Lobato, J. M. (2020). Constrained Bayesian optimization for
   automatic chemical design using variational autoencoders. *Chemical Science* — contrast:
   latent continuous BO; **this benchmark** stays in discrete PSMILES + OpenMM screening.

5. Optuna tutorial — Pythonic search space (categorical / mixed):
   https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/002_configurations.html
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Repo src layout (benchmarks/ at repo root)
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

DEFAULT_FEEDBACK_FRACTIONS = (0.5, 0.7, 0.9)
# TPE-friendly integer range (not full 2**31; keeps trials well-conditioned)
DEFAULT_MUTATOR_SEED_HIGH = 1_000_000


def _update_feedback_from_md(
    md_results: Dict[str, Any],
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Align with ``IterativeLiteratureMiner._update_feedback_state`` (no literature imports)."""
    top = md_results.get("successful_materials", md_results.get("high_performers", []))
    limitations = md_results.get("failed_features", md_results.get("problematic_features", []))

    name_to_psmiles: Dict[str, str] = {}
    if candidates:
        for c in candidates:
            name = c.get("material_name") or c.get("candidate_id")
            psm = c.get("chemical_structure") or c.get("psmiles")
            if name and psm:
                name_to_psmiles[str(name)] = psm

    high_psmiles: List[str] = []
    for name in top:
        psm = name_to_psmiles.get(str(name)) or (
            name if name and ("[*]" in str(name)) else None
        )
        if psm:
            high_psmiles.append(psm)

    problematic_psmiles: List[str] = []
    for item in limitations:
        if not isinstance(item, str):
            continue
        if "[*]" in item:
            problematic_psmiles.append(item)
            continue
        candidate_name = item.split(":", 1)[1] if ":" in item else item
        psm = name_to_psmiles.get(candidate_name)
        if psm:
            problematic_psmiles.append(psm)

    return {
        "top_candidates": top,
        "stability_mechanisms": md_results.get("effective_mechanisms", []),
        "target_properties": md_results.get("target_improvements", {}),
        "limitations": limitations,
        "high_performer_psmiles": high_psmiles,
        "problematic_psmiles": problematic_psmiles,
    }


def run_optuna_benchmark(
    seed_psmiles: str,
    n_trials: int,
    *,
    library_size_per_trial: int = 4,
    random_seed: int = 42,
    mutator_seed_high: int = DEFAULT_MUTATOR_SEED_HIGH,
    feedback_fractions: tuple[float, ...] = DEFAULT_FEEDBACK_FRACTIONS,
    md_steps: int = 5000,
    verbose_eval: bool = False,
    evaluate_candidates_fn: Optional[
        Callable[[List[Dict[str, Any]], int], Dict[str, Any]]
    ] = None,
) -> Dict[str, Any]:
    """
    Run an Optuna study maximizing :func:`insulin_ai.simulation.scoring.discovery_score`.

    Args:
        seed_psmiles: Initial PSMILES (must contain ``[*]``); validated before the study.
        n_trials: Number of Optuna trials.
        library_size_per_trial: Candidates generated per trial before validation/filtering.
        random_seed: Optuna sampler seed.
        mutator_seed_high: Upper bound (inclusive) for ``trial.suggest_int("mutator_seed", ...)``.
        feedback_fractions: Categorical choices for ``feedback_guided_mutation``.
        md_steps: Passed to :class:`~insulin_ai.simulation.md_simulator.MDSimulator`.
        verbose_eval: Forwarded to OpenMM evaluation when using the real simulator.
        evaluate_candidates_fn: Inject mock for tests; signature
            ``(candidates, max_candidates) -> md_result_dict``. If ``None``, uses ``MDSimulator``.

    Returns:
        Dict with ``study_summary``, ``best_trial``, ``seed_canonical``, ``error`` (if any).
    """
    import optuna
    from optuna.samplers import TPESampler

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

    feedback_state: Dict[str, Any] = {
        "high_performer_psmiles": [seed_canonical],
        "problematic_psmiles": [],
        "top_candidates": [],
        "stability_mechanisms": [],
        "limitations": [],
        "target_properties": {},
    }

    evaluation_trace: List[Dict[str, Any]] = []
    all_md_raw: List[Optional[Dict[str, Any]]] = []
    all_names: List[str] = []

    def objective(trial: optuna.Trial) -> float:
        nonlocal feedback_state
        mut_seed = trial.suggest_int("mutator_seed", 0, mutator_seed_high)
        fb_frac = trial.suggest_categorical("feedback_fraction", list(feedback_fractions))

        mutated = feedback_guided_mutation(
            feedback_state,
            library_size=library_size_per_trial,
            feedback_fraction=fb_frac,
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
            trial.set_user_attr("fail_reason", "no_valid_psmiles_after_validation")
            return float("-inf")

        if evaluate_candidates_fn is not None:
            md_results = evaluate_candidates_fn(valid_cands, len(valid_cands))
        else:
            assert sim is not None
            md_results = sim.evaluate_candidates(
                valid_cands, max_candidates=len(valid_cands), verbose=verbose_eval
            )

        raw = md_results.get("md_results_raw") or []
        for i, res in enumerate(raw):
            if i >= len(valid_cands):
                break
            name = str(
                valid_cands[i].get("material_name")
                or valid_cands[i].get("candidate_id")
                or f"trial{trial.number}_{i}"
            )
            all_md_raw.append(res)
            all_names.append(name)
            if res and res.get("interaction_energy_kj_mol") is not None:
                psm = res.get("psmiles") or valid_cands[i].get("chemical_structure")
                evaluation_trace.append(
                    {
                        "trial": trial.number,
                        "interaction_energy_kj_mol": float(
                            res["interaction_energy_kj_mol"]
                        ),
                        "psmiles": str(psm) if psm else "",
                        "phase": "optuna",
                    }
                )

        score = discovery_score(md_results)
        trial.set_user_attr("discovery_score", score)
        trial.set_user_attr("n_candidates_evaluated", len(valid_cands))

        state = _update_feedback_from_md(md_results, valid_cands)
        merged_hp = list(
            dict.fromkeys([seed_canonical] + (state.get("high_performer_psmiles") or []))
        )
        state["high_performer_psmiles"] = merged_hp
        feedback_state = state
        return float(score)

    study = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=random_seed),
    )
    t0 = time.perf_counter()
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    wall_time = time.perf_counter() - t0

    n_completed = len(
        [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    )

    feedback_final: Dict[str, Any] = {
        "high_performers": [],
        "effective_mechanisms": [],
        "problematic_features": [],
        "property_analysis": {},
    }
    if sim is not None and all_md_raw:
        feedback_final = sim.extractor.extract_feedback(all_md_raw, all_names)
    elif evaluate_candidates_fn is not None and evaluation_trace:
        pa = {}
        for e in evaluation_trace:
            psm = e.get("psmiles") or "x"
            pa[psm] = {"interaction_energy_kj_mol": e["interaction_energy_kj_mol"]}
        feedback_final = {
            "high_performers": [],
            "effective_mechanisms": [],
            "problematic_features": [],
            "property_analysis": pa,
        }

    merged_score = discovery_score(
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
    hp_names = feedback_final.get("high_performers") or []

    best_trial_payload: Optional[Dict[str, Any]] = None
    best_value: Optional[float] = None
    if n_completed > 0:
        try:
            best = study.best_trial
            best_value = float(study.best_value)
            best_trial_payload = {
                "number": best.number,
                "value": best.value,
                "params": best.params,
                "user_attrs": dict(best.user_attrs),
            }
        except ValueError:
            pass

    out: Dict[str, Any] = {
        "method": "optuna_tpe",
        "seed_canonical": seed_canonical,
        "n_trials": n_trials,
        "library_size_per_trial": library_size_per_trial,
        "random_seed": random_seed,
        "best_value": best_value,
        "best_trial": best_trial_payload,
        "study_summary": {"n_completed": n_completed},
        "n_evaluations": len(evaluation_trace),
        "best_discovery_score": round(float(merged_score), 4),
        "best_interaction_energy_kj_mol": round(min(energies), 4) if energies else None,
        "n_high_performers_found": len(set(hp_names)) if hp_names else 0,
        "n_unique_psmiles_evaluated": len(
            {e.get("psmiles") for e in evaluation_trace if e.get("psmiles")}
        ),
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
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Optuna benchmark: maximize discovery_score over PSMILES mutations (OpenMM)."
    )
    p.add_argument("--seed", type=str, required=True, help="Seed PSMILES with [*]")
    p.add_argument("--n-trials", type=int, default=5)
    p.add_argument("--library-size", type=int, default=4, help="Candidates per trial (mutation pool)")
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

    out = run_optuna_benchmark(
        args.seed,
        args.n_trials,
        library_size_per_trial=args.library_size,
        random_seed=args.random_seed,
        md_steps=args.md_steps,
        verbose_eval=args.verbose_eval,
    )

    if args.output and "error" not in out:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    if args.comparison_tsv and "error" not in out:
        row = make_comparison_row(
            method="optuna_tpe",
            n_evaluations=out.get("n_evaluations"),
            best_discovery_score=out.get("best_discovery_score"),
            best_interaction_energy_kj_mol=out.get("best_interaction_energy_kj_mol"),
            n_high_performers_found=out.get("n_high_performers_found"),
            n_unique_psmiles_evaluated=out.get("n_unique_psmiles_evaluated"),
            wall_time_s=out.get("wall_time_s"),
            algorithm="tpe",
            n_timesteps_trained=None,
            avg_episode_reward=None,
            avg_targets_per_episode=None,
            avg_steps_to_first_target=None,
            avg_episode_length=None,
            seed_psmiles=args.seed.strip(),
            n_proposals=args.library_size,
            target_energy_kj=None,
            notes=(
                f"live_openmm,n_trials={args.n_trials},random_seed={args.random_seed}"
                if out.get("evaluation") == "live_openmm"
                else f"injected,n_trials={args.n_trials},random_seed={args.random_seed}"
            ),
        )
        append_comparison_tsv(args.comparison_tsv, row)

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
