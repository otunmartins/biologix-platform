import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Experiment, ExperimentState


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


def record_progress(db: Session, experiment: Experiment, percent: int, stage: str, detail: str):
    entries = list(experiment.progress_log or [])
    entries.append({"timestamp": now().isoformat(), "stage": stage, "detail": detail, "progress": percent})
    experiment.progress = percent
    experiment.current_stage = stage
    experiment.progress_log = entries
    db.commit()


def resolve_psmiles(polymer_target: str | None) -> str:
    value = (polymer_target or "").strip()
    if "[*]" in value:
        return value
    return POLYMER_PSMILES.get(value.lower(), "[*]OCC[*]")


def execute_pipeline(experiment: Experiment, db: Session) -> dict:
    from biologix_ai.services.biologic_resolver import lookup_pdb_id
    from biologix_ai.services.compliance_service import check_excipient_compliance
    from biologix_ai.services.psmiles_service import validate_psmiles
    from biologix_ai.services.toxicity_service import screen_monomer

    record_progress(db, experiment, 10, "target_resolution", "Resolving the biologic target")
    pdb_id = lookup_pdb_id(experiment.biologic_target)

    psmiles = resolve_psmiles(experiment.polymer_target)
    record_progress(db, experiment, 30, "structure_validation", "Validating the polymer structure")
    validation = validate_psmiles(psmiles=psmiles, material_name=experiment.polymer_target or "", crosscheck_web=False)
    if isinstance(validation, str):
        validation = json.loads(validation)

    record_progress(db, experiment, 55, "safety_screen", "Screening the representative repeat unit for structural alerts")
    monomer_smiles = psmiles.replace("[*]", "C")
    toxicity = screen_monomer(monomer_smiles)

    record_progress(db, experiment, 75, "compliance", "Checking regulatory precedent and formulation alerts")
    compliance = check_excipient_compliance(psmiles).to_dict()

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
        "validation": validation,
        "safety": toxicity.model_dump(mode="json"),
        "compliance": compliance,
    }


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
            experiment.status = ExperimentState.done
            experiment.progress = 100
            experiment.current_stage = "complete"
            experiment.completed_at = now()
            record_progress(db, experiment, 100, "complete", "Scientific screening completed")
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
