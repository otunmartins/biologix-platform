#!/usr/bin/env python3
"""
Autoresearch-style autonomous materials discovery loop.

All outputs go under a single session directory (runs/<id>/ or INSULIN_AI_SESSION_DIR).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from insulin_ai.run_paths import ENV_SESSION, new_session_dir


def _ensure_paths(root: Optional[str] = None) -> str:
    r = root or os.environ.get("INSULIN_AI_ROOT", "")
    if not r:
        here = os.path.abspath(__file__)
        r = os.path.dirname(here)
        for _ in range(3):
            r = os.path.dirname(r)
        if not os.path.isfile(os.path.join(r, "insulin_ai_mcp_server.py")):
            r = os.path.dirname(r)
    if r not in sys.path:
        sys.path.insert(0, r)
    sp = os.path.join(r, "src", "python")
    if sp not in sys.path:
        sys.path.insert(0, sp)
    return r


def _append_tsv(
    path: Path,
    run_id: str,
    score: float,
    memory_gb: float,
    status: str,
    description: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        if write_header:
            w.writerow(["run_id", "score", "memory_gb", "status", "description"])
        w.writerow([run_id, f"{score:.4f}", f"{memory_gb:.1f}", status, description[:500]])


def _memory_gb() -> float:
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return round(ru.ru_maxrss / (1024 * 1024 * 1024), 2)
        return round(ru.ru_maxrss / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _interaction_energy_stats(md_results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Min/mean interaction energy (kJ/mol) from one ``evaluate_candidates`` result."""
    if not md_results:
        return {
            "min_interaction_energy_kj_mol": None,
            "mean_interaction_energy_kj_mol": None,
            "n_md_evaluations": 0,
        }
    pa = md_results.get("property_analysis") or {}
    vals: List[float] = []
    for row in pa.values():
        if isinstance(row, dict):
            e = row.get("interaction_energy_kj_mol")
            if e is not None:
                vals.append(float(e))
    if not vals:
        return {
            "min_interaction_energy_kj_mol": None,
            "mean_interaction_energy_kj_mol": None,
            "n_md_evaluations": 0,
        }
    return {
        "min_interaction_energy_kj_mol": min(vals),
        "mean_interaction_energy_kj_mol": sum(vals) / len(vals),
        "n_md_evaluations": len(vals),
    }


def run_autonomous_discovery_loop(
    budget_minutes: float,
    session_dir: Path,
    root: Optional[str] = None,
    md_steps: int = 5000,
    max_eval_per_iteration: int = 8,
    mutation_size: int = 10,
    log_json_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run discovery iterations until wall-clock budget is exhausted.
    All artifacts written under session_dir/.
    """
    root = _ensure_paths(root)
    os.chdir(root)
    os.environ[ENV_SESSION] = str(session_dir.resolve())

    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.scoring import discovery_score
    from insulin_ai.literature.iterative_mining import IterativeLiteratureMiner

    session_dir = Path(session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = session_dir / "autoresearch_results.tsv"
    if log_json_path:
        log_json = Path(log_json_path)
    else:
        log_json = session_dir / "autoresearch_summary.json"

    deadline = time.monotonic() + budget_minutes * 60.0
    miner = IterativeLiteratureMiner(run_dir=session_dir)
    md_sim = MDSimulator(n_steps=md_steps)
    try:
        from insulin_ai.mutation import MaterialMutator
    except ImportError as e:
        raise RuntimeError(
            "Mutation required for autonomous discovery (psmiles): install mutation deps."
        ) from e
    mutator = MaterialMutator(random_seed=42)

    feedback_state: Dict[str, Any] = {
        "top_candidates": [],
        "stability_mechanisms": [],
        "target_properties": {},
        "limitations": [],
        "high_performer_psmiles": [],
        "problematic_psmiles": [],
    }

    iterations_run = 0
    last_score = 0.0
    errors: List[str] = []
    log_lines: List[str] = []

    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        run_id = f"{datetime.now().strftime('%Y%m%d')}_{iteration:04d}"
        desc_parts: List[str] = [f"iter{iteration}"]
        status = "keep"
        score = 0.0

        try:
            mining_results = miner.mine_with_feedback(
                iteration=iteration,
                top_candidates=feedback_state["top_candidates"],
                stability_mechanisms=feedback_state["stability_mechanisms"],
                target_properties=feedback_state["target_properties"],
                limitations=feedback_state["limitations"],
                num_candidates=12,
            )
            candidates = list(mining_results.get("material_candidates", []))

            from insulin_ai.mutation import feedback_guided_mutation

            if iteration > 1 and feedback_state.get("high_performer_psmiles"):
                mutated = feedback_guided_mutation(
                    feedback_state,
                    library_size=mutation_size,
                    feedback_fraction=0.7,
                    random_seed=42 + iteration,
                )
            else:
                mutated = mutator.generate_library(library_size=mutation_size)
            candidates.extend(mutated)
            desc_parts.append(f"+{len(mutated)}mut")

            with_psmiles = [
                c
                for c in candidates
                if "[*]" in str(c.get("chemical_structure") or c.get("psmiles") or "")
            ]
            if not with_psmiles and candidates:
                with_psmiles = [
                    c
                    for c in candidates
                    if "[*]" in str(c.get("chemical_structure") or "")
                ]
            to_eval = with_psmiles[:max_eval_per_iteration] if with_psmiles else candidates[:max_eval_per_iteration]

            md_results: Optional[Dict[str, Any]] = None
            if not to_eval:
                status = "discard"
                desc_parts.append("no_psmiles_candidates")
                score = -1.0
            else:
                md_results = md_sim.evaluate_candidates(to_eval, max_candidates=len(to_eval))
                score = discovery_score(md_results)
                desc_parts.append(f"score={score:.2f}")
                desc_parts.append(f"hp={len(md_results.get('high_performers', []))}")
                feedback_state = miner._update_feedback_state(
                    md_results, feedback_state, to_eval
                )  # type: ignore[attr-defined]

            iterations_run += 1
            last_score = score

            energy_stats = _interaction_energy_stats(md_results)
            with open(session_dir / f"autoresearch_iteration_{iteration}.json", "w", encoding="utf-8") as sf:
                json.dump(
                    {
                        "iteration": iteration,
                        "timestamp": datetime.now().isoformat(),
                        "score": score,
                        **energy_stats,
                        "feedback_state": {k: v for k, v in feedback_state.items() if k != "target_properties"},
                        "run_id": run_id,
                    },
                    sf,
                    indent=2,
                    default=str,
                )

        except Exception as e:
            status = "crash"
            score = 0.0
            errors.append(f"{run_id}: {e}\n{traceback.format_exc()}")
            desc_parts.append(str(e)[:200])

        mem = _memory_gb()
        _append_tsv(tsv_path, run_id, score, mem, status, " ".join(desc_parts))
        log_lines.append(f"{run_id}\t{score}\t{status}\t{' '.join(desc_parts)}")

        if time.monotonic() >= deadline:
            break

    summary = {
        "session_dir": str(session_dir),
        "iterations_run": iterations_run,
        "tsv_path": str(tsv_path),
        "last_score": last_score,
        "errors": errors,
        "log": log_lines,
    }
    log_json.parent.mkdir(parents=True, exist_ok=True)
    with open(log_json, "w", encoding="utf-8") as jf:
        json.dump(summary, jf, indent=2)
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Autoresearch-style autonomous materials discovery")
    p.add_argument("--budget-minutes", type=float, default=60.0)
    p.add_argument("--run-name", type=str, default=None, help="Session name under runs/")
    p.add_argument("--session-dir", type=str, default=None, help="Absolute session dir (default: new runs/<id>)")
    p.add_argument("--root", default="", help="Repo root")
    p.add_argument("--md-steps", type=int, default=5000)
    p.add_argument("--max-eval", type=int, default=8)
    args = p.parse_args()
    root = _ensure_paths(args.root or None)
    repo = Path(root)
    if args.session_dir:
        session_dir = Path(args.session_dir).resolve()
    else:
        session_dir = new_session_dir(repo, name=args.run_name or f"autonomous_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    summary = run_autonomous_discovery_loop(
        budget_minutes=args.budget_minutes,
        session_dir=session_dir,
        root=root,
        md_steps=args.md_steps,
        max_eval_per_iteration=args.max_eval,
    )
    print(json.dumps(summary, indent=2))
    sys.exit(0 if not summary.get("errors") else 1)


if __name__ == "__main__":
    main()
