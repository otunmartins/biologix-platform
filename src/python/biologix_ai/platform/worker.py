import importlib.util
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Experiment, ExperimentArtifact, ExperimentEvent, ExperimentState
from .reporting import audit_bytes, json_bytes, report_pdf
from .storage import ArtifactStorage


POLYMER_PSMILES = {
    "peg": "[*]OCC[*]",
    "polyethylene glycol": "[*]OCC[*]",
    "ppg": "[*]OC(C)C[*]",
    "polypropylene glycol": "[*]OC(C)C[*]",
    "plga": "[*]OC(=O)C(C)OC(=O)C[*]",
    "pla": "[*]OC(=O)C(C)[*]",
    "pva": "[*]CC(O)[*]",
    "pcl": "[*]OC(=O)CCCCC(=O)O[*]",
}


def now():
    return datetime.now(timezone.utc)


def enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def scientific_capabilities() -> dict:
    capabilities = {
        "rdkit": module_available("rdkit"),
        "admet_ai": module_available("admet_ai"),
        "retrosynthesis_agent": module_available("RetroSynAgent"),
        "aizynthfinder": module_available("aizynthfinder"),
        "openmm": module_available("openmm"),
        "packmol": shutil.which("packmol") is not None,
        "openmm_platform": None,
    }
    if capabilities["openmm"]:
        try:
            import openmm

            names = [openmm.Platform.getPlatform(i).getName() for i in range(openmm.Platform.getNumPlatforms())]
            requested = os.getenv("BIOLOGIX_AI_OPENMM_PLATFORM", "auto").upper()
            if requested != "AUTO" and requested in names:
                capabilities["openmm_platform"] = requested
            else:
                capabilities["openmm_platform"] = next((name for name in ("CUDA", "OpenCL", "CPU", "Reference") if name in names), None)
            capabilities["openmm_platforms"] = names
        except Exception as exc:
            capabilities["openmm_error"] = str(exc)
    return capabilities


def record_progress(db: Session, experiment: Experiment, percent: int, stage: str, detail: str):
    timestamp = now()
    entries = list(experiment.progress_log or [])
    entries.append({"timestamp": timestamp.isoformat(), "stage": stage, "detail": detail, "progress": percent})
    sequence = db.scalar(select(func.coalesce(func.max(ExperimentEvent.sequence), 0)).where(ExperimentEvent.experiment_id == experiment.id)) + 1
    db.add(ExperimentEvent(
        experiment_id=experiment.id,
        sequence=sequence,
        stage=stage,
        detail=detail,
        progress=percent,
        created_at=timestamp,
    ))
    experiment.progress = percent
    experiment.current_stage = stage
    experiment.progress_log = entries
    db.commit()


def resolve_psmiles(polymer_target: str | None) -> str:
    value = (polymer_target or "").strip()
    if "[*]" in value:
        return value
    return POLYMER_PSMILES.get(value.lower(), "[*]OCC[*]")


def run_retrosynthesis(experiment: Experiment, capabilities: dict) -> dict:
    if not enabled("SCIENTIFIC_RETROSYNTHESIS_ENABLED"):
        return {"status": "disabled"}
    if not capabilities["retrosynthesis_agent"]:
        return {"status": "unavailable", "reason": "RetroSynthesisAgent is not installed"}
    from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
    from biologix_ai.retrosynthesis.retro_adapter import normalize_extractions, write_llm_res
    from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis

    parameters = experiment.parameters or {}
    material_name = str(
        parameters.get("retrosynthesis_material_name")
        or experiment.polymer_target
        or ""
    ).strip()
    session_root = os.getenv("BIOLOGIX_PLATFORM_RUNS_DIR", "/app/runs/platform")
    session_dir = os.path.join(session_root, str(experiment.id))
    os.makedirs(session_dir, exist_ok=True)

    extractions = parameters.get("retrosynthesis_extractions")
    if extractions:
        normalized = normalize_extractions(extractions)
        write_llm_res(
            Path(session_dir),
            material_name,
            normalized,
            target_psmiles=resolve_psmiles(experiment.polymer_target),
        )

    request = RetrosynthesisRequest(
        target=material_name or resolve_psmiles(experiment.polymer_target),
        biologic_target=experiment.biologic_target,
        constraints=RetrosynthesisConstraints(max_routes=3),
        session_dir=session_dir,
    )
    result = plan_retrosynthesis(request)
    payload = result.model_dump(mode="json")
    if result.errors:
        status = "failed"
    elif result.metadata.get("requires_agent_extractions"):
        status = "requires_input"
    elif not result.polymer_routes:
        status = "no_routes"
    else:
        status = "completed"
    return {"status": status, "result": payload}


def run_physics(experiment: Experiment, psmiles: str, capabilities: dict) -> dict:
    if not enabled("SCIENTIFIC_OPENMM_ENABLED"):
        return {"status": "disabled"}
    if not capabilities["openmm"] or not capabilities["packmol"]:
        return {"status": "unavailable", "reason": "OpenMM and Packmol are required"}
    from biologix_ai.services.physics_service import run_simulation

    result = run_simulation(
        [psmiles],
        biologic_target=experiment.biologic_target,
        temperature_k=float(experiment.parameters.get("temperature_k", 310.0)),
        n_steps=int(experiment.parameters.get("md_steps", os.getenv("SCIENTIFIC_OPENMM_STEPS", "100"))),
    )
    return {"status": "completed" if not result.get("errors") else "failed", "result": result}


def execute_pipeline(experiment: Experiment, db: Session) -> dict:
    from biologix_ai.services.biologic_resolver import lookup_pdb_id
    from biologix_ai.services.compliance_service import check_excipient_compliance
    from biologix_ai.services.psmiles_service import validate_psmiles
    from biologix_ai.services.toxicity_service import screen_monomer

    capabilities = scientific_capabilities()
    record_progress(db, experiment, 5, "capabilities", "Inspecting scientific runtime capabilities")
    record_progress(db, experiment, 12, "target_resolution", "Resolving the biologic target")
    pdb_id = lookup_pdb_id(experiment.biologic_target)

    psmiles = resolve_psmiles(experiment.polymer_target)
    record_progress(db, experiment, 24, "structure_validation", "Validating the polymer structure")
    validation = validate_psmiles(psmiles=psmiles, material_name=experiment.polymer_target or "", crosscheck_web=False)
    if isinstance(validation, str):
        validation = json.loads(validation)

    record_progress(db, experiment, 38, "safety_screen", "Running structural and ADMET safety screening")
    toxicity = screen_monomer(psmiles.replace("[*]", "C"))

    record_progress(db, experiment, 50, "compliance", "Checking regulatory precedent and formulation alerts")
    compliance = check_excipient_compliance(psmiles).to_dict()

    record_progress(db, experiment, 62, "retrosynthesis", "Planning available retrosynthesis routes")
    try:
        retrosynthesis = run_retrosynthesis(experiment, capabilities)
    except Exception as exc:
        retrosynthesis = {"status": "failed", "reason": str(exc)}

    record_progress(db, experiment, 78, "openmm", "Running molecular physics when the runtime supports it")
    try:
        physics = run_physics(experiment, psmiles, capabilities)
    except Exception as exc:
        physics = {"status": "failed", "reason": str(exc)}

    safe = bool(validation.get("valid", False)) and toxicity.safe
    disposition = "recommended" if safe and compliance["overall_status"] == "approved" else "review"
    return {
        "summary": {
            "disposition": disposition,
            "biologic_target": experiment.biologic_target,
            "pdb_id": pdb_id or None,
            "polymer_target": experiment.polymer_target,
            "psmiles": psmiles,
        },
        "capabilities": capabilities,
        "validation": validation,
        "safety": toxicity.model_dump(mode="json"),
        "compliance": compliance,
        "retrosynthesis": retrosynthesis,
        "physics": physics,
    }


def save_artifact(db: Session, experiment: Experiment, kind: str, filename: str, content_type: str, content: bytes):
    key = f"experiments/{experiment.owner_id}/{experiment.id}/{uuid.uuid4().hex}/{filename}"
    stored = ArtifactStorage().put(key, content, content_type)
    db.add(ExperimentArtifact(
        experiment_id=experiment.id,
        kind=kind,
        filename=filename,
        object_key=stored.key,
        content_type=content_type,
        size_bytes=stored.size,
        sha256=stored.sha256,
    ))
    db.commit()


def create_artifacts(db: Session, experiment: Experiment, results: dict):
    events = db.scalars(select(ExperimentEvent).where(ExperimentEvent.experiment_id == experiment.id).order_by(ExperimentEvent.sequence)).all()
    audit = [{
        "sequence": event.sequence,
        "timestamp": event.created_at.isoformat(),
        "stage": event.stage,
        "detail": event.detail,
        "progress": event.progress,
    } for event in events]
    save_artifact(db, experiment, "results", "results.json", "application/json", json_bytes(results))
    save_artifact(db, experiment, "audit", "audit.jsonl", "application/x-ndjson", audit_bytes(audit))
    save_artifact(db, experiment, "report", "report.pdf", "application/pdf", report_pdf(experiment.name, results))


def run_experiment(experiment_id: str):
    with SessionLocal() as db:
        experiment = db.get(Experiment, uuid.UUID(experiment_id))
        if not experiment:
            return {"error": "Experiment not found"}
        experiment.status = ExperimentState.running
        experiment.started_at = now()
        experiment.error_message = None
        db.commit()
        try:
            results = execute_pipeline(experiment, db)
            experiment.results = results
            record_progress(db, experiment, 92, "artifacts", "Generating report, results, and audit artifacts")
            try:
                create_artifacts(db, experiment, results)
            except Exception as exc:
                results["artifact_error"] = str(exc)
                experiment.results = results
                db.commit()
            experiment.status = ExperimentState.done
            experiment.completed_at = now()
            record_progress(db, experiment, 100, "complete", "Scientific workflow completed")
            return results
        except Exception as exc:
            experiment.status = ExperimentState.failed
            experiment.error_message = str(exc)
            experiment.completed_at = now()
            record_progress(db, experiment, experiment.progress, "failed", "The workflow could not complete")
            raise


def mark_job_failed(job, connection, exception_type, exception_value, traceback):
    experiment_id = job.args[0] if job.args else None
    if not experiment_id:
        return
    with SessionLocal() as db:
        experiment = db.get(Experiment, uuid.UUID(str(experiment_id)))
        if not experiment or experiment.status == ExperimentState.done:
            return
        experiment.status = ExperimentState.failed
        experiment.error_message = str(exception_value or "Worker process terminated unexpectedly")
        experiment.completed_at = now()
        record_progress(db, experiment, experiment.progress, "failed", "The worker could not complete the workflow")
