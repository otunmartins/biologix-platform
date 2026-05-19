#!/usr/bin/env python3
"""
Plot interaction energy vs progress for IBM RL benchmark JSON and agentic session(s).

IBM: for a **combined** figure, prefers ``rl_step_progress_trace`` (one row per RL
``step()``): **running-best** interaction energy after each step, binned into windows
of ``--ibm-window`` RL steps to match one agentic iteration. Older JSON without that
field falls back to binning ``evaluation_trace`` by evaluation count.

Agentic: reads ``min_interaction_energy_kj_mol`` from ``autoresearch_iteration_*.json``, or
minima from ``feedback.high_performers`` in ``agent_iteration_*.json`` (OpenCode workflow),
or ``autoresearch_subprocess.log`` block minima. Optional ``ALL_ITERATIONS_BEST_CANDIDATES.tsv``
(``--agentic-campaign-tsv``) fills missing iterations; session JSON wins on duplicates.
Multiple ``--agentic-session`` directories merge in order; autoresearch sessions are renumbered
1…K, while agent-iteration sessions keep their ``iteration`` index on the x-axis.

Example::

    python benchmarks/plot_ibm_vs_agentic_interaction_energy.py \\
        --ibm-json results/ibm_dqn.json \\
        --agentic-session runs/insulin-patch-discovery runs/iterations_3_to_12 \\
        --output runs/iterations_3_to_12/ibm_vs_agentic_interaction_energy.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]

# Agent-written summary of per-iteration best candidates (fills gaps vs sparse JSON).
_CAMPAIGN_TSV_BASENAME = "ALL_ITERATIONS_BEST_CANDIDATES.tsv"
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def running_best_from_trace(trace: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Cumulative minimum interaction energy after each trace entry (same as benchmark)."""
    out: List[Optional[float]] = []
    best: Optional[float] = None
    for entry in trace:
        e = entry.get("interaction_energy_kj_mol")
        if e is not None:
            fe = float(e)
            best = fe if best is None else min(best, fe)
        out.append(best)
    return out


def load_ibm_series(
    ibm_json: Path,
) -> Tuple[
    List[int],
    List[Optional[float]],
    List[Optional[float]],
    List[str],
]:
    """Return (eval_index, per_eval_energy, running_best, phase_labels)."""
    data = json.loads(ibm_json.read_text(encoding="utf-8"))
    trace = data.get("evaluation_trace") or []
    xs: List[int] = []
    ys: List[Optional[float]] = []
    phases: List[str] = []
    for i, entry in enumerate(trace, start=1):
        xs.append(i)
        e = entry.get("interaction_energy_kj_mol")
        ys.append(float(e) if e is not None else None)
        phases.append(str(entry.get("phase") or ""))
    running = data.get("running_best_interaction_energy_kj_mol")
    if running is None or len(running) != len(trace):
        running = running_best_from_trace(trace)
    return xs, ys, running, phases


def ibm_iteration_scaled(
    trace: List[Dict[str, Any]], window: int = 10
) -> Tuple[List[int], List[Optional[float]], List[Optional[float]]]:
    """Bin IBM evals into windows; return (x=1..n_win, window_min, running_best_through_window).

    ``running_best_through_window[j]`` is the best energy over evaluations 1 … j×window
    (clipped to trace length on the last window).
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    energies: List[Optional[float]] = []
    for entry in trace:
        e = entry.get("interaction_energy_kj_mol")
        energies.append(float(e) if e is not None else None)
    n = len(energies)
    if n == 0:
        return [], [], []
    n_win = (n + window - 1) // window
    xs = list(range(1, n_win + 1))
    win_min: List[Optional[float]] = []
    run: List[Optional[float]] = []
    for j in range(1, n_win + 1):
        lo = (j - 1) * window
        hi = min(j * window, n)
        chunk = [e for e in energies[lo:hi] if e is not None]
        win_min.append(min(chunk) if chunk else None)
        prefix = [e for e in energies[:hi] if e is not None]
        run.append(min(prefix) if prefix else None)
    return xs, win_min, run


def ibm_running_best_binned_by_rl_steps(
    rl_trace: List[Dict[str, Any]],
    window: int,
) -> Tuple[List[int], List[Optional[float]]]:
    """Bin RL steps by ``window``; y = running best at the last step in each bin.

    Iteration index ``k`` covers global RL steps ``(k-1)*window + 1 … k*window``.
    Within each bin, the entry with the largest ``global_step`` wins (ties: last in
    trace order). ``None`` running-best values are forward-filled from the last
    non-``None`` bin (early bins before the first MD eval then match the first known best).
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    if not rl_trace:
        return [], []

    bucket_last: Dict[int, Tuple[int, Optional[float]]] = {}
    for entry in rl_trace:
        g = int(entry["global_step"])
        b = (g - 1) // window + 1
        rb_raw = entry.get("running_best_interaction_energy_kj_mol")
        rb = float(rb_raw) if rb_raw is not None else None
        bucket_last[b] = (g, rb)

    xs = sorted(bucket_last.keys())
    ys_raw = [bucket_last[k][1] for k in xs]
    ys: List[Optional[float]] = []
    last: Optional[float] = None
    for y in ys_raw:
        if y is not None:
            last = y
        ys.append(last)
    return xs, ys


def _interaction_energy_from_campaign_tsv_row(parts: List[str]) -> Optional[float]:
    """Parse energy column from a tab row (6-col with PSMILES or 5-col legacy)."""
    if len(parts) >= 6:
        try:
            return float(parts[3])
        except (ValueError, IndexError):
            return None
    if len(parts) == 5:
        try:
            return float(parts[2])
        except (ValueError, IndexError):
            return None
    return None


def load_agentic_campaign_tsv(
    path: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """Load per-iteration minimum interaction energy from campaign TSV.

    Expects a header line starting with ``iteration`` and rows with either
    ``iteration, psmiles, material_name, energy, ...`` (6+ columns) or a 5-column
    legacy row without a separate PSMILES column. Multiple rows per iteration
    yield the minimum energy for that iteration.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], [], "none"
    start = 0
    first_cell = lines[0].split("\t")[0].strip().lower()
    if first_cell == "iteration":
        start = 1

    buckets: Dict[int, List[float]] = defaultdict(list)
    for line in lines[start:]:
        parts = line.split("\t")
        if not parts or not parts[0].strip().isdigit():
            continue
        it = int(parts[0].strip())
        e = _interaction_energy_from_campaign_tsv_row(parts)
        if e is not None:
            buckets[it].append(e)
    if not buckets:
        return [], [], "none"
    xs = sorted(buckets.keys())
    ys: List[Optional[float]] = [min(buckets[k]) for k in xs]
    return xs, ys, "campaign_tsv"


def resolve_campaign_tsv_path(
    session_dirs: Sequence[Path],
    explicit: Optional[Path],
    *,
    auto_pick: bool,
) -> Optional[Path]:
    """Return path to campaign TSV: explicit file, else first match under session dirs."""
    if explicit is not None:
        p = explicit.expanduser().resolve()
        if p.is_file():
            return p
        print(f"Warning: campaign TSV not found: {p}", file=sys.stderr)
        return None
    if not auto_pick:
        return None
    for d in session_dirs:
        cand = (d / _CAMPAIGN_TSV_BASENAME).resolve()
        if cand.is_file():
            return cand
    return None


def merge_agentic_sessions_with_campaign_tsv(
    session_dirs: Sequence[Path],
    campaign_tsv_path: Optional[Path],
) -> Tuple[List[int], List[Optional[float]], str]:
    """Merge session JSON/log agentic series with TSV; session values win on same iteration."""
    it_s, m_s, src = merge_agentic_sessions(session_dirs)
    primary: Dict[int, Optional[float]] = {}
    for i, m in zip(it_s, m_s):
        if m is not None:
            primary[i] = m

    if not campaign_tsv_path:
        if not primary:
            return [], [], "none"
        xs = sorted(primary.keys())
        return xs, [primary[k] for k in xs], src

    it_t, m_t, tsv_tag = load_agentic_campaign_tsv(campaign_tsv_path)
    tsv_d = {i: mm for i, mm in zip(it_t, m_t) if mm is not None}
    if not tsv_d and not primary:
        return [], [], "none"
    all_keys = sorted(set(primary.keys()) | set(tsv_d.keys()))
    out_m: List[Optional[float]] = []
    for k in all_keys:
        v = primary.get(k)
        if v is not None:
            out_m.append(v)
        else:
            out_m.append(tsv_d.get(k))
    if src == "none":
        tag = tsv_tag
    else:
        tag = f"{src}+{tsv_tag}"
    return all_keys, out_m, tag


def min_interaction_energy_from_agent_iteration(
    data: Dict[str, Any],
) -> Optional[float]:
    """Best (minimum) interaction energy from ``agent_iteration_*.json`` body."""
    top = data.get("min_interaction_energy_kj_mol")
    if top is not None:
        return float(top)
    fb = data.get("feedback")
    if not isinstance(fb, dict):
        return None
    hp = fb.get("high_performers")
    if not isinstance(hp, list):
        return None
    vals: List[float] = []
    for item in hp:
        if isinstance(item, dict):
            e = item.get("interaction_energy_kj_mol")
            if e is not None:
                vals.append(float(e))
    return min(vals) if vals else None


def load_agentic_from_agent_iteration_jsons(
    session_dir: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """OpenCode / MCP workflow: ``agent_iteration_<n>.json`` with feedback energies."""
    files = sorted(
        session_dir.glob("agent_iteration_*.json"),
        key=lambda p: int(
            re.search(r"agent_iteration_(\d+)\.json$", p.name).group(1)
        ),
    )
    if not files:
        return [], [], "none"
    iterations: List[int] = []
    mins: List[Optional[float]] = []
    for fp in files:
        m = re.search(r"agent_iteration_(\d+)\.json$", fp.name)
        if not m:
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        it = int(data.get("iteration", m.group(1)))
        iterations.append(it)
        mins.append(min_interaction_energy_from_agent_iteration(data))
    if not iterations:
        return [], [], "none"
    order = sorted(range(len(iterations)), key=lambda j: iterations[j])
    it_sorted = [iterations[j] for j in order]
    mins_sorted = [mins[j] for j in order]
    return it_sorted, mins_sorted, "agent_iteration_json"


def load_agentic_from_iteration_jsons(
    session_dir: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """Iteration index and minimum batch interaction energy (kJ/mol)."""
    files = sorted(session_dir.glob("autoresearch_iteration_*.json"))
    if not files:
        return [], [], "none"
    iterations: List[int] = []
    mins: List[Optional[float]] = []
    for fp in files:
        m = re.search(r"autoresearch_iteration_(\d+)\.json$", fp.name)
        if not m:
            continue
        it = int(m.group(1))
        data = json.loads(fp.read_text(encoding="utf-8"))
        e = data.get("min_interaction_energy_kj_mol")
        iterations.append(it)
        mins.append(float(e) if e is not None else None)
    if not iterations:
        return [], [], "none"
    order = sorted(range(len(iterations)), key=lambda j: iterations[j])
    it_sorted = [iterations[j] for j in order]
    mins_sorted = [mins[j] for j in order]
    return it_sorted, mins_sorted, "iteration_json"


def parse_agentic_subprocess_log(
    log_path: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """Split log on Packmol batch headers; min E_int per block (1-based block index)."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(r"\s+Evaluating \d+ via OpenMM Packmol matrix\.\.\.")
    blocks = pat.split(text)
    e_pat = re.compile(r"E_int=([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*kJ/mol")
    iterations: List[int] = []
    mins: List[Optional[float]] = []
    for i, block in enumerate(blocks[1:], start=1):
        vals = [float(m.group(1)) for m in e_pat.finditer(block)]
        iterations.append(i)
        mins.append(min(vals) if vals else None)
    return iterations, mins, "subprocess_log"


def load_agentic_aligned(
    session_dir: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """Align energies to iteration JSONs, subprocess log, or agent iteration state."""
    files = sorted(
        session_dir.glob("autoresearch_iteration_*.json"),
        key=lambda p: int(
            re.search(r"autoresearch_iteration_(\d+)\.json$", p.name).group(1)
        ),
    )
    if not files:
        ag_it, ag_m, ag_src = load_agentic_from_agent_iteration_jsons(session_dir)
        if ag_it and ag_src != "none":
            return ag_it, ag_m, ag_src
        log_path = session_dir / "autoresearch_subprocess.log"
        if log_path.is_file():
            return parse_agentic_subprocess_log(log_path)
        return [], [], "none"

    it_nums: List[int] = []
    energies: List[Optional[float]] = []
    for fp in files:
        m = re.search(r"autoresearch_iteration_(\d+)\.json$", fp.name)
        assert m is not None
        it_nums.append(int(m.group(1)))
        data = json.loads(fp.read_text(encoding="utf-8"))
        e = data.get("min_interaction_energy_kj_mol")
        energies.append(float(e) if e is not None else None)

    if any(e is not None for e in energies):
        return it_nums, energies, "iteration_json"

    log_path = session_dir / "autoresearch_subprocess.log"
    if not log_path.is_file():
        return it_nums, energies, "iteration_json"

    _lit, log_mins, _ = parse_agentic_subprocess_log(log_path)
    n = min(len(it_nums), len(log_mins))
    return it_nums[:n], log_mins[:n], "subprocess_log"


def load_agentic_series(
    session_dir: Path,
) -> Tuple[List[int], List[Optional[float]], str]:
    """Backward-compatible alias for a single session."""
    return load_agentic_aligned(session_dir)


def merge_agentic_sessions(
    session_dirs: Sequence[Path],
) -> Tuple[List[int], List[Optional[float]], str]:
    """Concatenate sessions in order.

    ``autoresearch`` / subprocess sources are renumbered 1…K in merge order (legacy).
    ``agent_iteration_json`` keeps iteration indices from each JSON (campaign step).
    """
    all_it: List[int] = []
    all_m: List[Optional[float]] = []
    sources: List[str] = []
    g = 1
    for d in session_dirs:
        _it, m, src = load_agentic_aligned(d)
        if not m:
            continue
        if all(x is None for x in m):
            continue
        if src == "agent_iteration_json":
            all_it.extend(_it)
            all_m.extend(m)
        else:
            for val in m:
                all_it.append(g)
                all_m.append(val)
                g += 1
        sources.append(src)
    src_tag = "+".join(sources) if sources else "none"
    return all_it, all_m, src_tag


def running_best_from_optional_values(values: List[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    best: Optional[float] = None
    for v in values:
        if v is not None:
            best = float(v) if best is None else min(best, float(v))
        out.append(best)
    return out


def plot_comparison(
    ibm_json: Path,
    agentic_sessions: Sequence[Path],
    output: Path,
    title: str = "Interaction energy vs progress (lower is better)",
    ibm_window: int = 10,
    layout: str = "combined",
    campaign_tsv: Optional[Path] = None,
    auto_pick_campaign_tsv: bool = True,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for plotting. Install with: "
            "pip install 'insulin-ai[simulation]' or pip install matplotlib"
        ) from e

    data = json.loads(ibm_json.read_text(encoding="utf-8"))
    trace = data.get("evaluation_trace") or []

    tsv_path = resolve_campaign_tsv_path(
        agentic_sessions,
        campaign_tsv,
        auto_pick=auto_pick_campaign_tsv,
    )
    ax_x_ag, ax_y_ag, ag_src = merge_agentic_sessions_with_campaign_tsv(
        agentic_sessions,
        tsv_path,
    )
    ag_run = running_best_from_optional_values(ax_y_ag)

    rl_trace = data.get("rl_step_progress_trace") or []
    if not trace and not rl_trace:
        print(
            "Note: IBM JSON has no 'evaluation_trace' or 'rl_step_progress_trace'. Re-run:\n"
            f"  python benchmarks/ibm_insulin_rl_benchmark.py --output {ibm_json}",
            file=sys.stderr,
        )

    if layout == "combined":
        fig, ax = plt.subplots(figsize=(9, 5))
        fig.suptitle(title)

        if rl_trace:
            x_i, ibm_run = ibm_running_best_binned_by_rl_steps(rl_trace, ibm_window)
            ibm_label = (
                f"IBM RL (running best; {ibm_window} RL steps / iteration)"
            )
        elif trace:
            x_i, _win_min, ibm_run = ibm_iteration_scaled(trace, window=ibm_window)
            ibm_label = (
                f"IBM RL (running best; {ibm_window} evals / iteration, trace-binned)"
            )
        else:
            x_i = []
            ibm_run = []
            ibm_label = ""

        if x_i:
            ax.plot(
                x_i,
                ibm_run,
                "-",
                linewidth=2.2,
                label=ibm_label,
            )

        if ax_x_ag:
            ax.plot(
                ax_x_ag,
                ag_run,
                "-",
                linewidth=2.2,
                label=f"Agentic (running best; {ag_src})",
            )
            ax.plot(
                ax_x_ag,
                ax_y_ag,
                "o",
                alpha=0.4,
                markersize=5,
                label="Agentic (min / iteration)",
            )

        ax.set_xlabel(
            "Iteration index (agentic = discovery iteration; IBM = RL steps ÷ window)"
        )
        ax.set_ylabel(r"Interaction energy (kJ mol$^{-1}$)")
        ax.grid(True, alpha=0.3)
        if x_i or ax_x_ag:
            ax.legend(loc="best", fontsize=9)
        else:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

        fig.tight_layout()
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    # layout == "dual"
    ax_x_ibm, ax_y_ibm, ax_run_ibm, _phases = load_ibm_series(ibm_json)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    fig.suptitle(title)

    ax0 = axes[0]
    if ax_x_ibm:
        ax0.plot(ax_x_ibm, ax_y_ibm, "o", alpha=0.35, markersize=3, label="per evaluation")
        ax0.plot(ax_x_ibm, ax_run_ibm, "-", linewidth=2, label="best so far")
        ax0.legend(loc="best", fontsize=8)
    ax0.set_xlabel("IBM benchmark evaluation index (train then test)")
    ax0.set_ylabel(r"Interaction energy (kJ mol$^{-1}$)")
    ax0.grid(True, alpha=0.3)

    ax1 = axes[1]
    if ax_x_ag:
        ax1.plot(ax_x_ag, ax_y_ag, "o", alpha=0.35, markersize=4, label="min / iteration")
        ax1.plot(ax_x_ag, ag_run, "-", linewidth=2, label="best so far")
        ax1.legend(loc="best", fontsize=8)
    ax1.set_xlabel(f"Agentic iteration (merged; {ag_src})")
    ax1.grid(True, alpha=0.3)

    if not ax_x_ibm and not ax_x_ag:
        fig.text(0.5, 0.5, "No IBM trace and no agentic data", ha="center", va="center")

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("Example::")[0].strip())
    p.add_argument("--ibm-json", type=Path, required=True, help="IBM benchmark JSON output")
    p.add_argument(
        "--agentic-session",
        type=Path,
        nargs="+",
        required=True,
        help="One or more session dirs (merged in order; empty/no-energy dirs skipped)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("ibm_vs_agentic_interaction_energy.png"),
        help="Output image path (default: ./ibm_vs_agentic_interaction_energy.png)",
    )
    p.add_argument("--title", type=str, default=None, help="Figure suptitle")
    p.add_argument(
        "--ibm-window",
        type=int,
        default=10,
        help=(
            "Combined plot: RL steps per IBM x-tick when JSON has rl_step_progress_trace; "
            "else evaluations per tick from evaluation_trace (default: 10)"
        ),
    )
    p.add_argument(
        "--layout",
        choices=("combined", "dual"),
        default="combined",
        help="combined = one axis; dual = separate IBM / agentic panels (default: combined)",
    )
    p.add_argument(
        "--agentic-campaign-tsv",
        type=Path,
        default=None,
        help=(
            f"TSV with per-iteration best energies (e.g. {_CAMPAIGN_TSV_BASENAME}). "
            "If omitted, that filename is auto-loaded from the first --agentic-session dir that contains it."
        ),
    )
    p.add_argument(
        "--no-agentic-campaign-tsv",
        action="store_true",
        help="Disable auto-loading of ALL_ITERATIONS_BEST_CANDIDATES.tsv from session dirs",
    )
    args = p.parse_args()
    plot_comparison(
        args.ibm_json,
        args.agentic_session,
        args.output,
        title=args.title or "Interaction energy vs progress (lower is better)",
        ibm_window=args.ibm_window,
        layout=args.layout,
        campaign_tsv=args.agentic_campaign_tsv,
        auto_pick_campaign_tsv=not args.no_agentic_campaign_tsv,
    )
    print(f"Wrote {args.output.resolve()}")


if __name__ == "__main__":
    main()
