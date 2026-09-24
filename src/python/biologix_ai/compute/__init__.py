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


PROGRESS_POLL_S = 3.0


def _call_worker(
    modal: Any,
    function: Any,
    spec_dict: Dict[str, Any],
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
) -> Dict[str, Any]:
    """Run *function* on a worker container, relaying its stages while it works.

    ``function.remote()`` blocks silently, so a client checking on the running job
    saw only the call that started it. The call is spawned instead and polled;
    between polls the worker's latest stage (written to a shared Dict under a key
    passed in the spec) is handed to *progress_callback*.
    """
    import uuid  # noqa: PLC0415

    from biologix_ai.compute.openmm_job import PROGRESS_DICT  # noqa: PLC0415

    if progress_callback is None:
        return function.remote(spec_dict)

    from biologix_ai import mcp_jobs  # noqa: PLC0415

    key = uuid.uuid4().hex
    call = None
    resume = mcp_jobs.resumable_worker()
    if resume:
        # The server restarted while this simulation ran; the worker did not. Pick up
        # its call rather than simulating again. A call that has expired falls through
        # to a fresh spawn below.
        try:
            call = modal.FunctionCall.from_id(resume[0])
            key = resume[1] or key
        except Exception:
            call = None
    spec_dict = {**spec_dict, "progress_key": key}
    try:
        store = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)
    except Exception:
        store = None
    reattached = call is not None
    if call is None:
        call = function.spawn(spec_dict)
        mcp_jobs.note_worker_call(getattr(call, "object_id", ""), key)
    seen: Any = None
    try:
        while True:
            try:
                response = call.get(timeout=PROGRESS_POLL_S)
                break
            except Exception as exc:
                # Observed on real Modal: from_id() never fails, a call that no longer
                # exists raises NotFoundError (or OutputExpiredError) from get().
                if reattached and type(exc).__name__ in ("NotFoundError", "OutputExpiredError"):
                    reattached = False
                    call = function.spawn(spec_dict)
                    mcp_jobs.note_worker_call(getattr(call, "object_id", ""), key)
                    continue
                if not isinstance(exc, TimeoutError):
                    raise
                # Not ready yet. Verified against the real client: a poll that
                # times out raises Python's builtin TimeoutError, not
                # modal.exception.TimeoutError. The worker's own timeout
                # (FunctionTimeoutError) and an expired output are Modal
                # exceptions that are not builtin TimeoutErrors, so they are not
                # caught here and end the call, as they should.
                pass
            entry = None
            if store is not None:
                try:
                    entry = store.get(key)
                except Exception:
                    entry = None
            # Compare stage and message, not the whole entry: it carries a timestamp,
            # so a worker re-reporting the same stage would otherwise repeat.
            marker = (entry.get("stage"), entry.get("message")) if entry else None
            if entry and marker != seen:
                seen = marker
                try:
                    progress_callback(
                        {
                            "status": "progress",
                            "stage": entry.get("stage", ""),
                            "message": entry.get("message", ""),
                        }
                    )
                except Exception:
                    pass
        return response
    finally:
        if store is not None:
            try:
                store.pop(key)
            except Exception:
                pass


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
        response = _call_worker(modal, function, spec_dict, progress_callback)
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
