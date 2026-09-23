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


# -- cross-container progress: worker stage -> web container's running job -----


class _FakeModal:
    """Just enough of `modal` to exercise the spawn-and-poll loop."""

    class exception:  # noqa: N801 — mirrors modal.exception
        class TimeoutError(Exception):
            """Modal's 'not ready yet' poll timeout (the base class)."""

        class FunctionTimeoutError(TimeoutError):
            """The worker itself timed out: a real failure."""

        class OutputExpiredError(TimeoutError):
            pass

    def __init__(self, gets, dict_values):
        self.gets = list(gets)              # each: an exception to raise, or a response
        self.dict_values = list(dict_values)
        self.popped = []
        self.spawned = []
        self.remote_calls = []
        outer = self

        class _Call:
            def get(self, timeout=None):
                item = outer.gets.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item

        class _Function:
            def spawn(self, spec):
                outer.spawned.append(spec)
                return _Call()

            def remote(self, spec):
                outer.remote_calls.append(spec)
                return {"result": {"ok": True}, "files": {}, "scratch_root": ""}

        class _Dict:
            def get(self, key, default=None):
                return outer.dict_values.pop(0) if outer.dict_values else None

            def pop(self, key, default=None):
                outer.popped.append(key)

        self.Dict = type("D", (), {"from_name": staticmethod(lambda *a, **k: _Dict())})
        self._function = _Function()


def _no_sleep(monkeypatch):
    monkeypatch.setattr(compute, "PROGRESS_POLL_S", 0.0)


def test_worker_stages_are_relayed_to_the_running_job(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    not_ready = _FakeModal.exception.TimeoutError
    fake = _FakeModal(
        gets=[not_ready(), not_ready(), not_ready(), {"result": {"ok": True}, "files": {}}],
        dict_values=[
            {"stage": "packmol", "message": "packing 8 polymer chains"},
            {"stage": "packmol", "message": "packing 8 polymer chains"},   # unchanged: not repeated
            {"stage": "minimize", "message": "LocalEnergyMinimizer"},
        ],
    )
    events = []
    response = compute._call_worker(fake, fake._function, {"psmiles": ["[*]CC[*]"]}, events.append)

    assert response["result"]["ok"] is True
    assert [e["stage"] for e in events] == ["packmol", "minimize"]
    assert events[0]["message"] == "packing 8 polymer chains"
    assert fake.remote_calls == [] and len(fake.spawned) == 1  # spawned, not blocking remote()
    key = fake.spawned[0]["progress_key"]
    assert key and fake.popped == [key]                        # the key is cleaned up


def test_a_worker_that_times_out_is_a_failure_not_a_reason_to_keep_polling(monkeypatch) -> None:
    """FunctionTimeoutError subclasses the poll timeout: catching the base loops forever."""
    _no_sleep(monkeypatch)
    fake = _FakeModal(gets=[_FakeModal.exception.FunctionTimeoutError("3600s")], dict_values=[])
    with pytest.raises(_FakeModal.exception.FunctionTimeoutError):
        compute._call_worker(fake, fake._function, {}, lambda event: None)
    assert len(fake.popped) == 1  # cleaned up even though it failed
    assert fake.gets == []        # and it did not poll again


def test_an_expired_output_is_not_polled_either(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    fake = _FakeModal(gets=[_FakeModal.exception.OutputExpiredError()], dict_values=[])
    with pytest.raises(_FakeModal.exception.OutputExpiredError):
        compute._call_worker(fake, fake._function, {}, lambda event: None)


def test_without_a_progress_callback_the_call_is_unchanged() -> None:
    fake = _FakeModal(gets=[], dict_values=[])
    response = compute._call_worker(fake, fake._function, {"psmiles": []}, None)
    assert response["result"]["ok"] is True
    assert fake.spawned == [] and len(fake.remote_calls) == 1
    assert "progress_key" not in fake.remote_calls[0]


def test_a_broken_progress_callback_cannot_fail_the_run(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    not_ready = _FakeModal.exception.TimeoutError
    fake = _FakeModal(
        gets=[not_ready(), {"result": {"ok": True}, "files": {}}],
        dict_values=[{"stage": "packmol", "message": "x"}],
    )

    def broken(event):
        raise RuntimeError("observer bug")

    assert compute._call_worker(fake, fake._function, {}, broken)["result"]["ok"] is True


def test_the_worker_writes_throttled_progress_and_survives_an_unreachable_dict(monkeypatch) -> None:
    import sys
    import types

    from biologix_ai.compute import openmm_job

    puts = []

    class _Store:
        def put(self, key, value):
            puts.append((key, value["stage"]))

    fake = types.SimpleNamespace(Dict=types.SimpleNamespace(from_name=lambda *a, **k: _Store()))
    monkeypatch.setitem(sys.modules, "modal", fake)
    write = openmm_job.modal_progress_writer("job-key")
    write({"status": "progress", "stage": "packmol", "message": "a"})
    write({"status": "progress", "stage": "packmol", "message": "b"})   # same stage, too soon
    write({"status": "progress", "stage": "minimize", "message": "c"})  # new stage: written
    write({"status": "completed", "stage": "minimize"})                 # not progress: ignored
    assert puts == [("job-key", "packmol"), ("job-key", "minimize")]

    assert openmm_job.modal_progress_writer("") is None
    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace())  # no Dict at all
    assert openmm_job.modal_progress_writer("job-key") is None
