"""Experiment lifecycle router: create sessions, poll state, retrieve results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix_ai.discovery_world import (
    apply_patch,
    ensure_world_for_session,
    load_world,
    planning_context,
    save_world,
    touch_meta_after_iteration,
    world_path_for_session,
)
from biologix_ai.http_api.deps import RUNS_DIR, _ROOT, session_path
from biologix_ai.http_api.schemas import (
    ExperimentStatus,
    FunnelCheckpoint,
    FunnelManifestEntry,
    PipelineAuditRecord,
    SessionResponse,
    StartExperimentRequest,
)
from biologix_ai.run_paths import ENV_SESSION, new_session_dir

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])

def _session_path(experiment_id: str) -> Path:
    """Resolve the session directory; raise 404 if not found."""
    return session_path(experiment_id)


class StartDiscoverySessionRequest(BaseModel):
    run_name: str = ""


class RunAutonomousDiscoveryRequest(BaseModel):
    budget_minutes: float = Field(default=60.0, ge=1.0)
    run_in_background: bool = True
    run_name: str = ""
    md_steps: int = Field(default=5000, ge=100)
    max_eval_per_iteration: int = Field(default=8, ge=1, le=50)


class SaveDiscoveryStateRequest(BaseModel):
    iteration: int = Field(..., ge=1)
    feedback: Dict[str, Any] = Field(default_factory=dict)
    query_used: str = ""
    notes: str = ""


class SaveTranscriptRequest(BaseModel):
    content: str
    filename: str = "SESSION_TRANSCRIPT.md"


class ImportTranscriptRequest(BaseModel):
    source_path: str
    dest_filename: str = ""


class PatchWorldRequest(BaseModel):
    patch: Dict[str, Any]


class SaveFunnelRequest(BaseModel):
    stage: str
    checkpoint_data: Dict[str, Any] = Field(default_factory=dict)


class SaveAuditRecordRequest(BaseModel):
    candidate_psmiles: str
    stage: str
    disposition: str
    detail: str = ""


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

    try:
        bio = resolve_biologic_target(
            req.biologic_target,
            _ROOT,
            fetch_pdb=req.fetch_pdb,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Biologic resolution failed: {exc}")

    run_name = req.run_name or f"{bio.pdb_id or bio.canonical_name}-discovery"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in run_name).strip("-")
    ts = time.strftime("%Y%m%d-%H%M%S")
    session_dir = RUNS_DIR / f"{safe_name}-{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_SESSION] = str(session_dir)

    if bio.pdb_path and bio.fetch_ok:
        os.environ["BIOLOGIX_AI_TARGET_PROTEIN_PDB"] = bio.pdb_path

    try:
        ensure_world_for_session(
            session_dir,
            objective=f"Discover delivery materials for {bio.canonical_name or req.biologic_target}",
        )
        world = load_world(world_path_for_session(session_dir))
        world = apply_patch(
            world,
            {
                "meta": {
                    "links": {
                        "biologic_target": req.biologic_target.strip(),
                        "polymer_target": (req.polymer_target or "").strip(),
                        "biologic_pdb_id": bio.pdb_id,
                        "biologic_pdb_path": bio.pdb_path,
                    }
                }
            },
        )
        save_world(world_path_for_session(session_dir), world)
    except Exception:
        pass

    note = f"Session created at {session_dir}. PDB resolved: {bio.pdb_path or 'not fetched'}."
    return SessionResponse(
        session_dir=str(session_dir),
        biologic_resolution=bio,
        note=note,
    )


# ---------------------------------------------------------------------------
# Static discovery routes (must be registered before /{experiment_id})
# ---------------------------------------------------------------------------

@router.post(
    "/discovery/start",
    summary="Start a materials discovery session (no biologic target required)",
)
def start_discovery_session(req: StartDiscoverySessionRequest) -> Dict[str, Any]:
    session_dir = new_session_dir(_ROOT, name=req.run_name.strip() or None)
    os.environ[ENV_SESSION] = str(session_dir)
    snapshot_note = ""
    instructions_src = _ROOT / ".opencode" / "agent" / "biologics-delivery-discovery.md"
    if instructions_src.is_file():
        try:
            shutil.copy2(str(instructions_src), str(session_dir / "agent_instructions_snapshot.md"))
            snapshot_note = " Agent instructions snapshotted."
        except OSError as exc:
            snapshot_note = f" Warning: could not snapshot agent instructions: {exc}"
    return {
        "session_dir": str(session_dir),
        "experiment_id": session_dir.name,
        "note": "Discovery session created." + snapshot_note,
    }


@router.post(
    "/discovery/autonomous",
    summary="Run autonomous materials discovery loop",
)
def run_autonomous_discovery(req: RunAutonomousDiscoveryRequest) -> Dict[str, Any]:
    session_dir = new_session_dir(
        _ROOT,
        name=(req.run_name.strip() or f"autonomous_{time.strftime('%Y%m%d_%H%M%S')}"),
    )
    log_out = session_dir / "autoresearch_subprocess.log"
    script = _ROOT / "scripts" / "run_autonomous_discovery.py"
    env = os.environ.copy()
    env["BIOLOGIX_AI_ROOT"] = str(_ROOT)
    env[ENV_SESSION] = str(session_dir)

    if req.run_in_background:
        if not script.is_file():
            raise HTTPException(status_code=500, detail=f"Script not found: {script}")
        cmd = [
            sys.executable,
            str(script),
            "--budget-minutes",
            str(req.budget_minutes),
            "--session-dir",
            str(session_dir),
            "--md-steps",
            str(req.md_steps),
            "--max-eval",
            str(req.max_eval_per_iteration),
        ]
        try:
            log_f = open(log_out, "a", encoding="utf-8")
            log_f.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} budget={req.budget_minutes}m ---\n")
            log_f.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=str(_ROOT),
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_f.close()
            return {
                "status": "started_background",
                "pid": proc.pid,
                "session_dir": str(session_dir),
                "experiment_id": session_dir.name,
                "budget_minutes": req.budget_minutes,
                "subprocess_log": str(log_out),
                "results_tsv": str(session_dir / "autoresearch_results.tsv"),
                "summary_json_when_done": str(session_dir / "autoresearch_summary.json"),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    try:
        from biologix_ai.autonomous_discovery import run_autonomous_discovery_loop

        return run_autonomous_discovery_loop(
            budget_minutes=req.budget_minutes,
            session_dir=session_dir,
            root=str(_ROOT),
            md_steps=req.md_steps,
            max_eval_per_iteration=req.max_eval_per_iteration,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/materials-status", summary="Materials discovery system status")
def get_materials_status() -> Dict[str, Any]:
    from biologix_ai.services.literature_service import paper_qa_index_status

    lines = ["Insulin AI Materials Discovery Status"]
    md_status = "unavailable"
    try:
        from biologix_ai.simulation import MDSimulator

        sim = MDSimulator()
        md_status = "insulin + polymer (implicit solvent)" if sim.runner else "unavailable"
    except Exception:
        md_status = "unavailable"
    mutation_status = "unavailable"
    try:
        from biologix_ai.mutation import MaterialMutator  # noqa: F401

        mutation_status = "available (cheminformatics)"
    except ImportError:
        mutation_status = "unavailable (pip install psmiles)"
    pqa = paper_qa_index_status()
    return {
        "md_simulation": md_status,
        "mutation": mutation_status,
        "literature_mining": "Semantic Scholar + agent extraction (no Ollama)",
        "paper_qa": pqa.get("message", "unavailable"),
        "summary_lines": lines + [
            f"MD Simulation: {md_status} (CPU)",
            f"Mutation: {mutation_status}",
            "Literature Mining: Semantic Scholar + agent extraction (no Ollama)",
            f"PaperQA2: {pqa.get('message', 'unavailable')}",
        ],
    }


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


# ---------------------------------------------------------------------------
# Experiment-scoped discovery writes (MCP parity)
# ---------------------------------------------------------------------------

@router.patch(
    "/{experiment_id}/world",
    summary="Merge a JSON patch into discovery_world.json",
)
def patch_world(experiment_id: str, req: PatchWorldRequest) -> Dict[str, Any]:
    session = _session_path(experiment_id)
    wp = world_path_for_session(session)
    try:
        existing = load_world(wp)
        merged = apply_patch(existing, req.patch)
        save_world(wp, merged)
        return {
            "ok": True,
            "world_path": str(wp),
            "session_dir": str(session),
            "meta": merged.get("meta", {}),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/{experiment_id}/planning-context",
    summary="Bounded planning context for discovery prompts",
)
def get_planning_context(experiment_id: str, max_chars: int = 8000) -> Dict[str, Any]:
    session = _session_path(experiment_id)
    wp = world_path_for_session(session)
    n = max(500, min(int(max_chars), 50_000))
    data = load_world(wp)
    ctx = planning_context(data, max_chars=n)
    return {
        "session_dir": str(session),
        "world_path": str(wp),
        "planning_context": ctx,
    }


@router.post(
    "/{experiment_id}/discovery-state",
    summary="Persist discovery iteration state",
)
def save_discovery_state(experiment_id: str, req: SaveDiscoveryStateRequest) -> Dict[str, Any]:
    session = _session_path(experiment_id)
    os.environ[ENV_SESSION] = str(session)
    state = {
        "iteration": req.iteration,
        "timestamp": datetime.now().isoformat(),
        "query_used": req.query_used,
        "notes": req.notes,
        "feedback": req.feedback,
    }
    path = session / f"agent_iteration_{req.iteration}.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    wp = world_path_for_session(session)
    if wp.is_file():
        try:
            world = load_world(wp)
            world = touch_meta_after_iteration(world, req.iteration, path.name)
            save_world(wp, world)
        except OSError:
            pass
    return {"saved": str(path), "session_dir": str(session)}


@router.get(
    "/{experiment_id}/discovery-state",
    summary="Load discovery iteration state",
)
def load_discovery_state(experiment_id: str, iteration: int = 0) -> Dict[str, Any]:
    session = _session_path(experiment_id)
    if iteration > 0:
        path = session / f"agent_iteration_{iteration}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No state file for iteration {iteration}")
    else:
        files = sorted(
            f.name for f in session.iterdir()
            if f.name.startswith("agent_iteration_") and f.name.endswith(".json")
        )
        if not files:
            raise HTTPException(status_code=404, detail="No agent_iteration_*.json in session")
        path = session / files[-1]
    return json.loads(path.read_text(encoding="utf-8"))


@router.post(
    "/{experiment_id}/transcript",
    summary="Save session transcript markdown",
)
def save_transcript(experiment_id: str, req: SaveTranscriptRequest) -> Dict[str, Any]:
    session = _session_path(experiment_id)
    fn = (req.filename or "SESSION_TRANSCRIPT.md").strip()
    if not fn or ".." in fn.replace("\\", "/"):
        raise HTTPException(status_code=422, detail="invalid filename")
    path = session / fn
    path.write_text(req.content, encoding="utf-8")
    return {"saved": str(path), "session_dir": str(session)}


def _allowed_transcript_source(src: Path) -> bool:
    try:
        src = src.resolve()
    except OSError:
        return False
    repo_root = _ROOT.resolve()
    if src == repo_root or repo_root in src.parents:
        return True
    cursor_home = (Path.home() / ".cursor").resolve()
    if not cursor_home.is_dir():
        return False
    try:
        src.relative_to(cursor_home)
    except ValueError:
        return False
    return "agent-transcripts" in src.parts


@router.post(
    "/{experiment_id}/transcript/import",
    summary="Import chat transcript file into session",
)
def import_transcript(experiment_id: str, req: ImportTranscriptRequest) -> Dict[str, Any]:
    src = Path(req.source_path).expanduser()
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"not a file: {src}")
    if not _allowed_transcript_source(src):
        raise HTTPException(
            status_code=403,
            detail="path not allowed (use repo path or ~/.cursor/.../agent-transcripts/)",
        )
    session = _session_path(experiment_id)
    dest = (req.dest_filename or "").strip() or src.name
    if ".." in dest.replace("\\", "/"):
        raise HTTPException(status_code=422, detail="invalid dest_filename")
    out = session / dest
    shutil.copy2(src, out)
    return {"copied_to": str(out), "session_dir": str(session)}


@router.post(
    "/{experiment_id}/funnel",
    summary="Save a funnel context checkpoint",
)
def save_funnel(experiment_id: str, req: SaveFunnelRequest) -> Dict[str, Any]:
    from biologix_ai.services.funnel_context import save_funnel_context

    session = _session_path(experiment_id)
    try:
        path = save_funnel_context(
            stage=req.stage,
            checkpoint_data=req.checkpoint_data,
            session_dir=session,
        )
        return {"saved": True, "stage": req.stage, "path": str(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/{experiment_id}/audit",
    summary="Append a pipeline audit record",
    response_model=Dict[str, Any],
)
def save_audit_record(experiment_id: str, req: SaveAuditRecordRequest) -> Dict[str, Any]:
    from biologix_ai.services.pipeline_audit import save_pipeline_stage

    session = _session_path(experiment_id)
    if req.disposition not in ("pass", "fail", "warning"):
        raise HTTPException(status_code=422, detail="disposition must be pass, fail, or warning")
    record = save_pipeline_stage(
        session_dir=session,
        candidate_psmiles=req.candidate_psmiles,
        stage=req.stage,
        disposition=req.disposition,
        detail=req.detail,
    )
    return {"recorded": True, "audit_id": record["audit_id"], "record": record}
