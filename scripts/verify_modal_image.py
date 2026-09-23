#!/usr/bin/env python3
"""Fail a container build when the full Biologix runtime is incomplete."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REQUIRED_MODULES = (
    "aizynthfinder",
    "cryptography.fernet",
    "datasets",
    "dotenv",
    "duckduckgo_search",
    "fake_useragent",
    "fitz",
    "fpdf",
    "GPy",
    "graphviz",
    "gymnasium",
    "h5py",
    "hf_transfer",
    "huggingface_hub",
    "jsonpickle",
    "langchain",
    "loguru",
    "markdown",
    "matplotlib",
    "mcp.server.fastmcp",
    "networkx",
    "numpy",
    "ollama",
    "openai",
    "openff.toolkit",
    "openmm",
    "openmmforcefields",
    "optuna",
    "pandas",
    "paperqa",
    "paretoset",
    "pdbfixer",
    "PIL",
    "psmiles",
    "psp",
    "pubchempy",
    "pyvis",
    "rdchiral",
    "rdkit",
    "requests",
    "RetroSynAgent.treeBuilder",
    "scholarly",
    "scipy",
    "selenium",
    "stable_baselines3",
    "torch",
    "uvicorn",
)

REQUIRED_EXECUTABLES = (
    "antechamber",
    "git",
    "packmol",
    "parmchk2",
)

REQUIRED_FILES = {
    "data/aizynthfinder/config.yml": 100,
    "data/aizynthfinder/uspto_model.onnx": 100_000,
    "data/aizynthfinder/uspto_templates.csv.gz": 100_000,
    "data/aizynthfinder/uspto_ringbreaker_model.onnx": 100_000,
    "data/aizynthfinder/uspto_ringbreaker_templates.csv.gz": 100_000,
    "data/aizynthfinder/uspto_filter_model.onnx": 100_000,
    "data/aizynthfinder/zinc_stock.hdf5": 1_000_000,
    "data/retrosynthesis/precursors.json": 100,
    "data/retrosynthesis/molport_inchikeys.pkl": 1_000,
    "data/retrosynthesis/emol.json": 2,
    "extern/RetroSynthesisAgent/RetroSynAgent/emol.json": 2,
    "src/python/biologix_ai/simulation/data/4F1C.pdb": 1_000,
    "src/python/biologix_ai/simulation/data/insulin_AB.pdb": 1_000,
    "data/biologics/biologic_1BUY.pdb": 1_000,
    "data/biologics/biologic_3WD5.pdb": 1_000,
    ".data-seed/aizynthfinder/config.yml": 100,
    ".data-seed/retrosynthesis/precursors.json": 100,
    ".data-seed/retrosynthesis/molport_inchikeys.pkl": 1_000,
    ".opencode-version": 1,
    "opt/conda/envs/pymol-viz/bin/pymol": 1,
    # OpenMM's CUDA platform for the Modal GPU worker (conda-forge ships it with openmm).
    "opt/conda/envs/biologix-ai-sim/lib/plugins/libOpenMMCUDA.so": 1_000,
    "src/python/biologix_ai/protocol/PROTOCOL.md": 1_000,
    "opt/conda/envs/biologix-admet/bin/python": 1,
    "root/.opencode/bin/opencode": 1,
}

REQUIRED_GLOBS = {
    "extern/admet_ai/admet_ai/resources/models/**/*.pt": 10,
}


class ImageVerificationError(RuntimeError):
    """Raised when one or more required image assets are unavailable."""


@dataclass(frozen=True)
class VerificationReport:
    """Counts of dependencies validated in the container image."""

    modules: int
    executables: int
    files: int
    distributions: int
    scientific_audits: int


def _version_tuple(raw_version: str) -> tuple[int, int]:
    parts = raw_version.split(".", maxsplit=2)
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as error:
        raise ImageVerificationError(f"Unparseable package version: {raw_version}") from error


def _last_json_object(output: str) -> dict[str, Any]:
    """Return the final JSON object from output that may include child-process logs."""
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("no JSON object found")


def verify_image(
    app_root: Path = Path("/app"),
    system_root: Path = Path("/"),
    module_loader: Callable[[str], Any] = importlib.import_module,
    executable_finder: Callable[[str], str | None] = shutil.which,
    distribution_version: Callable[[str], str] = importlib.metadata.version,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    environment: Mapping[str, str] = os.environ,
) -> VerificationReport:
    """Validate imports, executables, model data, and runtime wiring."""
    failures: list[str] = []

    for module_name in REQUIRED_MODULES:
        try:
            module_loader(module_name)
        except Exception as error:
            failures.append(f"module:{module_name} ({type(error).__name__}: {error})")

    for executable in REQUIRED_EXECUTABLES:
        if executable_finder(executable) is None:
            failures.append(f"executable:{executable}")

    file_count = 0
    for relative_path, minimum_bytes in REQUIRED_FILES.items():
        root = system_root if relative_path.startswith(("opt/", "root/")) else app_root
        path = root / relative_path
        try:
            size = path.stat().st_size
        except OSError:
            failures.append(f"file:{path} (missing)")
            continue
        if size < minimum_bytes:
            failures.append(f"file:{path} ({size} bytes; need {minimum_bytes})")
            continue
        file_count += 1

    for pattern, minimum_matches in REQUIRED_GLOBS.items():
        matches = [path for path in app_root.glob(pattern) if path.is_file()]
        if len(matches) < minimum_matches:
            failures.append(
                f"glob:{pattern} ({len(matches)} matches; need {minimum_matches})"
            )
        file_count += len(matches)

    try:
        mcp_version = distribution_version("mcp")
        if not (1, 30) <= _version_tuple(mcp_version) < (2, 0):
            failures.append(f"distribution:mcp ({mcp_version}; require >=1.30,<2)")
    except importlib.metadata.PackageNotFoundError:
        failures.append("distribution:mcp (missing)")

    expected_config = str(app_root / "data" / "aizynthfinder" / "config.yml")
    if environment.get("BIOLOGIX_AI_AIZYNTH_CONFIG") != expected_config:
        failures.append(
            "environment:BIOLOGIX_AI_AIZYNTH_CONFIG "
            f"(require {expected_config})"
        )
    if environment.get("RETRO_LLM_BACKEND") != "skip":
        failures.append("environment:RETRO_LLM_BACKEND (require skip)")

    admet_python = system_root / "opt" / "conda" / "envs" / "biologix-admet" / "bin" / "python"
    clean_environment = dict(environment)
    clean_environment.pop("PYTHONPATH", None)
    dependency_checks = (
        ("main-pip-check", [sys.executable, "-m", "pip", "check"]),
        ("admet-pip-check", [str(admet_python), "-m", "pip", "check"]),
    )
    for label, command in dependency_checks:
        completed = command_runner(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=clean_environment,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown conflict").strip()
            failures.append(f"{label}:{detail}")

    admet_runner = app_root / "scripts" / "run_admet_isolated.py"
    admet_environment = dict(clean_environment)
    admet_environment["LD_LIBRARY_PATH"] = str(admet_python.parent.parent / "lib")
    admet_smoke = command_runner(
        [str(admet_python), str(admet_runner)],
        input=json.dumps({"smiles": ["CCO"]}),
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
        cwd=str(app_root),
        env=admet_environment,
    )
    if admet_smoke.returncode != 0:
        detail = (admet_smoke.stderr or admet_smoke.stdout or "unknown error").strip()
        failures.append(f"admet-smoke:{detail}")
    else:
        try:
            admet_payload = json.loads(admet_smoke.stdout)
            rows = admet_payload.get("predictions", [])
            predictions = rows[0].get("predictions", {}) if rows else {}
            if not predictions:
                failures.append("admet-smoke:no predictions returned")
        except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
            failures.append("admet-smoke:invalid JSON output")

    scientific_checks = (
        (
            "scientific-assets",
            app_root / "scripts" / "verify_scientific_assets.py",
            300,
        ),
        (
            "retrosynthesis-smoke",
            app_root / "scripts" / "verify_retrosynthesis_stack.py",
            900,
        ),
    )
    scientific_audits = 0
    for label, script_path, timeout_seconds in scientific_checks:
        completed = command_runner(
            [sys.executable, str(script_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(app_root),
            env=dict(environment),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            failures.append(f"{label}:{detail}")
            continue
        try:
            payload = _last_json_object(completed.stdout)
            if payload.get("ok") is not True:
                failures.append(f"{label}:success payload did not contain ok=true")
                continue
            if label == "retrosynthesis-smoke" and (
                int(payload.get("aizynth_monomers_attempted") or 0) < 1
                or int(payload.get("aizynth_monomers_solved") or 0) < 1
            ):
                failures.append(f"{label}:AiZynthFinder route was not attempted and solved")
                continue
            scientific_audits += 1
        except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
            failures.append(f"{label}:invalid JSON output")

    if failures:
        raise ImageVerificationError(
            "Modal image verification failed:\n- " + "\n- ".join(failures)
        )

    return VerificationReport(
        modules=len(REQUIRED_MODULES),
        executables=len(REQUIRED_EXECUTABLES),
        files=file_count,
        distributions=1,
        scientific_audits=scientific_audits,
    )


def main() -> None:
    """Run image verification and print a machine-readable success report."""
    report = verify_image()
    print(json.dumps({"ok": True, **asdict(report)}, sort_keys=True))


if __name__ == "__main__":
    main()
