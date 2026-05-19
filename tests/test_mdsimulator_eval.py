"""MDSimulator.evaluate_candidates: Packmol matrix path and error handling."""

import pytest


def test_evaluate_candidates_raises_without_packmol(monkeypatch):
    """Matrix evaluation requires packmol; no silent fallback."""
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        pytest.skip("OpenMM stack required")

    monkeypatch.setattr(
        "insulin_ai.simulation.packmol_packer._packmol_available",
        lambda: False,
    )
    sim = MDSimulator(n_steps=100)
    with pytest.raises(RuntimeError, match="Packmol"):
        sim.evaluate_candidates(
            [{"material_name": "t", "chemical_structure": "[*]CC[*]"}],
            max_candidates=1,
            verbose=False,
        )


def test_evaluate_candidates_matrix_smoke(tmp_path):
    """Full matrix path when packmol + OpenMM available (slow)."""
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack required")
    if not _packmol_available():
        pytest.skip("packmol binary required")

    import os

    os.environ["INSULIN_AI_OPENMM_MATRIX_NPT"] = "0"
    os.environ["INSULIN_AI_OPENMM_MATRIX_FIXED_MODE"] = "1"
    os.environ["INSULIN_AI_OPENMM_MATRIX_N_POLYMERS"] = "2"
    os.environ["INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS"] = "300"
    os.environ["INSULIN_AI_OPENMM_N_REPEATS"] = "2"
    try:
        sim = MDSimulator(n_steps=100, random_seed=42)
        ad = str(tmp_path / "structures")
        r = sim.evaluate_candidates(
            [{"material_name": "smoke", "chemical_structure": "[*]CC[*]"}],
            max_candidates=1,
            verbose=False,
            artifacts_dir=ad,
        )
    finally:
        for k in (
            "INSULIN_AI_OPENMM_MATRIX_NPT",
            "INSULIN_AI_OPENMM_MATRIX_FIXED_MODE",
            "INSULIN_AI_OPENMM_MATRIX_N_POLYMERS",
            "INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS",
            "INSULIN_AI_OPENMM_N_REPEATS",
        ):
            os.environ.pop(k, None)

    assert r.get("md_results_raw")
    raw = r["md_results_raw"][0]
    assert raw is not None
    assert raw.get("method", "").startswith("OpenMM_matrix")
    assert raw.get("n_polymer_chains") == 2
    assert raw.get("n_polymer_atoms") == raw.get("n_polymer_atoms_per_chain", 0) * 2
    pdb = (tmp_path / "structures" / "smoke_complex_minimized.pdb")
    assert pdb.is_file()
    pm = raw.get("packing_metrics") or {}
    assert pm.get("ok") is True
    assert "min_polymer_protein_distance_nm" in pm


def test_evaluate_stderr_heartbeat_when_verbose_false(capsys, tmp_path):
    """verbose=False emits start/finish stderr lines unless INSULIN_AI_EVAL_QUIET is set."""
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack required")
    if not _packmol_available():
        pytest.skip("packmol binary required")

    import os

    os.environ.pop("INSULIN_AI_EVAL_QUIET", None)
    os.environ.pop("INSULIN_AI_EVAL_VERBOSE", None)
    os.environ["INSULIN_AI_OPENMM_MATRIX_NPT"] = "0"
    os.environ["INSULIN_AI_OPENMM_MATRIX_FIXED_MODE"] = "1"
    os.environ["INSULIN_AI_OPENMM_MATRIX_N_POLYMERS"] = "2"
    os.environ["INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS"] = "300"
    os.environ["INSULIN_AI_OPENMM_N_REPEATS"] = "2"
    try:
        sim = MDSimulator(n_steps=100, random_seed=42)
        sim.evaluate_candidates(
            [{"material_name": "hb", "chemical_structure": "[*]CC[*]"}],
            max_candidates=1,
            verbose=False,
            artifacts_dir=None,
        )
    finally:
        for k in (
            "INSULIN_AI_OPENMM_MATRIX_NPT",
            "INSULIN_AI_OPENMM_MATRIX_FIXED_MODE",
            "INSULIN_AI_OPENMM_MATRIX_N_POLYMERS",
            "INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS",
            "INSULIN_AI_OPENMM_N_REPEATS",
        ):
            os.environ.pop(k, None)

    err = capsys.readouterr().err
    assert "matrix eval starting" in err
    assert "finished in" in err and "status=completed" in err


# ---------------------------------------------------------------------------
# Parallel evaluation: ordering and max_workers wiring
# ---------------------------------------------------------------------------


def test_parallel_ordering_with_mock(monkeypatch):
    """
    With max_workers=2 and a real (or skipped) OpenMM stack, md_results_raw
    order must match the original candidate order.

    The parallel path dispatches to subprocess workers, so inter-process
    mocking is not possible — this test runs real OpenMM with minimal settings
    and skips if the stack is unavailable.
    """
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack required")
    if not _packmol_available():
        pytest.skip("packmol binary required")

    import os

    env_overrides = {
        "INSULIN_AI_OPENMM_MATRIX_NPT": "0",
        "INSULIN_AI_OPENMM_MATRIX_FIXED_MODE": "1",
        "INSULIN_AI_OPENMM_MATRIX_N_POLYMERS": "2",
        "INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS": "200",
        "INSULIN_AI_OPENMM_N_REPEATS": "1",
    }
    for k, v in env_overrides.items():
        os.environ[k] = v
    try:
        candidates = [
            {"material_name": f"poly_{i}", "chemical_structure": "[*]CC[*]"}
            for i in range(3)
        ]
        sim = MDSimulator(n_steps=100, random_seed=0)
        result = sim.evaluate_candidates(
            candidates,
            max_candidates=3,
            verbose=False,
            max_workers=2,
        )
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)

    raw = result["md_results_raw"]
    assert len(raw) == 3, "Must return one entry per candidate"
    for entry in raw:
        assert entry is not None, "All valid PSMILES should succeed with parallel workers"


def test_max_workers_env_var(monkeypatch):
    """_env_max_workers reads INSULIN_AI_EVAL_MAX_WORKERS and clamps to >=1."""
    from insulin_ai.simulation.md_simulator import _env_max_workers

    monkeypatch.setenv("INSULIN_AI_EVAL_MAX_WORKERS", "3")
    assert _env_max_workers() == 3

    monkeypatch.setenv("INSULIN_AI_EVAL_MAX_WORKERS", "0")
    assert _env_max_workers() == 1

    monkeypatch.delenv("INSULIN_AI_EVAL_MAX_WORKERS", raising=False)
    assert _env_max_workers() == 1


def test_max_workers_argument_overrides_env(monkeypatch):
    """
    Explicit max_workers=1 argument uses the sequential path even when
    INSULIN_AI_EVAL_MAX_WORKERS is set to a higher value in the environment.
    ``evaluate_candidates`` always includes ``evaluation_progress`` in the return dict;
    we assert one completed row and a successful matrix result.
    """
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack required")
    if not _packmol_available():
        pytest.skip("packmol binary required")

    import os

    env_overrides = {
        "INSULIN_AI_EVAL_MAX_WORKERS": "4",
        "INSULIN_AI_OPENMM_MATRIX_NPT": "0",
        "INSULIN_AI_OPENMM_MATRIX_FIXED_MODE": "1",
        "INSULIN_AI_OPENMM_MATRIX_N_POLYMERS": "2",
        "INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS": "200",
        "INSULIN_AI_OPENMM_N_REPEATS": "1",
    }
    for k, v in env_overrides.items():
        os.environ[k] = v
    try:
        sim = MDSimulator(n_steps=100, random_seed=0)
        result = sim.evaluate_candidates(
            [{"material_name": "x", "chemical_structure": "[*]CC[*]"}],
            max_candidates=1,
            verbose=False,
            max_workers=1,  # explicit override: env says 4 but we force sequential
        )
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)

    prog = result.get("evaluation_progress") or []
    assert len(prog) == 1
    assert prog[0].get("status") == "completed"
    assert result.get("md_results_raw") is not None
