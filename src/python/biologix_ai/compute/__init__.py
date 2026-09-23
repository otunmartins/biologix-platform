"""Where OpenMM runs: in this process, or on a Modal CPU or GPU worker.

``BIOLOGIX_AI_COMPUTE_BACKEND``
    ``local`` (default): run in the MCP server process, as always.
    ``modal``: call the ``openmm_worker_cpu`` / ``openmm_worker_gpu`` functions of
    the deployed Modal app (``BIOLOGIX_MODAL_APP``, default ``biologix-mcp``).
``BIOLOGIX_DEFAULT_COMPUTE``
    ``cpu`` (default) or ``gpu``: used when a call does not pass ``compute``.

Locally, ``compute="gpu"`` selects OpenMM's CUDA platform in this process and
fails loudly when no GPU is present; it never silently runs on CPU.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from biologix_ai.compute.openmm_job import (
    OpenMMJobSpec,
    materialize_remote_result,
    run_openmm_job,
    target_for_spec,
)

BACKEND_ENV = "BIOLOGIX_AI_COMPUTE_BACKEND"
DEFAULT_COMPUTE_ENV = "BIOLOGIX_DEFAULT_COMPUTE"
MODAL_APP_ENV = "BIOLOGIX_MODAL_APP"
WORKER_FUNCTIONS = {"cpu": "openmm_worker_cpu", "gpu": "openmm_worker_gpu"}
LOCAL_PLATFORMS = {"cpu": "", "gpu": "CUDA"}
# Settings the worker must share with the web container for identical physics.
_FORWARDED_ENV_PREFIXES = ("BIOLOGIX_AI_OPENMM_", "BIOLOGIX_AI_EVAL_", "BIOLOGIX_AI_GMX_")


class ComputeError(ValueError):
    """Unknown compute target or backend."""


def backend() -> str:
    value = os.environ.get(BACKEND_ENV, "local").strip().lower() or "local"
    if value not in ("local", "modal"):
        raise ComputeError(f"{BACKEND_ENV}={value!r} must be 'local' or 'modal'")
    return value


def resolve_compute(requested: str = "") -> str:
    """``cpu`` or ``gpu``: *requested*, else ``BIOLOGIX_DEFAULT_COMPUTE``, else ``cpu``."""
    value = (requested or os.environ.get(DEFAULT_COMPUTE_ENV, "cpu")).strip().lower() or "cpu"
    if value not in WORKER_FUNCTIONS:
        raise ComputeError(f"compute={value!r} must be 'cpu' or 'gpu'")
    return value


def describe() -> Dict[str, Any]:
    """Compute configuration for status tools."""
    info: Dict[str, Any] = {"backend": os.environ.get(BACKEND_ENV, "local") or "local"}
    try:
        info["default_compute"] = resolve_compute("")
    except ComputeError as exc:
        info["default_compute_error"] = str(exc)
    if info["backend"] == "modal":
        info["modal_app"] = os.environ.get(MODAL_APP_ENV, "biologix-mcp")
        info["workers"] = dict(WORKER_FUNCTIONS)
    return info


def _forwarded_env() -> Dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k.startswith(_FORWARDED_ENV_PREFIXES) and k != "BIOLOGIX_AI_OPENMM_PLATFORM"
    }


def run_openmm(
    spec: OpenMMJobSpec,
    *,
    compute: str,
    target_pdb_path: str = "",
    run_dir: Optional[Path] = None,
    artifacts_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run one OpenMM evaluation on *compute* and return the tool payload.

    The payload gains ``compute`` (``{"target", "backend", "worker"}``) so the
    report can say where the numbers came from.
    """
    compute = resolve_compute(compute)
    mode = backend()
    if mode == "modal":
        import modal  # noqa: PLC0415 — only the Modal image has it

        app_name = os.environ.get(MODAL_APP_ENV, "biologix-mcp")
        worker = WORKER_FUNCTIONS[compute]
        spec.env = _forwarded_env()
        spec_dict = spec.to_dict()
        spec_dict.update(target_for_spec(target_pdb_path))
        function = modal.Function.from_name(app_name, worker)
        response = function.remote(spec_dict)
        payload = materialize_remote_result(response, run_dir)
        payload["compute"] = {"target": compute, "backend": "modal", "worker": worker}
        return payload

    spec.openmm_platform = spec.openmm_platform or LOCAL_PLATFORMS[compute]
    payload = run_openmm_job(
        spec,
        target_pdb_path=target_pdb_path,
        artifacts_dir=artifacts_dir,
        progress_callback=progress_callback,
    )
    payload["compute"] = {"target": compute, "backend": "local", "worker": "in-process"}
    return payload


__all__ = [
    "BACKEND_ENV",
    "DEFAULT_COMPUTE_ENV",
    "ComputeError",
    "OpenMMJobSpec",
    "backend",
    "describe",
    "resolve_compute",
    "run_openmm",
]
