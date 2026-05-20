"""Candidate operations router: profile, screen, compliance, validate."""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from biologix_ai.http_api.schemas import (
    CandidateProfileRequest,
    CandidateProfileResponse,
    ComplianceRequest,
    ComplianceResponse,
    LibraryScreenItem,
    RetrosynthesisSummary,
    ScreenLibraryRequest,
    ToxicityResult,
    ValidateRequest,
    ValidationResponse,
)

router = APIRouter(prefix="/api/candidates", tags=["Candidates"])


# ---------------------------------------------------------------------------
# POST /api/candidates/validate — PSMILES structural validation
# ---------------------------------------------------------------------------

@router.post(
    "/validate",
    response_model=ValidationResponse,
    summary="Validate a PSMILES string",
    description="Runs RDKit structural checks, extracts functional groups, optionally looks up PubChem.",
)
def validate_candidate(req: ValidateRequest):
    try:
        from biologix_ai.services.psmiles_service import validate_psmiles

        result = validate_psmiles(
            psmiles=req.psmiles,
            material_name=req.material_name,
            crosscheck_web=req.crosscheck_web,
        )
        if isinstance(result, str):
            result = json.loads(result)
        return ValidationResponse(**result)
    except Exception as exc:
        return ValidationResponse(
            psmiles=req.psmiles,
            valid=False,
            errors=[str(exc)],
        )


# ---------------------------------------------------------------------------
# POST /api/candidates/compliance — excipient compliance check
# ---------------------------------------------------------------------------

@router.post(
    "/compliance",
    response_model=ComplianceResponse,
    summary="Check regulatory excipient compliance",
    description=(
        "Checks EMA/FDA/GRAS approved-excipient databases, GRAS status, "
        "and immunogenicity structural alerts for a PSMILES."
    ),
)
def check_compliance(req: ComplianceRequest):
    from biologix_ai.services.compliance_service import check_excipient_compliance

    result = check_excipient_compliance(
        psmiles=req.psmiles,
        jurisdiction=req.jurisdiction,
        check_gras=req.check_gras,
        check_immunogenicity=req.check_immunogenicity,
    )
    return ComplianceResponse(**result.to_dict())


# ---------------------------------------------------------------------------
# POST /api/candidates/profile — single-call composite dossier
# ---------------------------------------------------------------------------

@router.post(
    "/profile",
    response_model=CandidateProfileResponse,
    summary="Get a full candidate dossier in one call",
    description=(
        "Combines PSMILES validation + ADMET screening + retrosynthesis route summary "
        "+ excipient compliance into a single structured response. "
        "Equivalent to the NovoMCP get_molecule_profile pattern."
    ),
)
def candidate_profile(req: CandidateProfileRequest):
    profile = CandidateProfileResponse(
        psmiles=req.psmiles,
        biologic_target=req.biologic_target,
    )

    # 1. Validate
    try:
        from biologix_ai.services.psmiles_service import validate_psmiles

        val = validate_psmiles(psmiles=req.psmiles, material_name="", crosscheck_web=False)
        if isinstance(val, str):
            val = json.loads(val)
        profile.validation = ValidationResponse(**val)
    except Exception as exc:
        profile.validation = ValidationResponse(psmiles=req.psmiles, valid=False, errors=[str(exc)])

    # 2. ADMET
    if req.run_admet:
        try:
            from biologix_ai.services.toxicity_service import screen_monomer

            smiles_bare = req.psmiles.replace("[*]", "").strip()
            tox = screen_monomer(smiles_bare)
            profile.admet = tox
        except Exception as exc:
            profile.admet = ToxicityResult(
                smiles=req.psmiles,
                smarts_hits=[],
                admet=None,
                safe=None,
                warnings=[str(exc)],
            )

    # 3. Retrosynthesis
    if req.run_retro:
        try:
            from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
            from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis

            request = RetrosynthesisRequest(
                target=req.psmiles,
                biologic_target=req.biologic_target,
                constraints=RetrosynthesisConstraints(max_routes=3),
            )
            result = plan_retrosynthesis(request)
            profile.retrosynthesis = RetrosynthesisSummary(
                n_routes=len(result.polymer_routes),
                routes_summary=[
                    {
                        "steps": len(r.steps),
                        "monomers": [m.smiles for m in r.monomers],
                        "recommended": r.recommended,
                    }
                    for r in result.polymer_routes[:3]
                ],
                warnings=result.warnings,
            )
        except Exception as exc:
            profile.retrosynthesis = RetrosynthesisSummary(warnings=[str(exc)])

    # 4. Compliance
    if req.run_compliance:
        try:
            from biologix_ai.services.compliance_service import check_excipient_compliance

            comp = check_excipient_compliance(
                psmiles=req.psmiles,
                jurisdiction=req.jurisdiction,
            )
            profile.compliance = ComplianceResponse(**comp.to_dict())
        except Exception as exc:
            profile.compliance = ComplianceResponse(
                psmiles=req.psmiles,
                overall_status="unknown",
                errors=[str(exc)],
            )

    return profile


# ---------------------------------------------------------------------------
# POST /api/candidates/screen — batch library screening
# ---------------------------------------------------------------------------

@router.post(
    "/screen",
    response_model=List[LibraryScreenItem],
    summary="Batch screen a candidate library",
    description=(
        "Validates, screens ADMET, and checks compliance for up to max_candidates "
        "PSMILES in one call. Returns results sorted by disposition: pass, warning, fail. "
        "Equivalent to the NovoMCP screen_library pattern."
    ),
)
def screen_library(req: ScreenLibraryRequest):
    candidates = req.psmiles_list[: req.max_candidates]
    results: List[LibraryScreenItem] = []

    for psmiles in candidates:
        profile_req = CandidateProfileRequest(
            psmiles=psmiles,
            biologic_target=req.biologic_target,
            run_retro=req.run_retro,
            run_admet=req.run_admet,
            run_compliance=req.run_compliance,
            jurisdiction=req.jurisdiction,
        )
        try:
            profile = candidate_profile(profile_req)
        except Exception as exc:
            profile = CandidateProfileResponse(
                psmiles=psmiles,
                biologic_target=req.biologic_target,
                validation=ValidationResponse(psmiles=psmiles, valid=False, errors=[str(exc)]),
            )

        disposition: str = "pass"
        if profile.validation and not profile.validation.valid:
            disposition = "fail"
        elif profile.admet and profile.admet.safe is False:
            disposition = "fail"
        elif profile.compliance and profile.compliance.overall_status == "flagged":
            disposition = "warning"

        results.append(LibraryScreenItem(**profile.model_dump(), library_disposition=disposition))

    order = {"pass": 0, "warning": 1, "fail": 2}
    results.sort(key=lambda r: order.get(r.library_disposition, 2))
    return results
