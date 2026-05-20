"""Experiment lifecycle router: create sessions, poll state, retrieve results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from biologix_ai.http_api.schemas import (
    ExperimentStatus,
    FunnelCheckpoint,
    FunnelManifestEntry,
    PipelineAuditRecord,
    SessionResponse,
    StartExperimentRequest,
)

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])

_ROOT = Path(os.environ.get("BIOLOGIX_AI_ROOT", Path(__file__).parents[6]))
_RUNS = _ROOT / "runs"


def _session_path(experiment_id: str) -> Path:
    """Resolve the session directory; raise 404 if not found."""
    p = _RUNS / experiment_id
    if not p.is_dir():
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found under runs/")
    return p


# ---------------------------------------------------------------------------
# POST /api/experiments — start a new biologics session
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=SessionResponse,
    summary="Start a new biologic delivery discovery session",
    description=(
        "Resolves the biologic target PDB, creates a session folder under runs/, "
        "seeds discovery_world.json, and returns the session directory path."
    ),
)
def create_experiment(req: StartExperimentRequest):
    from biologix_ai.services.biologic_resolver import resolve_biologic_target
    from biologix_ai.services.biologics_session import patch_world_retrosynthesis
    from biologix_ai.discovery_world import ensure_world_for_session

    try:
        bio = resolve_biologic_target(
            name_or_pdb_id=req.biologic_target,
            fetch_pdb=req.fetch_pdb,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Biologic resolution failed: {exc}")

    run_name = req.run_name or f"{bio.pdb_id or bio.canonical_name}-discovery"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in run_name).strip("-")
    import time
    ts = time.strftime("%Y%m%d-%H%M%S")
    session_dir = _RUNS / f"{safe_name}-{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)

    try:
        ensure_world_for_session(
            session_dir,
            objective=f"Discover delivery materials for {bio.canonical_name or req.biologic_target}",
        )
    except Exception:
        pass

    note = f"Session created at {session_dir}. PDB resolved: {bio.pdb_path or 'not fetched'}."
    return SessionResponse(
        session_dir=str(session_dir),
        biologic_resolution=bio,
        note=note,
    )


# ---------------------------------------------------------------------------
# GET /api/experiments/{id} — session status summary
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}",
    response_model=ExperimentStatus,
    summary="Get session status and top-level summary",
    description="Returns objective, iteration count, candidate count, and best candidate from discovery_world.json.",
)
def get_experiment(experiment_id: str):
    session = _session_path(experiment_id)
    world_file = session / "discovery_world.json"

    status = ExperimentStatus(
        experiment_id=experiment_id,
        session_dir=str(session),
        biologic_target="",
    )

    if world_file.is_file():
        try:
            world = json.loads(world_file.read_text(encoding="utf-8"))
            status.objective = world.get("objective", "")
            status.updated_at = world.get("meta", {}).get("updated_at")
            status.last_iteration = world.get("meta", {}).get("last_iteration", 0)
            sims = world.get("simulation_entries", [])
            status.candidates_count = len(sims)
            if sims:
                best = min(
                    (s for s in sims if s.get("interaction_energy_kj_mol") is not None),
                    key=lambda s: s["interaction_energy_kj_mol"],
                    default=None,
                )
                if best:
                    status.top_candidate = best.get("psmiles")
            # Derive biologic_target from objective or retro entries
            retro = world.get("retrosynthesis_entries", [])
            if retro:
                status.biologic_target = retro[0].get("biologic_target", "")
        except Exception:
            pass

    return status


# ---------------------------------------------------------------------------
# GET /api/experiments/{id}/world — full discovery world state
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}/world",
    response_model=Dict[str, Any],
    summary="Return the full discovery_world.json for a session",
    description="Structured session state: objective, hypotheses, simulation entries, retrosynthesis entries.",
)
def get_world(experiment_id: str):
    session = _session_path(experiment_id)
    world_file = session / "discovery_world.json"
    if not world_file.is_file():
        return {}
    try:
        return json.loads(world_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /api/experiments/{id}/candidates — ranked candidates list
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}/candidates",
    response_model=List[Dict[str, Any]],
    summary="List ranked candidates for a session",
    description="Reads simulation_entries from discovery_world.json, sorted by interaction energy.",
)
def get_candidates(experiment_id: str):
    session = _session_path(experiment_id)
    world_file = session / "discovery_world.json"
    if not world_file.is_file():
        return []
    try:
        world = json.loads(world_file.read_text(encoding="utf-8"))
        entries = world.get("simulation_entries", [])
        return sorted(
            entries,
            key=lambda e: e.get("interaction_energy_kj_mol") or float("inf"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /api/experiments/{id}/audit — pipeline audit trail
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}/audit",
    response_model=List[PipelineAuditRecord],
    summary="Retrieve the GxP audit trail for a session",
    description="Returns every save_pipeline_stage record in chronological order.",
)
def get_audit(experiment_id: str, candidate_psmiles: Optional[str] = None):
    session = _session_path(experiment_id)
    from biologix_ai.services.pipeline_audit import get_pipeline_audit

    records = get_pipeline_audit(
        session_dir=session,
        candidate_psmiles=candidate_psmiles or "",
    )
    return [PipelineAuditRecord(**r) for r in records]


# ---------------------------------------------------------------------------
# GET /api/experiments/{id}/funnel — pipeline checkpoints
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}/funnel",
    response_model=List[FunnelManifestEntry],
    summary="List all funnel context checkpoints for a session",
    description="Returns the checkpoint manifest: stage names, file names, and save timestamps.",
)
def get_funnel_stages(experiment_id: str):
    session = _session_path(experiment_id)
    from biologix_ai.services.funnel_context import list_funnel_stages

    stages = list_funnel_stages(session)
    return [FunnelManifestEntry(**s) for s in stages]


# ---------------------------------------------------------------------------
# GET /api/experiments/{id}/funnel/{stage} — one checkpoint
# ---------------------------------------------------------------------------

@router.get(
    "/{experiment_id}/funnel/{stage}",
    response_model=Optional[FunnelCheckpoint],
    summary="Retrieve a specific funnel context checkpoint",
    description="Returns checkpoint data for a named pipeline stage, or null if not saved yet.",
)
def get_funnel_checkpoint(experiment_id: str, stage: str):
    session = _session_path(experiment_id)
    from biologix_ai.services.funnel_context import get_funnel_context

    cp = get_funnel_context(session_dir=session, stage=stage)
    if cp is None:
        return None
    return FunnelCheckpoint(
        stage=cp.get("stage", stage),
        saved_at=cp.get("saved_at", ""),
        data=cp.get("data", {}),
    )
