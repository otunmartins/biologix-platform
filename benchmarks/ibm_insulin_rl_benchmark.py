#!/usr/bin/env python3
"""
IBM Logical-Agent RL benchmark adapted for the biologix-ai evaluation pipeline.

Trains and tests DQN or PPO policies from ``stable-baselines3`` on the
``LogicalInsulinPSMILESEnv`` Gym environment, which uses the **same**
evaluation pipeline as the agentic MCP ``openmm_evaluate_psmiles`` tool:

    MDSimulator.evaluate_candidates
    → PropertyExtractor.extract_feedback
    → scoring.composite_screening_score / discovery_score

IBM's optimization loop (neuro-symbolic RL with logical action-aware features)
therefore drives *which* polymer to evaluate next, while the evaluation and
scoring are identical across benchmark systems.

References
----------
1. IBM logical-agent-driven polymer discovery:
   https://github.com/IBM/logical-agent-driven-polymer-discovery
   Reinforcement Learning with Logical Action-Aware Features for Polymer
   Discovery. RL4RealLife @ ICML 2021.

2. Raffin, A., et al. (2021). Stable-Baselines3: Reliable Reinforcement
   Learning Implementations. *JMLR* 22(268):1–8.
   https://jmlr.org/papers/v22/20-1364.html

3. Mnih, V., et al. (2015). Human-level control through deep reinforcement
   learning. *Nature* 518, 529–533.

4. Schulman, J., et al. (2017). Proximal Policy Optimization Algorithms.
   arXiv:1707.06347.

Usage
-----
.. code-block:: bash

    # Defaults match 20 agentic iterations × 10 evals (see --agentic-iterations).
    # Each new PSMILES is evaluated with live OpenMM (same as agentic MCP).
    python benchmarks/ibm_insulin_rl_benchmark.py \\
        --output results/ibm_dqn.json

    # Test a saved model
    python benchmarks/ibm_insulin_rl_benchmark.py \\
        --mode test --algorithm dqn \\
        --model-path models/ibm_dqn_insulin.zip \\
        --output results/ibm_dqn_test.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Parity with agentic discovery: ``run_autonomous_discovery_loop`` uses up to
# ``max_eval_per_iteration`` (default 8 in ``autonomous_discovery.main``).
# CLI ``--agentic-iterations`` × ``--evals-per-iteration`` sets training length
# and test episode count unless individual flags override.
_DEFAULT_AGENTIC_ITERATIONS = 20
_DEFAULT_EVALS_PER_AGENTIC_ITERATION = 10
# Module-level defaults (same as resolved parity with defaults above):
_AGENTIC_ITERATIONS_EQUIVALENT = _DEFAULT_AGENTIC_ITERATIONS
_AGENTIC_EVALS_PER_ITERATION = _DEFAULT_EVALS_PER_AGENTIC_ITERATION
DEFAULT_N_TIMESTEPS = _AGENTIC_ITERATIONS_EQUIVALENT * _AGENTIC_EVALS_PER_ITERATION
DEFAULT_MAX_STEPS_ENV = _AGENTIC_EVALS_PER_ITERATION
DEFAULT_N_PROPOSALS = _AGENTIC_EVALS_PER_ITERATION
DEFAULT_N_EPISODES = _AGENTIC_ITERATIONS_EQUIVALENT


def resolve_agentic_parity_settings(
    agentic_iterations: int,
    evals_per_iteration: int,
    n_timesteps: Optional[int] = None,
    n_episodes: Optional[int] = None,
    n_proposals: Optional[int] = None,
    max_steps: Optional[int] = None,
) -> Dict[str, int]:
    """Derive benchmark sizes from discovery-iteration parity.

    Training: ``n_timesteps = iterations × evals`` (one RL env step per pick).
    Testing: ``n_episodes = iterations`` (one rollout per notional iteration).
    Pool: ``n_proposals`` and ``max_steps`` default to ``evals_per_iteration``.
    Any argument that is not ``None`` is passed through unchanged.
    """
    if agentic_iterations < 1:
        raise ValueError("agentic_iterations must be >= 1")
    if evals_per_iteration < 1:
        raise ValueError("evals_per_iteration must be >= 1")
    return {
        "n_timesteps": (
            n_timesteps
            if n_timesteps is not None
            else agentic_iterations * evals_per_iteration
        ),
        "n_episodes": (
            n_episodes if n_episodes is not None else agentic_iterations
        ),
        "n_proposals": (
            n_proposals if n_proposals is not None else evals_per_iteration
        ),
        "max_steps": max_steps if max_steps is not None else evals_per_iteration,
    }


# ---------------------------------------------------------------------------
# Shared comparison row schema
# ---------------------------------------------------------------------------
_COMPARISON_COLUMNS = [
    "method",
    "n_evaluations",
    "best_discovery_score",
    "best_interaction_energy_kj_mol",
    "n_high_performers_found",
    "n_unique_psmiles_evaluated",
    "wall_time_s",
    "algorithm",
    "n_timesteps_trained",
    "avg_episode_reward",
    "avg_targets_per_episode",
    "avg_steps_to_first_target",
    "avg_episode_length",
    "seed_psmiles",
    "n_proposals",
    "target_energy_kj",
    "notes",
]


def make_comparison_row(**kwargs: Any) -> Dict[str, Any]:
    """Return a dict with all comparison columns, filling missing with None."""
    return {col: kwargs.get(col) for col in _COMPARISON_COLUMNS}


def _min_interaction_energy_in_phase(
    trace: List[Dict[str, Any]], phase: str
) -> Optional[float]:
    """Minimum interaction energy among ``evaluation_trace`` rows for a phase."""
    best: Optional[float] = None
    for e in trace:
        if e.get("phase") != phase:
            continue
        v = e.get("interaction_energy_kj_mol")
        if v is not None:
            fv = float(v)
            best = fv if best is None else min(best, fv)
    return best


def _running_best_interaction_energy(
    trace: List[Dict[str, Any]],
) -> List[Optional[float]]:
    """Cumulative minimum interaction energy (kJ/mol) after each logged eval."""
    out: List[Optional[float]] = []
    best: Optional[float] = None
    for entry in trace:
        e = entry.get("interaction_energy_kj_mol")
        if e is not None:
            fe = float(e)
            best = fe if best is None else min(best, fe)
        out.append(best)
    return out


def append_comparison_tsv(tsv_path: str, row: Dict[str, Any]) -> None:
    """Append one comparison row to a TSV file, creating header if needed."""
    p = Path(tsv_path)
    write_header = not p.is_file()
    with open(p, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COMPARISON_COLUMNS, delimiter="\t")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_model(
    algorithm: str,
    env_kwargs: Dict[str, Any],
    n_timesteps: int,
    model_path: Optional[str],
    random_seed: int,
    n_envs: int = 1,
    verbose: int = 0,
) -> Any:
    """Train a DQN or PPO policy on LogicalInsulinPSMILESEnv.

    Args:
        algorithm: ``"dqn"`` or ``"ppo"``.
        env_kwargs: Keyword arguments forwarded to ``LogicalInsulinPSMILESEnv``.
        n_timesteps: Total training steps.
        model_path: Optional path to save the trained model.
        random_seed: Seed for SB3 and the environment.
        n_envs: Parallel environments (PPO only; DQN ignores this).
        verbose: SB3 verbosity level (0=silent, 1=info).

    Returns:
        Trained SB3 model.
    """
    try:
        from stable_baselines3 import DQN, PPO
        from stable_baselines3.common.env_util import make_vec_env
    except ImportError as e:
        raise ImportError(
            "stable-baselines3 is required for RL training. "
            "Install with: pip install stable-baselines3"
        ) from e

    from benchmarks.ibm_insulin_env import LogicalInsulinPSMILESEnv

    def make_env():
        return LogicalInsulinPSMILESEnv(**env_kwargs)

    if algorithm == "dqn":
        env = make_env()
        model = DQN(
            "MlpPolicy",
            env,
            gamma=0.8,
            learning_rate=3e-4,
            buffer_size=20_000,
            batch_size=32,
            seed=random_seed,
            verbose=verbose,
        )
    elif algorithm == "ppo":
        vec_env = make_vec_env(make_env, n_envs=n_envs, seed=random_seed)
        model = PPO(
            "MlpPolicy",
            vec_env,
            gamma=0.8,
            n_steps=512,
            learning_rate=3e-4,
            seed=random_seed,
            verbose=verbose,
        )
    else:
        raise ValueError(f"algorithm must be 'dqn' or 'ppo', got '{algorithm}'")

    logger.info(
        "Training %s for %d timesteps (n_envs=%d) …",
        algorithm.upper(),
        n_timesteps,
        n_envs,
    )
    model.learn(total_timesteps=n_timesteps)

    if model_path:
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        model.save(model_path)
        logger.info("Model saved to %s", model_path)

    return model


# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

def test_model(
    model: Any,
    env_kwargs: Dict[str, Any],
    n_episodes: int,
    max_steps_per_episode: int = 100,
    vectorized_env: bool = False,
) -> Dict[str, Any]:
    """Run the trained model for n_episodes and collect metrics.

    Metrics mirror IBM upstream ``test_model``:
    - avg_reward, avg_targets, avg_episode_length, avg_steps_to_first_target
    Plus biologix-ai-specific:
    - best_discovery_score, best_interaction_energy_kj_mol,
      n_high_performers_found, all evaluated PSMILES
    """
    from benchmarks.ibm_insulin_env import LogicalInsulinPSMILESEnv
    from biologix_ai.simulation.scoring import discovery_score

    env = LogicalInsulinPSMILESEnv(**env_kwargs)

    log_rewards: List[float] = []
    log_targets: List[int] = []
    log_ep_lengths: List[int] = []
    log_first_target_steps: List[int] = []
    all_md_rows: List[Dict[str, Any]] = []
    all_target_psmiles: List[str] = []

    for episode in range(n_episodes):
        obs, _info = env.reset()
        ep_reward = 0.0
        targets = 0
        first_target_step: Optional[int] = None

        for step in range(max_steps_per_episode):
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            ep_reward += float(reward)

            if info.get("tier") == "target":
                targets += 1
                if first_target_step is None:
                    first_target_step = step + 1

            md_row = info.get("md_row") or {}
            if md_row:
                md_row["episode"] = episode
                md_row["step"] = step
                md_row["tier"] = info.get("tier")
                all_md_rows.append(md_row)
                if info.get("tier") == "target":
                    all_target_psmiles.append(info.get("psmiles", ""))

            if terminated or truncated:
                break

        log_rewards.append(ep_reward)
        log_targets.append(targets)
        log_ep_lengths.append(step + 1)
        if first_target_step is not None:
            log_first_target_steps.append(first_target_step)

        logger.info(
            "Episode %d/%d: reward=%.3f targets=%d length=%d",
            episode + 1,
            n_episodes,
            ep_reward,
            targets,
            step + 1,
        )

    # Compute discovery metrics from all evaluated rows
    energies = [
        r["interaction_energy_kj_mol"]
        for r in all_md_rows
        if r.get("interaction_energy_kj_mol") is not None
    ]
    high_performers = [r["psmiles"] for r in all_md_rows if r.get("tier") == "target"]

    import numpy as np

    # Build feedback dict for discovery_score (same as MCP)
    pa = {}
    for r in all_md_rows:
        ps = r.get("psmiles", "")
        if ps:
            pa[ps] = r
    feedback_for_score = {
        "high_performers": list(dict.fromkeys(high_performers)),
        "effective_mechanisms": ["favorable_interaction_energy"] if high_performers else [],
        "problematic_features": [],
        "property_analysis": pa,
    }
    d_score = discovery_score(feedback_for_score)

    return {
        "n_episodes": n_episodes,
        "avg_episode_reward": float(np.mean(log_rewards)) if log_rewards else 0.0,
        "avg_targets_per_episode": float(np.mean(log_targets)) if log_targets else 0.0,
        "avg_episode_length": float(np.mean(log_ep_lengths)) if log_ep_lengths else 0.0,
        "avg_steps_to_first_target": (
            float(np.mean(log_first_target_steps)) if log_first_target_steps else None
        ),
        "best_discovery_score": round(d_score, 4),
        "best_interaction_energy_kj_mol": round(min(energies), 4) if energies else None,
        "n_high_performers_found": len(set(high_performers)),
        "n_unique_psmiles_evaluated": len({r.get("psmiles") for r in all_md_rows}),
        "n_evaluations": len(all_md_rows),
        "all_target_psmiles": list(dict.fromkeys(all_target_psmiles)),
        "energy_stats": {
            "mean": round(float(np.mean(energies)), 4) if energies else None,
            "min": round(float(min(energies)), 4) if energies else None,
            "max": round(float(max(energies)), 4) if energies else None,
            "n": len(energies),
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_ibm_insulin_benchmark(
    mode: str = "train",
    algorithm: str = "dqn",
    seed_psmiles: str = "[*]OCC[*]",
    n_proposals: int = DEFAULT_N_PROPOSALS,
    max_steps: int = DEFAULT_MAX_STEPS_ENV,
    n_targets: int = 5,
    target_energy_kj: float = -5.0,
    md_steps: int = 5000,
    n_timesteps: int = DEFAULT_N_TIMESTEPS,
    n_episodes: int = DEFAULT_N_EPISODES,
    random_seed: int = 42,
    model_path: Optional[str] = None,
    evaluate_candidates_fn: Optional[
        Callable[[List[Dict[str, Any]], int], Dict[str, Any]]
    ] = None,
    comparison_tsv: Optional[str] = None,
    comparison_notes: str = "",
    verbose: int = 0,
) -> Dict[str, Any]:
    """Full train-or-test pipeline for the IBM insulin RL benchmark.

    Args:
        mode: ``"train"``, ``"test"``, or ``"train_and_test"``.
        algorithm: ``"dqn"`` or ``"ppo"``.
        seed_psmiles: Initial PSMILES.
        n_proposals: Candidates proposed per step.
        max_steps: Max episode length.
        n_targets: Early-stop targets per episode.
        target_energy_kj: Reward "target" threshold (kJ/mol).
        md_steps: OpenMM minimisation steps (per candidate).
        n_timesteps: RL training steps (CLI default: ``--agentic-iterations`` × ``--evals-per-iteration``).
        n_episodes: Test episodes (CLI default: ``--agentic-iterations``).
        random_seed: Global RNG seed.
        model_path: Save / load model here.
        evaluate_candidates_fn: Optional replacement for ``MDSimulator.evaluate_candidates``
            (tests only; default is live OpenMM).
        comparison_tsv: If set, append a comparison row to this TSV file.
        comparison_notes: Appended to the TSV ``notes`` column (comma-separated).
        verbose: SB3 verbosity (0=silent).

    Returns:
        JSON-serialisable result dict.
    """
    evaluation_trace: List[Dict[str, Any]] = []
    rl_step_progress_trace: List[Dict[str, Any]] = []
    env_kwargs: Dict[str, Any] = dict(
        seed_psmiles=seed_psmiles,
        n_proposals=n_proposals,
        max_steps=max_steps,
        n_targets=n_targets,
        target_energy_kj=target_energy_kj,
        md_steps=md_steps,
        random_seed=random_seed,
        evaluate_candidates_fn=evaluate_candidates_fn,
        evaluation_log=evaluation_trace,
        evaluation_log_phase="train",
        rl_step_progress_log=rl_step_progress_trace,
    )
    if mode == "test":
        env_kwargs["evaluation_log_phase"] = "test"

    t_start = time.perf_counter()
    result: Dict[str, Any] = {
        "algorithm": algorithm,
        "mode": mode,
        "seed_psmiles": seed_psmiles,
        "n_proposals": n_proposals,
        "target_energy_kj": target_energy_kj,
        "n_timesteps": n_timesteps,
        "n_episodes": n_episodes,
        "evaluation": "injected" if evaluate_candidates_fn else "live_openmm",
    }

    model = None

    if mode in ("train", "train_and_test"):
        model = train_model(
            algorithm=algorithm,
            env_kwargs=env_kwargs,
            n_timesteps=n_timesteps,
            model_path=model_path,
            random_seed=random_seed,
            verbose=verbose,
        )
        result["train_completed"] = True

    if mode == "test" or (mode == "train_and_test" and model is not None):
        env_kwargs["evaluation_log_phase"] = "test"
        if mode == "train_and_test":
            env_kwargs["initial_best_interaction_energy_kj_mol"] = (
                _min_interaction_energy_in_phase(evaluation_trace, "train")
            )
            env_kwargs["initial_global_rl_step"] = len(rl_step_progress_trace)
        if model is None:
            if not model_path:
                raise ValueError("--model-path required for --mode test")
            try:
                from stable_baselines3 import DQN, PPO
            except ImportError as e:
                raise ImportError("stable-baselines3 required") from e
            loader = DQN if algorithm == "dqn" else PPO
            model = loader.load(model_path)
            logger.info("Model loaded from %s", model_path)

        test_results = test_model(
            model=model,
            env_kwargs=env_kwargs,
            n_episodes=n_episodes,
            max_steps_per_episode=max_steps,
        )
        result.update(test_results)

    wall_time = time.perf_counter() - t_start
    result["wall_time_s"] = round(wall_time, 1)
    result["evaluation_trace"] = evaluation_trace
    result["rl_step_progress_trace"] = rl_step_progress_trace
    result["running_best_interaction_energy_kj_mol"] = (
        _running_best_interaction_energy(evaluation_trace)
    )

    # Append to comparison TSV
    if comparison_tsv:
        base_notes = "injected_evaluator" if evaluate_candidates_fn else "live_openmm"
        extra = comparison_notes.strip()
        notes_val = f"{base_notes},{extra}" if extra else base_notes
        row = make_comparison_row(
            method=f"ibm_rl_{algorithm}",
            n_evaluations=result.get("n_evaluations"),
            best_discovery_score=result.get("best_discovery_score"),
            best_interaction_energy_kj_mol=result.get("best_interaction_energy_kj_mol"),
            n_high_performers_found=result.get("n_high_performers_found"),
            n_unique_psmiles_evaluated=result.get("n_unique_psmiles_evaluated"),
            wall_time_s=round(wall_time, 1),
            algorithm=algorithm,
            n_timesteps_trained=n_timesteps if mode in ("train", "train_and_test") else None,
            avg_episode_reward=result.get("avg_episode_reward"),
            avg_targets_per_episode=result.get("avg_targets_per_episode"),
            avg_steps_to_first_target=result.get("avg_steps_to_first_target"),
            avg_episode_length=result.get("avg_episode_length"),
            seed_psmiles=seed_psmiles,
            n_proposals=n_proposals,
            target_energy_kj=target_energy_kj,
            notes=notes_val,
        )
        append_comparison_tsv(comparison_tsv, row)
        logger.info("Comparison row appended to %s", comparison_tsv)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "IBM Logical-Agent RL benchmark adapted for biologix-ai evaluation.\n"
            "Uses MDSimulator + PropertyExtractor + discovery_score (same as MCP).\n"
            "Optimization loop: DQN / PPO with logical action-aware features (IBM)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode",
        choices=["train", "test", "train_and_test"],
        default="train_and_test",
        help="Run mode (default: train_and_test).",
    )
    p.add_argument(
        "--algorithm",
        choices=["dqn", "ppo"],
        default="dqn",
        help="RL algorithm (default: dqn).",
    )
    p.add_argument(
        "--seed",
        type=str,
        default="[*]OCC[*]",
        dest="seed_psmiles",
        help="Seed PSMILES (default: PEG [*]OCC[*]).",
    )
    ag = p.add_argument_group(
        "Agentic parity",
        "Size of the RL run vs autonomous discovery: iterations × evals/iteration. "
        "Omit --n-timesteps / --n-episodes / --n-proposals / --max-steps to derive all from the two flags below.",
    )
    ag.add_argument(
        "--agentic-iterations",
        type=int,
        default=_DEFAULT_AGENTIC_ITERATIONS,
        metavar="N",
        help=(
            "Number of discovery-style iterations to mirror: n_episodes=N in test, "
            "and (with --evals-per-iteration) n_timesteps=N×K unless --n-timesteps is set (default: %d)."
            % _DEFAULT_AGENTIC_ITERATIONS
        ),
    )
    ag.add_argument(
        "--evals-per-iteration",
        type=int,
        default=_DEFAULT_EVALS_PER_AGENTIC_ITERATION,
        metavar="K",
        help=(
            "Max evaluations per iteration, like autonomous_discovery --max-eval "
            "(default: %d; use 8 to match autonomous_discovery.py default)."
            % _DEFAULT_EVALS_PER_AGENTIC_ITERATION
        ),
    )
    p.add_argument(
        "--n-proposals",
        type=int,
        default=None,
        help=(
            "Candidate pool size per step (default: --evals-per-iteration, "
            "currently %d if parity defaults apply)." % _DEFAULT_EVALS_PER_AGENTIC_ITERATION
        ),
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help=(
            "Maximum steps per episode (default: --evals-per-iteration, "
            "currently %d if parity defaults apply)." % _DEFAULT_EVALS_PER_AGENTIC_ITERATION
        ),
    )
    p.add_argument(
        "--n-targets",
        type=int,
        default=5,
        help="Targets per episode before early stop (default: 5).",
    )
    p.add_argument(
        "--target-energy",
        type=float,
        default=-5.0,
        dest="target_energy_kj",
        help="Interaction energy threshold for 'target' reward kJ/mol (default: -5.0).",
    )
    p.add_argument(
        "--md-steps",
        type=int,
        default=5000,
        help="OpenMM minimisation steps per candidate (default: 5000).",
    )
    p.add_argument(
        "--n-timesteps",
        type=int,
        default=None,
        help=(
            "RL training timesteps (default: --agentic-iterations × --evals-per-iteration "
            "= %d with current defaults)."
            % DEFAULT_N_TIMESTEPS
        ),
    )
    p.add_argument(
        "--n-episodes",
        type=int,
        default=None,
        help=(
            "Test episodes (default: --agentic-iterations = %d with current defaults)."
            % DEFAULT_N_EPISODES
        ),
    )
    p.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Global RNG seed (default: 42).",
    )
    p.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Path to save (train) or load (test) the model.",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="JSON output path for full results.",
    )
    p.add_argument(
        "--comparison-tsv",
        type=str,
        default=None,
        help="Append a comparison row to this TSV (shared with optuna / agentic benchmarks).",
    )
    p.add_argument(
        "--comparison-notes",
        type=str,
        default="",
        help="Extra text appended to TSV notes (e.g. ablation label).",
    )
    p.add_argument(
        "--verbose",
        type=int,
        default=0,
        help="SB3 verbosity (0=silent, 1=info). Default: 0.",
    )
    args = p.parse_args()

    parity = resolve_agentic_parity_settings(
        args.agentic_iterations,
        args.evals_per_iteration,
        n_timesteps=args.n_timesteps,
        n_episodes=args.n_episodes,
        n_proposals=args.n_proposals,
        max_steps=args.max_steps,
    )
    logger.info(
        "Agentic parity: %d iterations × %d evals/iter → "
        "n_timesteps=%d n_episodes=%d n_proposals=%d max_steps=%d",
        args.agentic_iterations,
        args.evals_per_iteration,
        parity["n_timesteps"],
        parity["n_episodes"],
        parity["n_proposals"],
        parity["max_steps"],
    )

    out = run_ibm_insulin_benchmark(
        mode=args.mode,
        algorithm=args.algorithm,
        seed_psmiles=args.seed_psmiles,
        n_proposals=parity["n_proposals"],
        max_steps=parity["max_steps"],
        n_targets=args.n_targets,
        target_energy_kj=args.target_energy_kj,
        md_steps=args.md_steps,
        n_timesteps=parity["n_timesteps"],
        n_episodes=parity["n_episodes"],
        random_seed=args.random_seed,
        model_path=args.model_path,
        comparison_tsv=args.comparison_tsv,
        comparison_notes=args.comparison_notes,
        verbose=args.verbose,
    )

    output_json = json.dumps(out, indent=2, default=str)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output_json)
        logger.info("Results written to %s", args.output)

    print(output_json)


if __name__ == "__main__":
    main()
