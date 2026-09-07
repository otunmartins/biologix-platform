"""Decide whether a pipeline run actually produced the science it claims.

Individual stages report their own status, but a stage can report success while
returning nothing usable: a retrosynthesis route with no steps, a physics run
with no interaction energy, an ADMET screen that never loaded its model. This
module inspects the payloads themselves and refuses to call those complete.

Findings (unsafe, flagged, non-purchasable) are NOT incompleteness. A stage that
ran and returned a negative answer is complete.
"""

import os

COMPLETE = "complete"
DEGRADED = "degraded"
SKIPPED = "skipped"
FAILED = "failed"

# Anything that did not actually do its work blocks, degraded included. A stage
# that reports "skipped", "could not be screened" or "structural screen only" has
# not produced the result the pipeline claims, so the run is not a success.
BLOCKING = (FAILED, SKIPPED, DEGRADED)


def _minimum_frames() -> int:
    """Frames the interaction energy has to be averaged over to carry an error bar."""
    try:
        return int(os.getenv("SCIENTIFIC_OPENMM_MIN_FRAMES", "10"))
    except ValueError:
        return 10


def _verdict(stage: str, status: str, reasons: list[str]) -> dict:
    return {"stage": stage, "status": status, "reasons": reasons}


def _check_target(results: dict) -> dict:
    biologic = results.get("biologic") or {}
    pdb_id = (results.get("summary") or {}).get("pdb_id")
    if not pdb_id:
        reason = biologic.get("fallback_reason")
        return _verdict("target_resolution", DEGRADED, [
            "The biologic target did not resolve to a PDB entry"
            + (f": {reason}" if reason else "")
        ])
    provenance = biologic.get("provenance")
    if provenance == "offline_cache":
        return _verdict("target_resolution", DEGRADED, [
            f"Structure {pdb_id} came from the offline name table, not from a checked lookup: "
            + str(biologic.get("fallback_reason") or "reason not recorded")
        ])
    if provenance == "rcsb_text_search":
        return _verdict("target_resolution", DEGRADED, [
            f"Structure {pdb_id} is the top text-search hit and was not confirmed to be this "
            "biologic: " + str(biologic.get("fallback_reason") or "reason not recorded")
        ])
    return _verdict("target_resolution", COMPLETE, [])


def _check_structure_resolution(results: dict) -> dict:
    """The repeat unit every later stage is computed from has to be a checked answer."""
    structure = results.get("structure") or {}
    provenance = structure.get("provenance")
    if not provenance:
        return _verdict("structure_resolution", SKIPPED, ["The polymer structure was never resolved"])
    if provenance == "offline_cache":
        return _verdict("structure_resolution", DEGRADED, [
            "The repeat unit came from the offline table rather than being resolved and "
            "verified: " + str(structure.get("fallback_reason") or "reason not recorded")
        ])
    reasons = []
    if structure.get("confidence") == "low":
        reasons.append(
            "Low confidence in the repeat unit: " + str(structure.get("notes") or "no detail given")
        )
    return _verdict("structure_resolution", DEGRADED if reasons else COMPLETE, reasons)


def _check_validation(results: dict) -> dict:
    validation = results.get("validation") or {}
    if not validation:
        return _verdict("structure_validation", SKIPPED, ["The structure was never validated"])
    if not validation.get("valid"):
        reason = validation.get("error") or "The polymer structure did not validate"
        return _verdict("structure_validation", FAILED, [str(reason)])
    reasons = []
    warning = (validation.get("pubchem_lookup") or {}).get("warning")
    if warning:
        reasons.append(str(warning))
    consistency = validation.get("name_consistency") or {}
    if consistency and not consistency.get("consistent", True):
        missing = ", ".join(consistency.get("missing") or []) or "expected groups absent"
        reasons.append(f"Structure does not match the material name: {missing}")
    return _verdict("structure_validation", DEGRADED if reasons else COMPLETE, reasons)


def _check_safety(results: dict) -> dict:
    safety = results.get("safety") or {}
    if not safety:
        return _verdict("safety_screen", SKIPPED, ["The safety screen did not run"])
    admet = safety.get("admet")
    if admet is None:
        return _verdict("safety_screen", DEGRADED, ["No ADMET profile was produced; only structural alerts were screened"])
    if not admet.get("available", False):
        return _verdict("safety_screen", DEGRADED, ["The ADMET model was unavailable; only structural alerts were screened"])
    return _verdict("safety_screen", COMPLETE, [])


def _check_compliance(results: dict) -> dict:
    compliance = results.get("compliance") or {}
    if not compliance.get("overall_status"):
        return _verdict("compliance", SKIPPED, ["The compliance check did not run"])
    if compliance.get("errors"):
        return _verdict("compliance", DEGRADED, [str(error) for error in compliance["errors"]])
    return _verdict("compliance", COMPLETE, [])


def _check_retrosynthesis(results: dict) -> dict:
    retro = results.get("retrosynthesis") or {}
    status = retro.get("status")
    reason = retro.get("reason")

    if status in ("disabled", "unavailable"):
        return _verdict("retrosynthesis", SKIPPED, [reason or f"Retrosynthesis was {status}"])
    if status == "failed":
        return _verdict("retrosynthesis", FAILED, [reason or "Retrosynthesis failed"])
    if status == "requires_input":
        return _verdict("retrosynthesis", FAILED, [
            "The engine could not build a route and is asking for synthesis evidence"
        ])
    if status == "no_routes":
        return _verdict("retrosynthesis", FAILED, ["No synthesis route was found"])

    plan = retro.get("result") or {}
    routes = plan.get("polymer_routes") or []
    if not routes:
        return _verdict("retrosynthesis", FAILED, ["The plan contains no routes"])

    # A route object that carries no reaction steps is not a route. The engine
    # can emit one with a perfect pathway_score, so the payload has to be checked
    # rather than the status field.
    with_steps = [route for route in routes if route.get("steps")]
    if not with_steps:
        return _verdict("retrosynthesis", FAILED, [
            f"{len(routes)} route object(s) returned but none contain any reaction steps"
        ])

    reasons = []
    if not any(route.get("monomers") for route in with_steps):
        reasons.append("No route identifies its starting monomers, so residual monomers cannot be screened")
    if status == "provisional" or retro.get("evidence_source") == "offline_curated_route":
        planning_error = (retro.get("planning") or {}).get("error")
        reasons.append(
            "Route came from the offline curated table, not from evidence that was planned "
            "and checked" + (f": {planning_error}" if planning_error else "")
        )
    if len(with_steps) < len(routes):
        reasons.append(f"{len(routes) - len(with_steps)} of {len(routes)} routes contain no steps")
    skip = (plan.get("metadata") or {}).get("aizynth_skip_reason")
    if skip:
        reasons.append(f"Monomer route enrichment skipped: {skip}")
    for warning in plan.get("warnings") or []:
        reasons.append(str(warning))
    return _verdict("retrosynthesis", DEGRADED if reasons else COMPLETE, reasons)


def _check_monomer_safety(results: dict) -> dict:
    screened = results.get("monomer_safety")
    plan = (results.get("retrosynthesis") or {}).get("result") or {}
    all_monomers = [
        monomer
        for route in (plan.get("polymer_routes") or [])
        for monomer in (route.get("monomers") or [])
    ]
    unresolved = [
        monomer.get("name") or "unnamed"
        for monomer in all_monomers
        if not monomer.get("smiles")
    ]
    expected = [
        monomer
        for monomer in all_monomers
        if monomer.get("smiles") and "[*]" not in monomer["smiles"]
    ]
    if unresolved:
        return _verdict("monomer_safety", DEGRADED, [
            "No structure resolved, so these could not be screened: " + ", ".join(unresolved)
        ])
    if not expected:
        return _verdict("monomer_safety", SKIPPED, [
            "No residual monomers were screened because retrosynthesis identified none"
        ])
    if not screened:
        return _verdict("monomer_safety", SKIPPED, [
            f"{len(expected)} monomer(s) were identified but none were screened"
        ])
    reasons = []
    unparsed = [
        item.get("name") or item.get("smiles")
        for item in screened
        if item.get("parsed") is False
    ]
    if unparsed:
        reasons.append(
            "Structure could not be parsed, so no screen ran for: "
            + ", ".join(str(name) for name in unparsed)
        )
    unavailable = [
        item.get("name") or item.get("smiles")
        for item in screened
        if not ((item.get("admet") or {}).get("available", False))
    ]
    if unavailable:
        reasons.append(
            "ADMET predictions unavailable for: " + ", ".join(str(name) for name in unavailable)
        )
    out_of_domain = [
        item.get("name") or item.get("smiles")
        for item in screened
        if (item.get("admet") or {}).get("available", False)
        and not (item.get("admet") or {}).get("in_domain", True)
    ]
    if out_of_domain:
        reasons.append(
            "Too small for the ADMET applicability domain, structural screen only: "
            + ", ".join(str(name) for name in out_of_domain)
        )
    if reasons:
        return _verdict("monomer_safety", DEGRADED, reasons)
    return _verdict("monomer_safety", COMPLETE, [])


def _physics_records(physics: dict) -> list[dict]:
    payload = (physics.get("result") or {}).get("results")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [item for item in (payload.get("md_results_raw") or []) if isinstance(item, dict)]
    return []


def _check_physics(results: dict) -> dict:
    physics = results.get("physics") or {}
    status = physics.get("status")
    reason = physics.get("reason")

    if status in ("disabled", "unavailable"):
        return _verdict("physics", SKIPPED, [reason or f"Molecular physics was {status}"])
    if status == "failed":
        return _verdict("physics", FAILED, [reason or "The simulation failed"])

    records = _physics_records(physics)
    energies = [
        record.get("interaction_energy_kj_mol")
        for record in records
        if isinstance(record.get("interaction_energy_kj_mol"), (int, float))
    ]
    if not energies:
        return _verdict("physics", FAILED, ["The simulation produced no interaction energy"])

    reasons = []
    # An interaction energy read off a single minimised pose is one sample of a
    # quantity that moves by hundreds of kJ/mol between runs, so it cannot rank
    # candidates. Sampling has to have run, and it has to have produced enough frames
    # to report a spread alongside the mean.
    sampling = physics.get("sampling") or {}
    minimum = _minimum_frames()
    frames = [
        record.get("n_frames_averaged")
        for record in records
        if isinstance(record.get("n_frames_averaged"), int)
    ]
    if not sampling.get("npt_enabled", False):
        reasons.append(
            "Constant-pressure sampling was disabled, so the interaction energy comes "
            "from a single minimised pose and carries no uncertainty"
        )
    elif not frames:
        reasons.append("Sampling ran but no frames were averaged into the interaction energy")
    elif min(frames) < minimum:
        reasons.append(
            f"Interaction energy averaged over {min(frames)} frame(s), below the {minimum} "
            "frame minimum for a usable mean"
        )
    if frames and not any(
        isinstance(record.get("interaction_energy_kj_mol_std"), (int, float)) for record in records
    ):
        reasons.append("No standard deviation was reported for the interaction energy")

    # A trajectory that is still drifting has not reached the state it claims to
    # measure: the two halves of the production window disagree by more than the
    # spread within them, so the mean describes the approach, not the equilibrium.
    for record in records:
        drift = record.get("interaction_energy_drift_kj_mol")
        spread = record.get("interaction_energy_kj_mol_std")
        if not isinstance(drift, (int, float)) or not isinstance(spread, (int, float)):
            continue
        if abs(drift) > max(spread, 1e-9):
            reasons.append(
                f"Interaction energy is still drifting ({drift:+.1f} kJ/mol across the "
                f"production window, larger than its {spread:.1f} kJ/mol spread), so the "
                "trajectory has not converged and the mean cannot rank this candidate"
            )
    if sampling.get("truncated_by_wall_clock"):
        reasons.append(
            f"Sampling stopped at the {sampling.get('wall_clock_limit_s')}s wall-clock limit "
            "before the requested trajectory finished"
        )
    payload = (physics.get("result") or {}).get("results")
    if isinstance(payload, dict):
        for name, analysis in (payload.get("property_analysis") or {}).items():
            if not isinstance(analysis, dict):
                continue
            missing = sorted(key for key, value in analysis.items() if value is None)
            if missing:
                reasons.append(f"{name}: no value computed for {', '.join(missing)}")
        for item in payload.get("evaluation_progress") or []:
            if isinstance(item, dict) and item.get("status") != "completed":
                reasons.append(str(item.get("reason") or f"A candidate did not complete ({item.get('status')})"))
    return _verdict("physics", DEGRADED if reasons else COMPLETE, reasons)


CHECKS = (
    _check_target,
    _check_structure_resolution,
    _check_validation,
    _check_safety,
    _check_compliance,
    _check_retrosynthesis,
    _check_monomer_safety,
    _check_physics,
)


def audit(results: dict) -> dict:
    """Report per-stage completeness plus an overall verdict."""
    stages = [check(results or {}) for check in CHECKS]
    blocking = [stage for stage in stages if stage["status"] in BLOCKING]
    degraded = [stage for stage in stages if stage["status"] == DEGRADED]
    verdict = "incomplete" if blocking else "complete"
    return {
        "verdict": verdict,
        "stages": stages,
        "blocking": [stage["stage"] for stage in blocking],
        "degraded": [stage["stage"] for stage in degraded],
    }


def failure_message(report: dict) -> str:
    parts = []
    for stage in report["stages"]:
        if stage["status"] in BLOCKING:
            label = stage["stage"].replace("_", " ")
            detail = stage["reasons"][0] if stage["reasons"] else stage["status"]
            parts.append(f"{label}: {detail}")
    return "Incomplete scientific run - " + "; ".join(parts)
