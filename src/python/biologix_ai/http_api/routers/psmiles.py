"""PSMILES cheminformatics router (MCP PSMILES tools parity)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix_ai.http_api.deps import session_path

router = APIRouter(prefix="/api/psmiles", tags=["PSMILES"])


class GeneratePSMILESRequest(BaseModel):
    material_name: str


class MutatePSMILESRequest(BaseModel):
    library_size: int = Field(default=10, ge=1, le=100)
    feedback: Dict[str, Any] = Field(default_factory=dict)


class PSMILESStringRequest(BaseModel):
    psmiles: str


class DimerizeRequest(BaseModel):
    psmiles: str
    star_index: int = Field(default=0, ge=0, le=1)


class FingerprintRequest(BaseModel):
    psmiles: str
    fingerprint_type: str = "rdkit"


class SimilarityRequest(BaseModel):
    psmiles1: str
    psmiles2: str


class RenderPNGRequest(BaseModel):
    psmiles: str
    output_basename: str = ""
    experiment_id: str = ""


@router.post("/generate", summary="Convert material name to PSMILES")
def generate_psmiles(req: GeneratePSMILESRequest) -> Dict[str, Any]:
    from biologix_ai.services.psmiles_service import generate_psmiles_from_name

    return generate_psmiles_from_name(req.material_name)


@router.post("/mutate", summary="Generate mutated PSMILES candidates")
def mutate_psmiles_endpoint(req: MutatePSMILESRequest) -> List[Dict[str, str]]:
    from biologix_ai.services.psmiles_service import mutate_psmiles

    try:
        return mutate_psmiles(library_size=req.library_size, feedback=req.feedback)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/canonicalize", summary="Canonicalize PSMILES")
def canonicalize_endpoint(req: PSMILESStringRequest) -> Dict[str, str]:
    from biologix_ai.services.psmiles_service import canonicalize_psmiles

    try:
        return {"psmiles": req.psmiles, "canonical": canonicalize_psmiles(req.psmiles)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/dimerize", summary="Dimerize PSMILES at connection point")
def dimerize_endpoint(req: DimerizeRequest) -> Dict[str, str]:
    from biologix_ai.services.psmiles_service import dimerize_psmiles

    try:
        return {"psmiles": req.psmiles, "dimer": dimerize_psmiles(req.psmiles, req.star_index)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/fingerprint", summary="Compute PSMILES fingerprint")
def fingerprint_endpoint(req: FingerprintRequest) -> Dict[str, Any]:
    from biologix_ai.services.psmiles_service import fingerprint_psmiles

    try:
        fp = fingerprint_psmiles(req.psmiles, req.fingerprint_type)
        return {"psmiles": req.psmiles, "fingerprint_type": req.fingerprint_type, "fingerprint": fp}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/similarity", summary="Compute similarity between two PSMILES")
def similarity_endpoint(req: SimilarityRequest) -> Dict[str, Any]:
    from biologix_ai.services.psmiles_service import similarity_psmiles

    try:
        sim = similarity_psmiles(req.psmiles1, req.psmiles2)
        return {"psmiles1": req.psmiles1, "psmiles2": req.psmiles2, "similarity": sim}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/render", summary="Render 2D PNG of polymer repeat unit")
def render_png_endpoint(req: RenderPNGRequest) -> Dict[str, Any]:
    from biologix_ai.services.psmiles_service import render_psmiles_png

    if req.experiment_id:
        session = session_path(req.experiment_id)
    else:
        from biologix_ai.http_api.deps import RUNS_DIR
        from biologix_ai.run_paths import new_session_dir

        session = new_session_dir(RUNS_DIR.parent, name="psmiles-render")

    try:
        return render_psmiles_png(req.psmiles, session, output_basename=req.output_basename)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
