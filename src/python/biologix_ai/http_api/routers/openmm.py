"""OpenMM screening router with background jobs and SSE status (MCP parity)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from biologix_ai.http_api.deps import session_path
from biologix_ai.http_api.jobs import create_job, read_job, run_job_in_background

router = APIRouter(prefix="/api/openmm", tags=["OpenMM"])

_POLL_INTERVAL = 0.5
_KEEPALIVE_INTERVAL = 15


class OpenMMEvaluateRequest(BaseModel):
    psmiles_list: List[str] = Field(..., min_length=1)
    experiment_id: str = ""
    verbose: bool = True
    max_workers: Optional[int] = None
    response_format: Literal["full", "concise"] = "full"
    run_in_background: bool = True


def _evaluate_psmiles(payload: OpenMMEvaluateRequest) -> Dict[str, Any]:
    """Run OpenMM matrix evaluation (mirrors MCP openmm_evaluate_psmiles)."""
    parts = [p.strip() for p in payload.psmiles_list if p.strip()]
    if not parts:
        return {"ok": False, "error": "psmiles_list is empty"}

    from biologix_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        return {
            "ok": False,
            "error": (
                "OpenMM screening stack incomplete (openmm, openmmforcefields, openff.toolkit, "
                "and AmberTools antechamber/parmchk2 on PATH). Run ./install."
            ),
        }

    from biologix_ai.simulation import MDSimulator

    session = session_path(payload.experiment_id) if payload.experiment_id else None
    artifacts_dir = None
    if session:
        artifacts_dir = str(session / "structures")

    candidates = [{"material_name": f"Candidate_{i}", "chemical_structure": p} for i, p in enumerate(parts)]
    sim = MDSimulator(n_steps=5000)
    concise = payload.response_format.strip().lower() == "concise"
    result = sim.evaluate_candidates(
        candidates,
        max_candidates=len(candidates),
        verbose=payload.verbose,
        artifacts_dir=artifacts_dir,
        max_workers=payload.max_workers,
    )

    try:
        from biologix_ai.simulation.scoring import discovery_score

        score = discovery_score(result)
    except Exception:
        score = None

    candidate_outcomes = []
    for ep in result.get("evaluation_progress") or []:
        status = ep.get("status", "unknown")
        oc: Dict[str, Any] = {
            "index": ep.get("index"),
            "material_name": ep.get("material_name"),
            "status": status,
        }
        if status == "completed":
            oc["interaction_energy_kj_mol"] = ep.get("interaction_energy_kj_mol")
        else:
            if ep.get("stage"):
                oc["stage"] = ep["stage"]
            if ep.get("reason"):
                oc["reason"] = ep["reason"]
        candidate_outcomes.append(oc)

    out: Dict[str, Any] = {
        "ok": True,
        "high_performers": result["high_performers"],
        "effective_mechanisms": result["effective_mechanisms"],
        "problematic_features": result["problematic_features"],
        "candidate_outcomes": candidate_outcomes,
    }
    if result.get("property_analysis"):
        out["property_analysis"] = result["property_analysis"]
    if score is not None:
        out["discovery_score"] = round(score, 4)
    if not concise:
        if payload.verbose and result.get("evaluation_progress") is not None:
            out["evaluation_progress"] = result["evaluation_progress"]
        if result.get("evaluation_note"):
            out["evaluation_note"] = result["evaluation_note"]
    if result.get("structure_artifacts_dir"):
        out["structure_artifacts_dir"] = result["structure_artifacts_dir"]
    if not concise:
        raw = result.get("md_results_raw") or []
        paths = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            paths.append({
                "psmiles": r.get("psmiles"),
                "complex_pdb_path": r.get("complex_pdb_path"),
                "monomer_png_path": r.get("monomer_png_path"),
                "complex_preview_png_path": r.get("complex_preview_png_path"),
                "complex_chemviz_png_path": r.get("complex_chemviz_png_path"),
                "packing_metrics": r.get("packing_metrics"),
            })
        if paths:
            out["structure_artifact_paths"] = paths
    return out


@router.post("/evaluate", summary="Evaluate PSMILES via OpenMM matrix screening")
def evaluate_openmm(req: OpenMMEvaluateRequest) -> Dict[str, Any]:
    if not req.run_in_background:
        return _evaluate_psmiles(req)

    experiment_id = req.experiment_id or None
    if req.experiment_id:
        session_path(req.experiment_id)

    job_id = create_job(experiment_id)
    run_job_in_background(job_id, experiment_id, lambda: _evaluate_psmiles(req))
    return {
        "job_id": job_id,
        "experiment_id": experiment_id,
        "status": "started",
        "poll_url": f"/api/openmm/jobs/{job_id}",
        "stream_url": f"/api/openmm/jobs/{job_id}/stream"
        + (f"?experiment_id={experiment_id}" if experiment_id else ""),
    }


@router.get("/jobs/{job_id}", summary="Poll OpenMM job status")
def get_openmm_job(job_id: str, experiment_id: str = "") -> Dict[str, Any]:
    job = read_job(job_id, experiment_id or None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


async def _tail_job(job_id: str, experiment_id: Optional[str], request: Request):
    last_payload = ""
    last_keepalive = asyncio.get_event_loop().time()

    while True:
        if await request.is_disconnected():
            yield "event: done\ndata: {}\n\n"
            return

        job = read_job(job_id, experiment_id)
        if job:
            payload = json.dumps(job, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
                if job.get("status") in ("completed", "failed"):
                    yield "event: done\ndata: {}\n\n"
                    return

        now = asyncio.get_event_loop().time()
        if now - last_keepalive >= _KEEPALIVE_INTERVAL:
            yield ": keepalive\n\n"
            last_keepalive = now

        await asyncio.sleep(_POLL_INTERVAL)


@router.get(
    "/jobs/{job_id}/stream",
    summary="Stream OpenMM job status via SSE",
    response_class=StreamingResponse,
)
async def stream_openmm_job(job_id: str, request: Request, experiment_id: str = ""):
    job = read_job(job_id, experiment_id or None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return StreamingResponse(
        _tail_job(job_id, experiment_id or None, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
