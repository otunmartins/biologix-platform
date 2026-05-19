"""Retrosynthesis and ADMET router.

Replaces the flat routes on app.py with versioned, tagged, response-model-typed
equivalents. The existing /retrosynthesis/plan and /admet/* routes in app.py
will be superseded; this router uses /api/ prefix for clean namespacing.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from insulin_ai.http_api.schemas import (
    CompiledReport,
    RetrosynthesisResult,
    ToxicityResult,
)

router = APIRouter(tags=["Retrosynthesis", "ADMET"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class RetrosynthesisPlanRequest(BaseModel):
    target: str = Field(..., description="Polymer PSMILES or common name")
    biologic_target: str = Field(default="insulin", description="Biologic being stabilised")
    max_routes: int = Field(default=5, ge=1, le=20)
    allowed_mechanisms: Optional[List[str]] = None
    banned_reagents: List[str] = Field(default_factory=list)
    biologic_pdb_path: str = ""
    session_dir: str = Field(default="", description="Session folder for retro workspace")


class PrepareRetrosynthesisRequest(BaseModel):
    target: str
    biologic_target: str = "insulin"
    session_dir: str = Field(..., description="Session folder path")
    max_pdfs: int = Field(default=5, ge=1, le=20)


class SubmitRetroExtractionsRequest(BaseModel):
    session_dir: str
    material_name: str
    extractions: dict = Field(..., description="paper_name -> reaction text")


class CompileRequest(BaseModel):
    target: str
    biologic_target: str = "insulin"
    max_routes: int = 5
    run_admet: bool = True
    biologic_pdb_path: str = ""


class ADMETSingleRequest(BaseModel):
    smiles: str = Field(..., description="Monomer SMILES to screen")


class ADMETBatchRequest(BaseModel):
    smiles_list: List[str] = Field(..., description="List of monomer SMILES")


# ---------------------------------------------------------------------------
# Retrosynthesis routes
# ---------------------------------------------------------------------------

@router.post(
    "/api/retrosynthesis/plan",
    response_model=RetrosynthesisResult,
    summary="Plan retrosynthetic routes for a polymer excipient",
    description=(
        "Uses RetroSynthesisAgent (polymer routes) and AiZynthFinder (small-molecule "
        "monomer routes) to produce RAFT/ATRP synthesis plans with purchasability checks."
    ),
)
def retrosynthesis_plan(req: RetrosynthesisPlanRequest):
    from insulin_ai.retrosynthesis.models import (
        PolymerizationType,
        RetrosynthesisConstraints,
        RetrosynthesisRequest,
    )
    from insulin_ai.services.retrosynthesis_service import plan_retrosynthesis

    mechanisms = None
    if req.allowed_mechanisms:
        mechanisms = []
        for m in req.allowed_mechanisms:
            try:
                mechanisms.append(PolymerizationType(m.strip().upper()))
            except ValueError:
                mechanisms.append(PolymerizationType.OTHER)

    request = RetrosynthesisRequest(
        target=req.target,
        biologic_target=req.biologic_target,
        biologic_pdb_path=req.biologic_pdb_path,
        session_dir=req.session_dir or None,
        constraints=RetrosynthesisConstraints(
            max_routes=req.max_routes,
            allowed_mechanisms=mechanisms,
            banned_reagents=req.banned_reagents,
        ),
    )
    try:
        return plan_retrosynthesis(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/api/retrosynthesis/compile",
    response_model=CompiledReport,
    summary="Run the full pipeline and compile a ranked report",
    description=(
        "Runs retrosynthesis + optional ADMET screening + scoring to produce a "
        "CompiledReport with route scorecards, safety summary, and next-steps narrative."
    ),
)
def retrosynthesis_compile(req: CompileRequest):
    from insulin_ai.retrosynthesis.models import (
        RetrosynthesisConstraints,
        RetrosynthesisRequest,
    )
    from insulin_ai.services.results_compiler import compile_results
    from insulin_ai.services.retrosynthesis_service import plan_retrosynthesis
    from insulin_ai.services.toxicity_service import screen_monomer

    request = RetrosynthesisRequest(
        target=req.target,
        biologic_target=req.biologic_target,
        biologic_pdb_path=req.biologic_pdb_path,
        constraints=RetrosynthesisConstraints(max_routes=req.max_routes),
    )
    try:
        retro_result = plan_retrosynthesis(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Retrosynthesis failed: {exc}")

    tox_results = {}
    if req.run_admet:
        seen: set = set()
        for route in retro_result.polymer_routes:
            for monomer in route.monomers:
                if monomer.smiles not in seen:
                    seen.add(monomer.smiles)
                    try:
                        tox_results[monomer.smiles] = screen_monomer(monomer.smiles)
                    except Exception:
                        pass

    try:
        return compile_results(retro_result, tox_results=tox_results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Compile failed: {exc}")


@router.post(
    "/api/retrosynthesis/prepare",
    summary="Prepare retrosynthesis workspace (download PDFs)",
)
def retrosynthesis_prepare(req: PrepareRetrosynthesisRequest):
    from pathlib import Path

    from insulin_ai.services.retrosynthesis_service import prepare_retrosynthesis_workspace

    session = Path(req.session_dir).expanduser().resolve()
    if not session.is_dir():
        raise HTTPException(status_code=400, detail=f"session_dir not found: {session}")
    try:
        return prepare_retrosynthesis_workspace(
            target=req.target,
            session_dir=session,
            max_pdfs=req.max_pdfs,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/api/retrosynthesis/submit-extractions",
    summary="Submit agent-produced reaction extractions",
)
def retrosynthesis_submit(req: SubmitRetroExtractionsRequest):
    from pathlib import Path

    from insulin_ai.retrosynthesis.retro_adapter import normalize_extractions, write_llm_res

    session = Path(req.session_dir).expanduser().resolve()
    if not session.is_dir():
        raise HTTPException(status_code=400, detail=f"session_dir not found: {session}")
    try:
        data = normalize_extractions(req.extractions)
        llm_path = write_llm_res(session, req.material_name, data)
        return {
            "ok": True,
            "llm_res_path": str(llm_path),
            "paper_count": len(data),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/api/retrosynthesis/templates",
    summary="List available polymerisation types",
    description="Returns the set of recognised polymerisation type tokens accepted by plan endpoints.",
)
def list_templates():
    from insulin_ai.retrosynthesis.models import PolymerizationType

    return {
        "polymerization_types": [t.value for t in PolymerizationType],
        "note": "Template catalog is extensible via rxnutils.",
    }


# ---------------------------------------------------------------------------
# ADMET routes
# ---------------------------------------------------------------------------

@router.post(
    "/api/admet/screen",
    response_model=ToxicityResult,
    summary="Screen a single monomer SMILES for toxicity",
    description="SMARTS structural alerts + ADMET-AI predictions for one monomer SMILES.",
)
def admet_screen(req: ADMETSingleRequest):
    from insulin_ai.services.toxicity_service import screen_monomer

    try:
        return screen_monomer(req.smiles)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/api/admet/batch",
    response_model=List[ToxicityResult],
    summary="Screen multiple monomer SMILES for toxicity",
    description="Batch SMARTS + ADMET-AI screening for a list of monomer SMILES.",
)
def admet_batch(req: ADMETBatchRequest):
    from insulin_ai.services.toxicity_service import screen_monomers_batch

    try:
        return screen_monomers_batch(req.smiles_list)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
