"""Biologics-specific router (MCP biologics tools parity)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix_ai.http_api.deps import _ROOT, session_path
from biologix_ai.run_paths import ENV_SESSION, new_session_dir

router = APIRouter(prefix="/api/biologics", tags=["Biologics"])


class ResolveBiologicRequest(BaseModel):
    name_or_pdb_id: str
    fetch_pdb: bool = True
    experiment_id: str = ""


class RunBiologicsDiscoveryRequest(BaseModel):
    biologic_target: str
    polymer_target: str = ""
    budget_minutes: float = Field(default=60.0, ge=1.0)
    run_in_background: bool = True
    run_name: str = ""
    max_routes: int = Field(default=5, ge=1, le=20)
    run_admet: bool = True
    run_openmm: bool = False


@router.post("/resolve", summary="Resolve biologic name or PDB ID to local structure")
def resolve_biologic(req: ResolveBiologicRequest) -> Dict[str, Any]:
    from biologix_ai.services.biologic_resolver import resolve_biologic_target

    session = session_path(req.experiment_id) if req.experiment_id else None
    try:
        bio = resolve_biologic_target(
            req.name_or_pdb_id,
            _ROOT,
            session_dir=session,
            fetch_pdb=req.fetch_pdb,
        )
        return bio.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/discovery", summary="Run scripted biologics retrosynthesis discovery loop")
def run_biologics_discovery(req: RunBiologicsDiscoveryRequest) -> Dict[str, Any]:
    session_dir = new_session_dir(
        _ROOT,
        name=(req.run_name.strip() or f"biologics_{time.strftime('%Y%m%d_%H%M%S')}"),
    )
    log_out = session_dir / "biologics_discovery_subprocess.log"
    script = _ROOT / "scripts" / "run_biologics_discovery.py"
    env = os.environ.copy()
    env["BIOLOGIX_AI_ROOT"] = str(_ROOT)
    env[ENV_SESSION] = str(session_dir)

    if req.run_in_background:
        if not script.is_file():
            raise HTTPException(status_code=500, detail=f"Script not found: {script}")
        cmd = [
            sys.executable,
            str(script),
            "--biologic-target",
            req.biologic_target,
            "--budget-minutes",
            str(req.budget_minutes),
            "--session-dir",
            str(session_dir),
            "--max-routes",
            str(req.max_routes),
        ]
        if req.polymer_target.strip():
            cmd.extend(["--polymer-target", req.polymer_target.strip()])
        if not req.run_admet:
            cmd.append("--no-admet")
        if req.run_openmm:
            cmd.append("--openmm")
        try:
            log_f = open(log_out, "a", encoding="utf-8")
            log_f.write(f"\n--- start biologics {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
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
                "summary_json_when_done": str(session_dir / "biologics_discovery_summary.json"),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    try:
        from biologix_ai.autonomous_biologics import run_biologics_discovery_loop

        summary = run_biologics_discovery_loop(
            biologic_target=req.biologic_target,
            polymer_target=req.polymer_target,
            session_dir=session_dir,
            root=str(_ROOT),
            budget_minutes=req.budget_minutes,
            max_routes=req.max_routes,
            run_admet=req.run_admet,
            run_openmm=req.run_openmm,
        )
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
