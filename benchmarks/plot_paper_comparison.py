#!/usr/bin/env python3
"""
Paper Figure: running-best interaction energy vs cumulative evaluation index.

Loads benchmark JSON artifacts (IBM RL, Optuna, random baseline) from ``results/``
and optionally one or more agentic session directories. Curves share the same
OpenMM evaluation semantics as :data:`evaluation_trace` in
``ibm_insulin_rl_benchmark.py``.

Example::

    mamba run -n insulin-ai-sim python benchmarks/plot_paper_comparison.py \\
        --results-dir results \\
        --agentic-session runs/autonomous-20iter \\
        --output runs/paper_comparison_running_best.png

Use any session with ``agent_iteration_*.json``; ``runs/autonomous-25iter`` is a
legacy 25-iteration run—new paper sessions should use **20** iterations to match
``run_paper_study.sh`` (20×8 eval budget).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _running_best_from_trace(trace: Sequence[Dict[str, Any]]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    best: Optional[float] = None
    for entry in trace:
        e = entry.get("interaction_energy_kj_mol")
        if e is not None and not (isinstance(e, float) and math.isnan(e)):
            fe = float(e)
            best = fe if best is None else min(best, fe)
        out.append(best)
    return out


def _pad_last(xs: List[Optional[float]], length: int) -> List[float]:
    """Pad with last finite value; missing → nan."""
    if not xs:
        return [float("nan")] * length
    out: List[float] = []
    last: Optional[float] = None
    for v in xs:
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            last = float(v)
        out.append(last if last is not None else float("nan"))
    while len(out) < length:
        out.append(last if last is not None else float("nan"))
    return out[:length]


def load_json_trace(path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    trace = data.get("evaluation_trace") or []
    label = path.stem
    return label, trace


def load_agentic_session(session_dir: Path) -> Tuple[str, List[Dict[str, Any]]]:
    """Build a synthetic evaluation trace from ``agent_iteration_*.json`` order."""
    trace: List[Dict[str, Any]] = []
    pattern = re.compile(r"agent_iteration_(\d+)\.json$")
    paths = sorted(
        session_dir.glob("agent_iteration_*.json"),
        key=lambda p: int(pattern.search(p.name).group(1)) if pattern.search(p.name) else 0,
    )
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        fb = data.get("feedback") or {}
        for c in fb.get("candidates") or []:
            if c.get("status") != "completed":
                continue
            e = c.get("interaction_energy_kj_mol")
            if e is None:
                continue
            trace.append(
                {
                    "interaction_energy_kj_mol": float(e),
                    "psmiles": c.get("psmiles") or "",
                    "phase": "agentic",
                }
            )
    return session_dir.name, trace


def mean_std_band(
    series_list: List[List[Optional[float]]],
    max_len: int,
) -> Tuple[List[int], List[float], List[float], List[float]]:
    """Return x, mean, mean-std, mean+std (population std; nan-aware)."""
    padded = [_pad_last(s, max_len) for s in series_list]
    xs = list(range(1, max_len + 1))
    mus: List[float] = []
    lo: List[float] = []
    hi: List[float] = []
    for i in range(max_len):
        vals = [p[i] for p in padded if i < len(p) and math.isfinite(p[i])]
        if not vals:
            mus.append(float("nan"))
            lo.append(float("nan"))
            hi.append(float("nan"))
            continue
        m = mean(vals)
        mus.append(m)
        if len(vals) >= 2:
            s = pstdev(vals)
            lo.append(m - s)
            hi.append(m + s)
        else:
            lo.append(m)
            hi.append(m)
    return xs, mus, lo, hi


def collect_result_globs(results_dir: Path) -> Dict[str, List[Path]]:
    """Group JSON files by method prefix for multi-seed averaging."""
    groups: Dict[str, List[Path]] = {}
    for pat, key in [
        ("ibm_dqn_seed*.json", "ibm_rl_dqn"),
        ("ibm_ppo_seed*.json", "ibm_rl_ppo"),
        ("optuna_seed*.json", "optuna_tpe"),
        ("random_seed*.json", "random_psmiles"),
    ]:
        files = sorted(results_dir.glob(pat))
        if files:
            groups[key] = files
    return groups


def plot_paper_comparison(
    results_dir: Path,
    agentic_sessions: Sequence[Path],
    output: Path,
    title: str = "Running-best interaction energy (paper study)",
) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.5))
    groups = collect_result_globs(results_dir)
    colors = {
        "ibm_rl_dqn": "C0",
        "ibm_rl_ppo": "C1",
        "optuna_tpe": "C2",
        "random_psmiles": "C4",
        "agentic": "C3",
    }

    if not groups and not agentic_sessions:
        raise SystemExit(
            "No benchmark JSON groups found under --results-dir "
            "(expected ibm_dqn_seed*.json, ibm_ppo_seed*.json, optuna_seed*.json, "
            "random_seed*.json) and no --agentic-session given."
        )

    max_len = 0
    for key, paths in groups.items():
        traces = [load_json_trace(p)[1] for p in paths]
        rb_list = [_running_best_from_trace(t) for t in traces]
        lens = [len(r) for r in rb_list]
        mlen = max(lens) if lens else 0
        max_len = max(max_len, mlen)
        if len(rb_list) == 1:
            xs = list(range(1, len(rb_list[0]) + 1))
            ax.plot(
                xs,
                rb_list[0],
                label=key,
                color=colors.get(key, "C5"),
                linewidth=2,
            )
        else:
            xb, mu, lo, hi = mean_std_band(rb_list, mlen)
            c = colors.get(key, "C5")
            ax.plot(xb, mu, label=f"{key} (mean)", color=c, linewidth=2)
            ax.fill_between(xb, lo, hi, color=c, alpha=0.2)

    if agentic_sessions:
        agent_rb: List[List[Optional[float]]] = []
        for d in agentic_sessions:
            if not d.is_dir():
                continue
            _, tr = load_agentic_session(d)
            agent_rb.append(_running_best_from_trace(tr))
        if agent_rb:
            mlen = max(len(r) for r in agent_rb)
            max_len = max(max_len, mlen)
            if len(agent_rb) == 1:
                xs = list(range(1, len(agent_rb[0]) + 1))
                ax.plot(
                    xs,
                    agent_rb[0],
                    label="agentic",
                    color=colors["agentic"],
                    linewidth=2,
                )
            else:
                xb, mu, lo, hi = mean_std_band(agent_rb, mlen)
                c = colors["agentic"]
                ax.plot(xb, mu, label="agentic (mean)", color=c, linewidth=2)
                ax.fill_between(xb, lo, hi, color=c, alpha=0.2)

    ax.set_xlabel("Cumulative evaluation index")
    ax.set_ylabel(r"Running-best interaction energy (kJ mol$^{-1}$)")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--results-dir",
        type=Path,
        default=_ROOT / "results",
        help="Directory containing ibm_*_seed*.json, optuna_seed*.json, random_seed*.json",
    )
    p.add_argument(
        "--agentic-session",
        type=Path,
        nargs="*",
        default=(),
        help="Session dirs with agent_iteration_*.json (optional, repeatable)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "results" / "paper_comparison_running_best.png",
    )
    p.add_argument("--title", type=str, default="Running-best interaction energy (paper study)")
    args = p.parse_args()
    plot_paper_comparison(
        args.results_dir,
        args.agentic_session,
        args.output,
        title=args.title,
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
