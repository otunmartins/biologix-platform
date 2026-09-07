import importlib.util
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .completeness import audit, failure_message
from .database import SessionLocal
from .models import Experiment, ExperimentArtifact, ExperimentEvent, ExperimentState
from .reporting import audit_bytes, json_bytes, report_pdf
from .storage import ArtifactStorage

logger = logging.getLogger(__name__)


# Offline cache, not a knowledge base. Claude resolves the repeat unit and the
# structure is verified with RDKit; these entries only answer when the model cannot
# be reached, and anything served from here is labelled offline_cache in the results
# so it is never mistaken for a computed answer.
CACHED_PSMILES = {
    "peg": "[*]OCC[*]",
    "polyethylene glycol": "[*]OCC[*]",
    "ppg": "[*]OC(C)C[*]",
    "polypropylene glycol": "[*]OC(C)C[*]",
    "plga": "[*]OC(=O)COC(=O)C(C)[*]",
    "pla": "[*]OC(=O)C(C)[*]",
    "pva": "[*]CC([*])O",
    "pcl": "[*]OC(=O)CCCCC[*]",
    "chitosan": "[*]OC1C(N)C(O)C(CO)OC1[*]",
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


def _cached_structure(value: str) -> dict | None:
    """Answer from the offline tables when the model is unreachable."""
    known = CACHED_PSMILES.get(value.lower())
    if not known:
        from biologix_ai.material_mappings import name_to_psmiles

        resolved = name_to_psmiles(value)
        known = str(resolved["psmiles"]) if resolved.get("ok") and resolved.get("psmiles") else ""
    if not known:
        return None
    return {
        "psmiles": known,
        "material_name": value,
        "monomers": [],
        "provenance": "offline_cache",
        "confidence": "unverified",
    }


def resolve_polymer_structure(polymer_target: str | None) -> dict:
    """Resolve the polymer target to a repeat unit, and say where the answer came from.

    Claude proposes the structure and RDKit plus the molecular dynamics prescreen decide
    whether it is usable. The offline tables are consulted only when the model cannot be
    reached, and that is recorded rather than hidden.
    """
    value = (polymer_target or "").strip()
    if not value:
        raise ValueError("A polymer target is required for a scientific experiment")

    if "[*]" in value:
        from biologix_ai.llm.chemistry import verify_psmiles

        checks = verify_psmiles(value, value)
        if not checks["ok"]:
            raise ValueError(
                f"The PSMILES repeat unit you entered cannot be used: {'; '.join(checks['problems'])}"
            )
        return {
            "psmiles": value,
            "material_name": value,
            "monomers": [],
            "provenance": "user_supplied",
            "confidence": "high",
            "verification": {
                "validation": checks["validation"],
                "prescreen": checks["prescreen"],
            },
        }

    from biologix_ai.llm.chemistry import LLMUnavailable, resolve_polymer

    try:
        resolved = resolve_polymer(value)
        return {
            "psmiles": resolved["psmiles"],
            "material_name": resolved["material_name"],
            "monomers": resolved["monomers"],
            "polymerization_type": resolved["polymerization_type"],
            "provenance": "llm_verified",
            "confidence": resolved["confidence"],
            "notes": resolved["notes"],
            "model": resolved["model"],
            "rounds": resolved["rounds"],
            "verification": resolved["verification"],
        }
    except LLMUnavailable as exc:
        cached = _cached_structure(value)
        if cached:
            cached["fallback_reason"] = str(exc)
            return cached
        raise ValueError(
            f"Could not resolve polymer target {value!r}: {exc}. No cached structure is "
            "available either, so enter a PSMILES repeat unit with two [*] connection points."
        ) from exc
    except ValueError as exc:
        cached = _cached_structure(value)
        if cached:
            cached["fallback_reason"] = str(exc)
            return cached
        raise


def resolve_psmiles(polymer_target: str | None) -> str:
    return resolve_polymer_structure(polymer_target)["psmiles"]


def _routes_with_steps(result) -> list:
    return [route for route in result.polymer_routes if route.steps]


def _apply_mechanisms(result, mechanism_by_source: dict) -> None:
    """Label each route with the mechanism the source it came from reported.

    The knowledge graph carries reaction text, not mechanism, so every route it builds
    is typed 'unknown'. The evidence knows which polymerisation each route is.
    """
    from biologix_ai.retrosynthesis.models import PolymerizationType

    if not mechanism_by_source:
        return
    for route in result.polymer_routes:
        if route.polymerization_type not in (None, PolymerizationType.UNKNOWN):
            continue
        for step in route.steps:
            source = (step.literature_source or "").strip()
            mechanism = mechanism_by_source.get(source)
            if not mechanism:
                continue
            try:
                route.polymerization_type = PolymerizationType(mechanism)
            except ValueError:
                continue
            break


def _tree_feedback(result, evidence: dict) -> str:
    """Turn a failed graph build into the next instruction for the model."""
    lines = [
        "The knowledge graph was built from your evidence but no complete route to the "
        "target survived: every branch has to reach a purchasable chemical."
    ]
    unresolved = evidence.get("unresolved_reactants") or []
    if unresolved:
        lines.append(
            "These reactants never resolved to anything purchasable: " + ", ".join(unresolved)
        )
    for warning in list(result.warnings or [])[:3]:
        lines.append(str(warning))
    for error in list(result.errors or [])[:3]:
        lines.append(str(error))
    return "\n".join(f"- {line}" for line in lines)


def _retro_material_name(experiment: Experiment, structure: dict) -> str:
    """The name the graph is rooted on: an explicit override, else the resolved name."""
    parameters = experiment.parameters or {}
    override = str(parameters.get("retrosynthesis_material_name") or "").strip()
    if override and "[*]" not in override:
        return override
    target = str(experiment.polymer_target or "").strip()
    if target and "[*]" not in target:
        return target
    name = str(structure.get("material_name") or "").strip()
    if name and "[*]" not in name:
        return name

    from biologix_ai.llm.chemistry import name_polymer

    return name_polymer(structure["psmiles"])["material_name"]


def _max_retro_attempts() -> int:
    try:
        return max(1, int(os.getenv("BIOLOGIX_RETRO_PLAN_ATTEMPTS", "2")))
    except ValueError:
        return 2


def run_retrosynthesis(experiment: Experiment, structure: dict, capabilities: dict) -> dict:
    if not enabled("SCIENTIFIC_RETROSYNTHESIS_ENABLED"):
        return {"status": "disabled"}
    from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
    from biologix_ai.retrosynthesis.retro_adapter import normalize_extractions, write_llm_res
    from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace
    from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis

    parameters = experiment.parameters or {}
    psmiles = structure["psmiles"]
    material_name = _retro_material_name(experiment, structure)
    session_root = os.getenv("BIOLOGIX_PLATFORM_RUNS_DIR", "/app/runs/platform")
    session_dir = Path(session_root) / str(experiment.id)
    session_dir.mkdir(parents=True, exist_ok=True)

    # AiZynthFinder is optional enrichment for unresolved leaf monomers. Known,
    # purchasable monomers should not each trigger a several-minute USPTO tree
    # search unless the experiment explicitly asks.
    enrich_small_molecules = bool(parameters.get("enrich_monomers_with_aizynth", False))

    def build_request(allow_template: bool) -> RetrosynthesisRequest:
        return RetrosynthesisRequest(
            target=material_name,
            biologic_target=experiment.biologic_target,
            constraints=RetrosynthesisConstraints(
                max_routes=3,
                enrich_monomers_with_aizynth=enrich_small_molecules,
                allow_curated_template=allow_template,
            ),
            session_dir=str(session_dir),
        )

    planning: dict = {"material_name": material_name}
    supplied = parameters.get("retrosynthesis_extractions")

    if supplied:
        write_llm_res(
            session_dir,
            material_name,
            normalize_extractions(supplied),
            target_psmiles=psmiles,
        )
        result = plan_retrosynthesis(build_request(False))
        evidence_source = "user_supplied"
    else:
        # Claude proposes the literature chemistry, the graph decides whether it holds
        # up, and what the graph rejects is handed straight back to the model.
        from biologix_ai.llm.client import LLMUnavailable
        from biologix_ai.llm.retro_planner import plan_evidence

        workspace = ensure_workspace(session_dir, material_name)["workspace"]
        result = None
        evidence_source = None
        attempts: list = []
        feedback = ""
        try:
            for attempt in range(_max_retro_attempts()):
                evidence = plan_evidence(
                    material_name,
                    psmiles,
                    workspace=Path(workspace),
                    extra_feedback=feedback,
                )
                write_llm_res(
                    session_dir,
                    material_name,
                    evidence["extractions"],
                    target_psmiles=psmiles,
                )
                result = plan_retrosynthesis(build_request(False))
                attempts.append({
                    "attempt": attempt + 1,
                    "sources": len(evidence["sources"]),
                    "rounds": evidence["rounds"],
                    "unresolved_reactants": evidence["unresolved_reactants"],
                    "routes_with_steps": len(_routes_with_steps(result)),
                })
                if _routes_with_steps(result):
                    _apply_mechanisms(result, evidence.get("mechanism_by_source") or {})
                    evidence_source = "llm_literature_evidence"
                    planning["model"] = evidence["model"]
                    planning["sources"] = evidence["sources"]
                    planning["diagnostics"] = evidence["diagnostics"]
                    break
                feedback = _tree_feedback(result, evidence)
        except (LLMUnavailable, ValueError) as exc:
            planning["error"] = str(exc)
            logger.warning("Claude route planning failed for %r: %s", material_name, exc)
        finally:
            # Recorded even when planning raised part-way, so a failed run still shows
            # what was tried.
            planning["attempts"] = attempts

        if not (result and _routes_with_steps(result)):
            # Nothing evidence-backed survived this run. Fall back to the offline curated
            # table, which the completeness audit treats as provisional rather than a
            # result. Evidence left in the workspace by an earlier attempt still wins.
            result = plan_retrosynthesis(build_request(True))
            provenance = result.metadata.get("route_provenance")
            if provenance == "curated_template":
                evidence_source = "offline_curated_route"
            elif _routes_with_steps(result) and not evidence_source:
                evidence_source = "workspace_evidence"

    payload = result.model_dump(mode="json")
    if result.errors:
        status = "failed"
    elif result.metadata.get("route_provenance") == "curated_template":
        status = "provisional"
    elif result.metadata.get("requires_agent_extractions"):
        status = "requires_input"
    elif not result.polymer_routes:
        status = "no_routes"
    else:
        status = "completed"
    planning["evidence_source"] = evidence_source
    return {"status": status, "evidence_source": evidence_source, "planning": planning, "result": payload}


# Fixed by the matrix simulator: 2 fs Langevin timestep, interaction energy sampled
# every 250 steps. Used to work out whether a trajectory finished or was cut short.
_NPT_TIMESTEP_PS = 0.002
_NPT_REPORT_INTERVAL_STEPS = 250


def _physics_sampling(parameters: dict) -> dict:
    """Trajectory settings for the interaction-energy estimate.

    SCIENTIFIC_OPENMM_STEPS never controlled a trajectory: it is forwarded to
    LocalEnergyMinimizer.maxIterations, so a run with it set to 100 or to 5000 both
    reported an interaction energy read off a single minimised pose, with no sampling
    and no error bar. Constant-pressure sampling is what produces an energy that can
    be averaged, so the platform turns it on and reports how long it ran.
    """
    # 200 ps is where this system converges, measured on PEG/insulin: the interaction
    # energy drifts +13 kJ/mol across the production window against a 41 kJ/mol spread,
    # while 20 ps and 100 ps were both still falling (-814, then -1306, then -1433).
    duration_ps = float(parameters.get("md_ps", os.getenv("SCIENTIFIC_OPENMM_NPT_PS", "200")))
    # Generous enough that slower hardware finishes the trajectory instead of silently
    # truncating it; the run reports how many frames it actually got either way.
    wall_clock_s = float(os.getenv("SCIENTIFIC_OPENMM_WALL_CLOCK_S", "3600"))
    minimize_iterations = int(
        parameters.get("minimize_iterations", os.getenv("SCIENTIFIC_OPENMM_MINIMIZE_ITERATIONS", "2000"))
    )
    return {
        "npt_enabled": enabled("SCIENTIFIC_OPENMM_NPT", True),
        "duration_ps": duration_ps,
        "wall_clock_limit_s": wall_clock_s,
        "minimize_iterations": minimize_iterations,
        "candidate_timeout_s": float(
            os.getenv("BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S", str(wall_clock_s + 600))
        ),
    }


def run_physics(experiment: Experiment, psmiles: str, biologic: dict, capabilities: dict) -> dict:
    if not enabled("SCIENTIFIC_OPENMM_ENABLED"):
        return {"status": "disabled"}
    if not capabilities["openmm"] or not capabilities["packmol"]:
        return {"status": "unavailable", "reason": "OpenMM and Packmol are required"}
    from biologix_ai.services.physics_service import run_simulation

    session_root = os.getenv("BIOLOGIX_PLATFORM_RUNS_DIR", "/app/runs/platform")
    sampling = _physics_sampling(experiment.parameters or {})
    overrides = {
        "BIOLOGIX_AI_SESSION_DIR": os.path.join(session_root, str(experiment.id)),
        "BIOLOGIX_AI_OPENMM_MATRIX_NPT": "true" if sampling["npt_enabled"] else "false",
        "BIOLOGIX_AI_OPENMM_MATRIX_NPT_PS": str(sampling["duration_ps"]),
        "BIOLOGIX_AI_OPENMM_MATRIX_WALL_CLOCK_S": str(sampling["wall_clock_limit_s"]),
        "BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S": str(sampling["candidate_timeout_s"]),
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        result = run_simulation(
            [psmiles],
            biologic_target=biologic.get("pdb_id") or experiment.biologic_target,
            temperature_k=float(experiment.parameters.get("temperature_k", 310.0)),
            n_steps=sampling["minimize_iterations"],
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    evaluation = result.get("results")
    if isinstance(evaluation, dict):
        progress = evaluation.get("evaluation_progress") or []
        evaluation_failed = any(item.get("status") != "completed" for item in progress)
        no_success = not (evaluation.get("successful_materials") or evaluation.get("md_results_raw"))
    elif isinstance(evaluation, list):
        evaluation_failed = any(not item.get("ok", False) for item in evaluation)
        no_success = not evaluation
    else:
        evaluation_failed = True
        no_success = True
    status = "failed" if result.get("errors") or evaluation_failed or no_success else "completed"

    # The simulator reports how many frames it averaged but not whether it finished, so
    # compare against the trajectory that was asked for.
    records = evaluation.get("md_results_raw") if isinstance(evaluation, dict) else evaluation
    frames = [
        record.get("n_frames_averaged")
        for record in (records or [])
        if isinstance(record, dict) and isinstance(record.get("n_frames_averaged"), int)
    ]
    expected_frames = int(sampling["duration_ps"] / _NPT_TIMESTEP_PS / _NPT_REPORT_INTERVAL_STEPS)
    sampling["expected_frames"] = expected_frames
    sampling["frames_averaged"] = min(frames) if frames else 0
    sampling["truncated_by_wall_clock"] = bool(
        sampling["npt_enabled"] and frames and min(frames) < expected_frames
    )
    return {
        "status": status,
        "sampling": sampling,
        "platform": capabilities.get("openmm_platform"),
        "result": result,
    }


def execute_pipeline(experiment: Experiment, db: Session) -> dict:
    from biologix_ai.services.biologic_resolver import resolve_pdb_entry
    from biologix_ai.services.compliance_service import check_excipient_compliance
    from biologix_ai.services.psmiles_service import validate_psmiles
    from biologix_ai.services.toxicity_service import screen_monomer

    capabilities = scientific_capabilities()
    record_progress(db, experiment, 5, "capabilities", "Inspecting scientific runtime capabilities")
    record_progress(db, experiment, 12, "target_resolution", "Resolving the biologic target")
    biologic = resolve_pdb_entry(experiment.biologic_target)
    pdb_id = biologic["pdb_id"]

    structure = resolve_polymer_structure(experiment.polymer_target)
    psmiles = structure["psmiles"]
    record_progress(
        db,
        experiment,
        18,
        "structure_resolution",
        f"Resolved the repeat unit ({structure['provenance'].replace('_', ' ')})",
    )
    record_progress(db, experiment, 24, "structure_validation", "Validating the polymer structure")
    validation = validate_psmiles(
        psmiles=psmiles,
        material_name=structure.get("material_name") or experiment.polymer_target or "",
        crosscheck_web=False,
    )
    if isinstance(validation, str):
        validation = json.loads(validation)

    record_progress(db, experiment, 38, "safety_screen", "Running structural and ADMET safety screening")
    # This is a repeat-unit structural alert screen. Route monomers are screened
    # separately below once retrosynthesis identifies the actual residuals.
    toxicity = screen_monomer(psmiles.replace("[*]", "C"))

    record_progress(db, experiment, 50, "compliance", "Checking regulatory precedent and formulation alerts")
    compliance = check_excipient_compliance(psmiles).to_dict()

    record_progress(db, experiment, 62, "retrosynthesis", "Planning available retrosynthesis routes")
    try:
        retrosynthesis = run_retrosynthesis(experiment, structure, capabilities)
    except Exception as exc:
        retrosynthesis = {"status": "failed", "reason": str(exc)}

    monomer_safety = []
    for route in (retrosynthesis.get("result") or {}).get("polymer_routes", []):
        for monomer in route.get("monomers", []):
            smiles = monomer.get("smiles")
            if smiles and "[*]" not in smiles:
                screened = screen_monomer(smiles).model_dump(mode="json")
                screened["name"] = monomer.get("name")
                monomer_safety.append(screened)

    record_progress(db, experiment, 78, "openmm", "Running molecular physics when the runtime supports it")
    try:
        physics = run_physics(experiment, psmiles, biologic, capabilities)
    except Exception as exc:
        physics = {"status": "failed", "reason": str(exc)}

    results = {
        # summary is built before the audit so target resolution can be judged
        "summary": {
            "biologic_target": experiment.biologic_target,
            "pdb_id": pdb_id or None,
            "biologic_provenance": biologic["provenance"],
            "polymer_target": experiment.polymer_target,
            "psmiles": psmiles,
            "material_name": structure.get("material_name"),
            "structure_provenance": structure["provenance"],
        },
        "structure": structure,
        "capabilities": capabilities,
        "biologic": biologic,
        "validation": validation,
        "safety": toxicity.model_dump(mode="json"),
        "monomer_safety": monomer_safety,
        "compliance": compliance,
        "retrosynthesis": retrosynthesis,
        "physics": physics,
    }
    record_progress(db, experiment, 88, "completeness", "Auditing whether every scientific stage produced a result")
    completeness = audit(results)
    # Residual monomers end up in the formulation, so a mutagenicity or structural
    # alert on one of them has to count against the candidate the same way an alert
    # on the repeat unit does.
    monomers_safe = all(item.get("safe", False) for item in monomer_safety)
    safe = bool(validation.get("valid", False)) and toxicity.safe and monomers_safe
    disposition = (
        "recommended"
        if safe and compliance["overall_status"] == "approved" and completeness["verdict"] == "complete"
        else "review"
    )
    return {
        **results,
        "completeness": completeness,
        "summary": {
            **results["summary"],
            "disposition": disposition,
            "completeness": completeness["verdict"],
        },
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
            report = results.get("completeness") or {}
            if report.get("verdict") == "incomplete":
                experiment.status = ExperimentState.failed
                experiment.error_message = failure_message(report)
                experiment.completed_at = now()
                record_progress(
                    db,
                    experiment,
                    experiment.progress,
                    "failed",
                    experiment.error_message,
                )
                return results
            experiment.status = ExperimentState.done
            experiment.completed_at = now()
            record_progress(db, experiment, 100, "complete", "Scientific workflow completed")
            return results
        except Exception as exc:
            db.rollback()
            experiment = db.get(Experiment, uuid.UUID(experiment_id)) or experiment
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
