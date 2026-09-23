"""Regression tests for the isolated ADMET-AI runtime."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from biologix_ai.services import toxicity_service
from scripts import run_admet_isolated
from scripts.run_admet_isolated import normalize_predictions


def test_admet_batch_uses_one_isolated_subprocess(
    monkeypatch,
    tmp_path: Path,
) -> None:
    admet_python = tmp_path / "bin" / "python"
    admet_python.parent.mkdir(parents=True)
    admet_python.write_text("#!/bin/sh\n", encoding="utf-8")
    admet_python.chmod(0o755)
    monkeypatch.setenv("BIOLOGIX_ADMET_PYTHON", str(admet_python))
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        payload = {
            "predictions": [
                {"smiles": "CCO", "predictions": {"AMES": 0.1}},
                {"smiles": "CC(=O)O", "predictions": {"AMES": 0.2}},
            ]
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(toxicity_service.subprocess, "run", fake_run)

    results = toxicity_service.screen_monomers_batch(["CCO", "CC(=O)O"])

    assert len(calls) == 1
    assert json.loads(calls[0]["input"]) == {"smiles": ["CCO", "CC(=O)O"]}
    assert calls[0]["env"]["LD_LIBRARY_PATH"] == str(admet_python.parent.parent / "lib")
    assert "PYTHONPATH" not in calls[0]["env"]
    assert [result.admet.predictions["AMES"] for result in results] == [0.1, 0.2]


def test_admet_subprocess_failure_is_reported_as_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    admet_python = tmp_path / "bin" / "python"
    admet_python.parent.mkdir(parents=True)
    admet_python.write_text("#!/bin/sh\n", encoding="utf-8")
    admet_python.chmod(0o755)
    monkeypatch.setenv("BIOLOGIX_ADMET_PYTHON", str(admet_python))

    def fail_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="model load failed")

    monkeypatch.setattr(toxicity_service.subprocess, "run", fail_run)

    result = toxicity_service.screen_monomer("CCO")

    assert result.admet is not None
    assert result.admet.available is False
    assert any("ADMET-AI unavailable" in warning for warning in result.warnings)


def test_install_script_separates_admet_from_aizynth_environment() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts" / "install_submodules.sh").read_text(encoding="utf-8")
    environment = (repo_root / "environment-simulation.yml").read_text(encoding="utf-8")
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")

    assert "biologix-admet" in script
    assert 'pip_in_env install -e "extern/admet_ai"' not in script
    assert '--no-deps -e "extern/admet_ai"' in script
    assert 'PYTHONPATH="" "$ADMET_PYTHON" -m pip install \\\n  "rdkit>=2025.9.5"' in script
    assert 'PYTHONPATH="" "$ADMET_PYTHON" -m pip check' in script
    assert "rdkit>=2023.9.1,<2024" in environment
    assert "networkx>=2.8,<3" in pyproject
    assert "bibtexparser>=1.4,<2" in pyproject
    assert "BIOLOGIX_ADMET_PYTHON=/opt/conda/envs/biologix-admet/bin/python" in dockerfile
    assert "PYTHONPATH=/app:/app/src/python:/app/extern/RetroSynthesisAgent" in dockerfile


def test_normalize_isolated_admet_dataframe_preserves_input_order() -> None:
    raw_predictions = pd.DataFrame(
        [{"AMES": 0.2}, {"AMES": 0.1}],
        index=["CC(=O)O", "CCO"],
    )

    rows = normalize_predictions(raw_predictions, ["CCO", "CC(=O)O"])

    assert [row["smiles"] for row in rows] == ["CCO", "CC(=O)O"]
    assert [row["predictions"]["AMES"] for row in rows] == [0.1, 0.2]


def test_isolated_admet_stdout_contains_only_json(
    monkeypatch,
) -> None:
    class NoisyModel:
        def __init__(self) -> None:
            print("loading weights")

        def predict(self, smiles):
            print("predicting")
            return {"AMES": 0.1}

    standard_input = io.StringIO(json.dumps({"smiles": ["CCO"]}))
    standard_output = io.StringIO()
    standard_error = io.StringIO()
    monkeypatch.setattr(sys, "stdin", standard_input)
    monkeypatch.setattr(sys, "stdout", standard_output)
    monkeypatch.setattr(sys, "stderr", standard_error)
    monkeypatch.setattr(
        run_admet_isolated.importlib,
        "import_module",
        lambda _name: SimpleNamespace(ADMETModel=NoisyModel),
    )

    run_admet_isolated.main()

    assert json.loads(standard_output.getvalue())["predictions"][0]["smiles"] == "CCO"
    assert "loading weights" not in standard_output.getvalue()
    assert "loading weights" in standard_error.getvalue()
