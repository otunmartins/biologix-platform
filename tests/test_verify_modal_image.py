"""Tests for the fail-fast Modal image verifier."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.verify_modal_image import (
    REQUIRED_EXECUTABLES,
    REQUIRED_FILES,
    REQUIRED_GLOBS,
    REQUIRED_MODULES,
    ImageVerificationError,
    verify_image,
)


def test_oauth_encryption_runtime_is_part_of_image_audit() -> None:
    assert "cryptography.fernet" in REQUIRED_MODULES


def _populate_required_files(app_root: Path, system_root: Path) -> None:
    for relative_path, minimum_bytes in REQUIRED_FILES.items():
        root = system_root if relative_path.startswith(("opt/", "root/")) else app_root
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * minimum_bytes)
    for pattern, minimum_matches in REQUIRED_GLOBS.items():
        directory = app_root / pattern.split("*", maxsplit=1)[0]
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(minimum_matches):
            (directory / f"model_{index}.pt").write_bytes(b"model")


def test_verify_image_accepts_complete_dependency_set(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    system_root = tmp_path / "system"
    _populate_required_files(app_root, system_root)
    imported: list[str] = []

    def load_module(name: str) -> object:
        imported.append(name)
        return object()

    commands: list[list[str]] = []

    def run_command(command, **kwargs):
        commands.append(command)
        stdout = ""
        if command[-1].endswith("run_admet_isolated.py"):
            stdout = json.dumps(
                {
                    "predictions": [
                        {"smiles": "CCO", "predictions": {"AMES": 0.1}}
                    ]
                }
            )
        elif command[-1].endswith("verify_scientific_assets.py"):
            stdout = json.dumps(
                {
                    "ok": True,
                    "precursor_entries": 1017,
                    "molport_inchikeys": 1_196_975,
                    "zinc_inchikeys": 17_422_831,
                }
            )
        elif command[-1].endswith("verify_retrosynthesis_stack.py"):
            stdout = "AiZynth child progress\n" + json.dumps(
                {
                    "ok": True,
                    "aizynth_monomers_attempted": 1,
                    "aizynth_monomers_solved": 1,
                }
            )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    report = verify_image(
        app_root=app_root,
        system_root=system_root,
        module_loader=load_module,
        executable_finder=lambda name: f"/usr/bin/{name}",
        distribution_version=lambda _name: "1.30.0",
        command_runner=run_command,
        environment={
            "BIOLOGIX_AI_AIZYNTH_CONFIG": str(
                app_root / "data" / "aizynthfinder" / "config.yml"
            ),
            "RETRO_LLM_BACKEND": "skip",
        },
    )

    assert set(imported) == set(REQUIRED_MODULES)
    assert report.modules == len(REQUIRED_MODULES)
    assert report.executables == len(REQUIRED_EXECUTABLES)
    assert report.files >= len(REQUIRED_FILES)
    assert sum(command[-2:] == ["pip", "check"] for command in commands) == 2
    assert any(command[-1].endswith("run_admet_isolated.py") for command in commands)
    assert any(command[-1].endswith("verify_scientific_assets.py") for command in commands)
    assert any(
        command[-1].endswith("verify_retrosynthesis_stack.py")
        for command in commands
    )


def test_verify_image_reports_all_missing_requirements(tmp_path: Path) -> None:
    def fail_import(name: str) -> object:
        raise ImportError(name)

    with pytest.raises(ImageVerificationError) as error_info:
        verify_image(
            app_root=tmp_path / "app",
            system_root=tmp_path / "system",
            module_loader=fail_import,
            executable_finder=lambda _name: None,
            command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command, 1, "", "dependency conflict"
            ),
        )

    message = str(error_info.value)
    assert "module:openmm" in message
    assert "executable:packmol" in message
    assert "data/aizynthfinder/config.yml" in message
    assert "admet_ai/resources/models" in message
    assert "scientific-assets:dependency conflict" in message
    assert "retrosynthesis-smoke:dependency conflict" in message
