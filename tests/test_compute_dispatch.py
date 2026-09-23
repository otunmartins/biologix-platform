"""Tests for choosing where OpenMM runs and for moving worker artifacts home."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai import compute  # noqa: E402
from biologix_ai.compute.openmm_job import (  # noqa: E402
    OpenMMJobSpec,
    materialize_remote_result,
    target_for_spec,
)


def test_compute_defaults_and_validation(monkeypatch) -> None:
    monkeypatch.delenv(compute.DEFAULT_COMPUTE_ENV, raising=False)
    assert compute.resolve_compute("") == "cpu"
    monkeypatch.setenv(compute.DEFAULT_COMPUTE_ENV, "gpu")
    assert compute.resolve_compute("") == "gpu"
    assert compute.resolve_compute("CPU") == "cpu"
    with pytest.raises(compute.ComputeError):
        compute.resolve_compute("tpu")
    monkeypatch.setenv(compute.BACKEND_ENV, "cloud")
    with pytest.raises(compute.ComputeError):
        compute.backend()


def test_local_gpu_request_selects_cuda_without_fallback(monkeypatch) -> None:
    seen = {}

    def fake_job(spec, **kwargs):
        seen["platform"] = spec.openmm_platform
        return {"ok": True, "candidate_outcomes": []}

    monkeypatch.setenv(compute.BACKEND_ENV, "local")
    monkeypatch.setattr(compute, "run_openmm_job", fake_job)
    out = compute.run_openmm(OpenMMJobSpec(psmiles=["[*]OCC[*]"]), compute="gpu")
    assert seen["platform"] == "CUDA"
    assert out["compute"] == {"target": "gpu", "backend": "local", "worker": "in-process"}


def test_bundled_targets_are_named_not_shipped(tmp_path) -> None:
    bundled = ROOT / "src" / "python" / "biologix_ai" / "simulation" / "data" / "4F1C.pdb"
    assert target_for_spec(str(bundled)) == {"target_package_path": "simulation/data/4F1C.pdb"}
    prepared = tmp_path / "biologic_target.pdb"
    prepared.write_text("ATOM\n")
    assert target_for_spec(str(prepared)) == {"target_pdb_text": "ATOM\n"}


def test_worker_artifacts_land_in_the_session_and_paths_are_rewritten(tmp_path) -> None:
    response = {
        "scratch_root": "/tmp/biologix_openmm_x",
        "files": {
            "structures/c0_complex.pdb": b"PDB",
            "../escape.txt": b"nope",
        },
        "result": {
            "ok": True,
            "structure_artifacts_dir": "/tmp/biologix_openmm_x/structures",
            "candidate_outcomes": [{"complex_pdb_path": "/tmp/biologix_openmm_x/structures/c0_complex.pdb"}],
        },
    }
    out = materialize_remote_result(response, tmp_path)
    assert (tmp_path / "structures" / "c0_complex.pdb").read_bytes() == b"PDB"
    assert not (tmp_path.parent / "escape.txt").exists()
    assert out["structure_artifacts_dir"] == f"{tmp_path}/structures"
    assert out["candidate_outcomes"][0]["complex_pdb_path"] == f"{tmp_path}/structures/c0_complex.pdb"


def test_spec_round_trips_as_plain_data() -> None:
    spec = OpenMMJobSpec(psmiles=["[*]CC[*]"], target_chains="H,L", env={"A": "1"})
    assert OpenMMJobSpec.from_dict(spec.to_dict()) == spec


def test_run_conditions_reach_the_agent_in_candidate_outcomes(monkeypatch) -> None:
    """Counterions and the chain charge must be disclosed beside the energy."""
    import biologix_ai.simulation as simulation_pkg
    from biologix_ai.compute import openmm_job
    from biologix_ai.simulation import openmm_compat

    progress = [
        {
            "index": 0,
            "material_name": "Candidate_0",
            "status": "completed",
            "interaction_energy_kj_mol": -1645.3,
            "openmm_platform": {"name": "CUDA"},
            "counterions": {"residue": "NA", "count": 64, "neutralises": "polymer matrix"},
            "polymer_chain_charge": -8,
            "box_nm": 7.5,
        },
        {"index": 1, "material_name": "Candidate_1", "status": "failed", "reason": "packmol timeout"},
    ]

    class FakeSim:
        def __init__(self, **kwargs):
            pass

        def evaluate_candidates(self, *args, **kwargs):
            return {
                "high_performers": [],
                "effective_mechanisms": [],
                "problematic_features": [],
                "evaluation_progress": progress,
            }

    monkeypatch.setattr(openmm_compat, "openmm_available", lambda: True)
    monkeypatch.setattr(simulation_pkg, "MDSimulator", FakeSim)
    out = openmm_job.run_openmm_job(OpenMMJobSpec(psmiles=["[*]CC([*])C(=O)[O-]"]))

    completed, failed = out["candidate_outcomes"]
    assert completed["counterions"]["count"] == 64
    assert completed["polymer_chain_charge"] == -8
    assert completed["openmm_platform"]["name"] == "CUDA"
    assert failed["reason"] == "packmol timeout"
    assert "counterions" not in failed
