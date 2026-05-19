#!/usr/bin/env python3
"""
Build a Markdown **Table 2**-style summary from ``comparison_results_study.tsv``.

Groups rows by ``method`` and reports mean ± population std for numeric columns
used in the paper (best interaction energy, n_evaluations, wall time, unique PSMILES).

Example::

    python benchmarks/generate_paper_comparison_table.py \\
        --tsv benchmarks/comparison_results_study.tsv \\
        --output docs/PAPER_TABLE2.md
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List


def _f(x: Any) -> float:
    if x is None or str(x).strip() == "":
        return float("nan")
    return float(x)


def _fmt_mean_std(vals: List[float]) -> str:
    finite = [v for v in vals if math.isfinite(v)]
    if not finite:
        return "—"
    m = mean(finite)
    if len(finite) >= 2:
        s = pstdev(finite)
        return f"{m:.2f} ± {s:.2f}"
    return f"{m:.2f}"


def generate_table(tsv_path: Path) -> str:
    rows: List[Dict[str, str]] = []
    with open(tsv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if not row.get("method"):
                continue
            rows.append(row)

    by_method: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)

    lines = [
        "# Paper Table 2 (auto-generated)",
        "",
        f"Source: `{tsv_path.as_posix()}`",
        "",
        "| Method | n_eval (mean ± std) | Best E_int / kJ mol⁻¹ (mean ± std) | Unique PSMILES (mean ± std) | Wall time / s (mean ± std) |",
        "| --- | --- | --- | --- | --- |",
    ]

    order = [
        "random_psmiles",
        "optuna_tpe",
        "ibm_rl_dqn",
        "ibm_rl_ppo",
    ]
    rest = sorted(k for k in by_method if k not in order)
    for key in order + rest:
        if key not in by_method:
            continue
        grp = by_method[key]
        ne = [_f(r.get("n_evaluations")) for r in grp]
        be = [_f(r.get("best_interaction_energy_kj_mol")) for r in grp]
        nu = [_f(r.get("n_unique_psmiles_evaluated")) for r in grp]
        wt = [_f(r.get("wall_time_s")) for r in grp]
        lines.append(
            f"| `{key}` | {_fmt_mean_std(ne)} | {_fmt_mean_std(be)} | {_fmt_mean_std(nu)} | {_fmt_mean_std(wt)} |"
        )

    lines.extend(["", "## Raw rows", ""])
    lines.append("```")
    with open(tsv_path, encoding="utf-8") as f:
        lines.append(f.read().rstrip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tsv",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "comparison_results_study.tsv",
    )
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()
    md = generate_table(args.tsv)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(md)


if __name__ == "__main__":
    main()
