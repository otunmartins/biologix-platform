"""Tests for interaction-energy series helpers (IBM vs agentic plotting)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_sp = _ROOT / "src" / "python"
if str(_sp) not in sys.path:
    sys.path.insert(0, str(_sp))

from benchmarks.plot_ibm_vs_agentic_interaction_energy import (  # noqa: E402
    ibm_iteration_scaled,
    ibm_running_best_binned_by_rl_steps,
    load_agentic_aligned,
    load_agentic_campaign_tsv,
    load_agentic_from_agent_iteration_jsons,
    merge_agentic_sessions_with_campaign_tsv,
    min_interaction_energy_from_agent_iteration,
    load_agentic_from_iteration_jsons,
    load_agentic_series,
    load_ibm_series,
    merge_agentic_sessions,
    parse_agentic_subprocess_log,
    running_best_from_optional_values,
    running_best_from_trace,
)


def test_running_best_from_trace():
    trace = [
        {"interaction_energy_kj_mol": -10.0},
        {"interaction_energy_kj_mol": -5.0},
        {"interaction_energy_kj_mol": -20.0},
    ]
    rb = running_best_from_trace(trace)
    assert rb == [-10.0, -10.0, -20.0]


def test_running_best_from_optional_values_skips_none():
    assert running_best_from_optional_values([None, -3.0, None, -5.0]) == [
        None,
        -3.0,
        -3.0,
        -5.0,
    ]


def test_load_ibm_series_recomputes_running_if_missing(tmp_path: Path):
    p = tmp_path / "ibm.json"
    trace = [
        {"phase": "train", "interaction_energy_kj_mol": 1.0},
        {"phase": "train", "interaction_energy_kj_mol": -2.0},
    ]
    p.write_text(json.dumps({"evaluation_trace": trace}), encoding="utf-8")
    xs, ys, run, phases = load_ibm_series(p)
    assert xs == [1, 2]
    assert ys == [1.0, -2.0]
    assert run == [1.0, -2.0]
    assert phases == ["train", "train"]


def test_load_agentic_aligned_zips_log_blocks_to_json_count(tmp_path: Path):
    d = tmp_path / "sess"
    d.mkdir()
    for i in (1, 2):
        (d / f"autoresearch_iteration_{i}.json").write_text(
            json.dumps({"iteration": i, "score": 1.0}),
            encoding="utf-8",
        )
    (d / "autoresearch_subprocess.log").write_text(
        "  Evaluating 8 via OpenMM Packmol matrix...\nE_int=-1.0 kJ/mol\n"
        "  Evaluating 8 via OpenMM Packmol matrix...\nE_int=-2.0 kJ/mol\n"
        "  Evaluating 8 via OpenMM Packmol matrix...\nE_int=-99.0 kJ/mol\n",
        encoding="utf-8",
    )
    it, mins, src = load_agentic_aligned(d)
    assert src == "subprocess_log"
    assert it == [1, 2]
    assert mins[0] == pytest.approx(-1.0)
    assert mins[1] == pytest.approx(-2.0)


def test_load_agentic_campaign_tsv_five_and_six_column_rows(tmp_path: Path):
    p = tmp_path / "ALL_ITERATIONS_BEST_CANDIDATES.tsv"
    p.write_text(
        "iteration\tpsmiles\tmaterial_name\tinteraction_energy_kj_mol\tfg\tsrc\n"
        "1\tchitosan\t-10.0\tamine, hydroxyl\tmanual\n"
        "2\t[*]C\tmat\t-20.0\tamide\tsrc\n"
        "3\t[*]C\tmat\t-5.0\tamide\ta\n"
        "3\t[*]D\tmat2\t-30.0\tamide\tb\n",
        encoding="utf-8",
    )
    xs, ys, src = load_agentic_campaign_tsv(p)
    assert src == "campaign_tsv"
    assert xs == [1, 2, 3]
    assert ys[0] == pytest.approx(-10.0)
    assert ys[1] == pytest.approx(-20.0)
    assert ys[2] == pytest.approx(-30.0)


def test_merge_agentic_sessions_with_campaign_tsv_json_wins(tmp_path: Path):
    sess = tmp_path / "s"
    sess.mkdir()
    (sess / "agent_iteration_1.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "feedback": {
                    "high_performers": [{"interaction_energy_kj_mol": -99.0}]
                },
            }
        ),
        encoding="utf-8",
    )
    tsv = tmp_path / "c.tsv"
    tsv.write_text(
        "iteration\tpsmiles\tmaterial_name\tinteraction_energy_kj_mol\tfg\tsrc\n"
        "1\t[*]X\ta\t-1.0\tx\ty\n"
        "4\t[*]Y\tb\t-40.0\tx\ty\n",
        encoding="utf-8",
    )
    xs, ys, _src = merge_agentic_sessions_with_campaign_tsv([sess], tsv)
    assert xs == [1, 4]
    assert ys[0] == pytest.approx(-99.0)
    assert ys[1] == pytest.approx(-40.0)


def test_min_interaction_energy_from_agent_iteration():
    assert (
        min_interaction_energy_from_agent_iteration(
            {"min_interaction_energy_kj_mol": -9.0}
        )
        == pytest.approx(-9.0)
    )
    d = {
        "feedback": {
            "high_performers": [
                {"interaction_energy_kj_mol": -1.0},
                {"interaction_energy_kj_mol": -5.0},
            ]
        }
    }
    assert min_interaction_energy_from_agent_iteration(d) == pytest.approx(-5.0)


def test_load_agentic_from_agent_iteration_jsons(tmp_path: Path):
    d = tmp_path / "sess"
    d.mkdir()
    (d / "agent_iteration_2.json").write_text(
        json.dumps(
            {
                "iteration": 2,
                "feedback": {
                    "high_performers": [{"interaction_energy_kj_mol": -3.0}]
                },
            }
        ),
        encoding="utf-8",
    )
    (d / "agent_iteration_1.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "feedback": {
                    "high_performers": [{"interaction_energy_kj_mol": -1.0}]
                },
            }
        ),
        encoding="utf-8",
    )
    it, mins, src = load_agentic_from_agent_iteration_jsons(d)
    assert src == "agent_iteration_json"
    assert it == [1, 2]
    assert mins[0] == pytest.approx(-1.0)
    assert mins[1] == pytest.approx(-3.0)


def test_merge_agentic_sessions_concatenates(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "autoresearch_iteration_1.json").write_text(
        json.dumps({"iteration": 1, "min_interaction_energy_kj_mol": -1.0}),
        encoding="utf-8",
    )
    (b / "autoresearch_iteration_1.json").write_text(
        json.dumps({"iteration": 1, "min_interaction_energy_kj_mol": -3.0}),
        encoding="utf-8",
    )
    it, m, _src = merge_agentic_sessions([a, b])
    assert it == [1, 2]
    assert m == [-1.0, -3.0]


def test_ibm_iteration_scaled():
    trace = [
        {"interaction_energy_kj_mol": 0.0},
        {"interaction_energy_kj_mol": -5.0},
        {"interaction_energy_kj_mol": 10.0},
        {"interaction_energy_kj_mol": -20.0},
    ]
    x, win_min, run = ibm_iteration_scaled(trace, window=2)
    assert x == [1, 2]
    assert win_min[0] == pytest.approx(-5.0)
    assert win_min[1] == pytest.approx(-20.0)
    assert run[0] == pytest.approx(-5.0)
    assert run[1] == pytest.approx(-20.0)


def test_ibm_running_best_binned_by_rl_steps_last_step_in_bin():
    rl = [
        {"global_step": 1, "running_best_interaction_energy_kj_mol": None},
        {"global_step": 7, "running_best_interaction_energy_kj_mol": None},
        {"global_step": 8, "running_best_interaction_energy_kj_mol": -5.0},
        {"global_step": 9, "running_best_interaction_energy_kj_mol": -5.0},
        {"global_step": 16, "running_best_interaction_energy_kj_mol": -10.0},
    ]
    x, y = ibm_running_best_binned_by_rl_steps(rl, window=8)
    assert x == [1, 2]
    assert y[0] == pytest.approx(-5.0)
    assert y[1] == pytest.approx(-10.0)


def test_ibm_running_best_binned_forward_fill_and_empty():
    assert ibm_running_best_binned_by_rl_steps([], window=4) == ([], [])
    rl = [
        {"global_step": 4, "running_best_interaction_energy_kj_mol": None},
        {"global_step": 8, "running_best_interaction_energy_kj_mol": -1.0},
    ]
    x, y = ibm_running_best_binned_by_rl_steps(rl, window=8)
    assert x == [1]
    assert y[0] == pytest.approx(-1.0)

    with pytest.raises(ValueError, match="window"):
        ibm_running_best_binned_by_rl_steps(rl, window=0)


def test_load_agentic_series_falls_back_to_log_when_json_has_no_energies(
    tmp_path: Path,
):
    """Legacy iteration JSONs exist but omit min_interaction_energy_kj_mol."""
    d = tmp_path / "sess"
    d.mkdir()
    (d / "autoresearch_iteration_1.json").write_text(
        json.dumps({"iteration": 1, "score": 1.0}),
        encoding="utf-8",
    )
    (d / "autoresearch_subprocess.log").write_text(
        "  Evaluating 8 via OpenMM Packmol matrix...\nE_int=-99.0 kJ/mol\n",
        encoding="utf-8",
    )
    it, mins, src = load_agentic_series(d)
    assert src == "subprocess_log"
    assert it == [1]
    assert mins[0] == pytest.approx(-99.0)


def test_load_agentic_from_iteration_jsons(tmp_path: Path):
    d = tmp_path / "sess"
    d.mkdir()
    (d / "autoresearch_iteration_2.json").write_text(
        json.dumps({"iteration": 2, "min_interaction_energy_kj_mol": -1.5}),
        encoding="utf-8",
    )
    (d / "autoresearch_iteration_1.json").write_text(
        json.dumps({"iteration": 1, "min_interaction_energy_kj_mol": 5.0}),
        encoding="utf-8",
    )
    it, mins, src = load_agentic_from_iteration_jsons(d)
    assert src == "iteration_json"
    assert it == [1, 2]
    assert mins == [5.0, -1.5]


def test_parse_agentic_subprocess_log(tmp_path: Path):
    log = """
 preamble
  Evaluating 8 via OpenMM Packmol matrix...
 foo E_int=-10.5 kJ/mol bar E_int=-3.0 kJ/mol
  Evaluating 8 via OpenMM Packmol matrix...
 no energies here
  Evaluating 8 via OpenMM Packmol matrix...
 E_int=1.0e1 kJ/mol
"""
    p = tmp_path / "autoresearch_subprocess.log"
    p.write_text(log, encoding="utf-8")
    it, mins, src = parse_agentic_subprocess_log(p)
    assert src == "subprocess_log"
    assert it == [1, 2, 3]
    assert mins[0] == pytest.approx(-10.5)
    assert mins[1] is None
    assert mins[2] == pytest.approx(10.0)


def test_interaction_energy_stats_autonomous_discovery():
    from insulin_ai.autonomous_discovery import _interaction_energy_stats

    z = _interaction_energy_stats(None)
    assert z["n_md_evaluations"] == 0
    assert z["min_interaction_energy_kj_mol"] is None

    r = _interaction_energy_stats(
        {
            "property_analysis": {
                "A": {"interaction_energy_kj_mol": -1.0},
                "B": {"interaction_energy_kj_mol": 3.0},
            }
        }
    )
    assert r["min_interaction_energy_kj_mol"] == pytest.approx(-1.0)
    assert r["mean_interaction_energy_kj_mol"] == pytest.approx(1.0)
    assert r["n_md_evaluations"] == 2
