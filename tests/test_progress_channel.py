"""The channel that carries a running simulation's stages back to a check-back.

A candidate runs in a worker process, and the stage hook lives only in the
parent, so before this channel every check-back on a running OpenMM job reported
the call that started it, however far the simulation had got.
"""

from __future__ import annotations

import glob
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "python"))

pytest.importorskip("openmm")

from biologix_ai.simulation import md_simulator  # noqa: E402
from biologix_ai.simulation import openmm_complex  # noqa: E402


def _slow_worker(psmiles, matrix_kw, progress_file):
    """Pool child that reports a stage, then runs past its time budget."""
    os.environ[openmm_complex.PROGRESS_FILE_ENV] = progress_file
    openmm_complex._stage_heartbeat("packmol", "packing 8 polymer chains")
    time.sleep(30)
    return {"ok": True}


def _quick_worker(psmiles, matrix_kw, progress_file):
    os.environ[openmm_complex.PROGRESS_FILE_ENV] = progress_file
    openmm_complex._stage_heartbeat("minimize", "LocalEnergyMinimizer")
    openmm_complex._stage_heartbeat("energy_eval", "computing interaction energy")
    return {"ok": True, "interaction_energy_kj_mol": -1.0}


def _leftover_progress_dirs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "biologix_progress_*")))


def test_a_stage_reaches_the_file_even_when_stderr_is_silenced(tmp_path, monkeypatch) -> None:
    progress = tmp_path / "progress.tsv"
    monkeypatch.setenv(openmm_complex.PROGRESS_FILE_ENV, str(progress))
    monkeypatch.setenv("BIOLOGIX_AI_EVAL_QUIET", "1")  # the Modal/Docker default
    openmm_complex._stage_heartbeat("packmol", "packing   8\nchains")
    assert progress.read_text() == "packmol\tpacking 8 chains\n"


def test_without_a_progress_file_nothing_is_written(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(openmm_complex.PROGRESS_FILE_ENV, raising=False)
    monkeypatch.setenv("BIOLOGIX_AI_EVAL_QUIET", "1")
    openmm_complex._stage_heartbeat("packmol", "x")  # must not raise
    monkeypatch.setenv(openmm_complex.PROGRESS_FILE_ENV, str(tmp_path / "missing" / "p.tsv"))
    openmm_complex._stage_heartbeat("packmol", "x")  # an unwritable path must not raise


def test_the_tailer_delivers_lines_as_they_arrive_and_drains_at_the_end(tmp_path) -> None:
    path = tmp_path / "p.tsv"
    path.touch()
    seen = []
    done = threading.Event()

    def writer():
        for stage in ("packmol", "minimize", "energy_eval"):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"{stage}\tstep\n")
            time.sleep(0.15)
        done.set()

    threading.Thread(target=writer).start()
    finished = md_simulator._tail_progress_file(
        str(path), done.is_set, lambda stage, msg: seen.append(stage), poll_s=0.05
    )
    assert finished is True
    assert seen == ["packmol", "minimize", "energy_eval"]  # nothing lost at the end


def test_the_tailer_hands_control_back_at_the_deadline(tmp_path) -> None:
    """It replaces a blocking wait, so it must not swallow the candidate time limit."""
    path = tmp_path / "p.tsv"
    path.touch()
    started = time.monotonic()
    finished = md_simulator._tail_progress_file(
        str(path), lambda: False, lambda *_: None,
        deadline=time.monotonic() + 0.4, poll_s=0.05,
    )
    assert finished is False
    assert time.monotonic() - started < 2.0


def test_a_failing_observer_cannot_fail_the_simulation(tmp_path) -> None:
    path = tmp_path / "p.tsv"
    path.write_text("packmol\tx\n")

    def broken(stage, message):
        raise RuntimeError("observer bug")

    assert md_simulator._tail_progress_file(str(path), lambda: True, broken) is True


def test_cpu_worker_stages_reach_the_parent_and_the_temp_dir_is_removed(monkeypatch) -> None:
    monkeypatch.setattr(md_simulator, "_matrix_worker", _quick_worker)
    before = _leftover_progress_dirs()
    seen = []
    result = md_simulator._run_matrix_eval_with_timeout(
        "[*]CC[*]", {}, 60.0, on_progress=lambda stage, msg: seen.append((stage, msg))
    )
    assert result["interaction_energy_kj_mol"] == -1.0
    assert seen == [("minimize", "LocalEnergyMinimizer"), ("energy_eval", "computing interaction energy")]
    assert _leftover_progress_dirs() == before


def test_a_slow_cpu_candidate_is_still_killed_at_its_time_limit(monkeypatch) -> None:
    """Tailing progress must not turn the candidate timeout into a suggestion."""
    monkeypatch.setattr(md_simulator, "_matrix_worker", _slow_worker)
    before = _leftover_progress_dirs()
    seen = []
    started = time.monotonic()
    result = md_simulator._run_matrix_eval_with_timeout(
        "[*]CC[*]", {}, 2.0, on_progress=lambda stage, msg: seen.append(stage)
    )
    assert result["ok"] is False and result["stage"] == "timeout"
    assert "BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S=2.0" in result["error"]
    assert time.monotonic() - started < 20  # the worker sleeps 30 s; it was killed
    assert seen == ["packmol"]  # what it reported before the limit is not lost
    assert _leftover_progress_dirs() == before


def test_without_a_callback_the_cpu_path_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(md_simulator, "_matrix_worker", _quick_worker)
    result = md_simulator._run_matrix_eval_with_timeout("[*]CC[*]", {}, 60.0)
    assert result["ok"] is True


def test_the_worker_never_leaks_the_progress_file_into_the_parent(monkeypatch) -> None:
    monkeypatch.delenv(openmm_complex.PROGRESS_FILE_ENV, raising=False)
    monkeypatch.setattr(md_simulator, "_matrix_worker", _quick_worker)
    md_simulator._run_matrix_eval_with_timeout("[*]CC[*]", {}, 60.0, on_progress=lambda *_: None)
    assert openmm_complex.PROGRESS_FILE_ENV not in os.environ
