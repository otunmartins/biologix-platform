"""Persona presets router.

Exposes the five expert personas defined in the blueprint (section 10.2)
as a typed REST resource. A frontend uses these to render a persona selector
with adjustable scoring weight sliders.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from biologix_ai.http_api.schemas import PersonaPreset
from biologix_ai.persona_presets import PERSONA_MAP, PERSONAS

router = APIRouter(prefix="/api/personas", tags=["Personas"])


@router.get(
    "",
    response_model=List[PersonaPreset],
    summary="List all available expert personas",
    description=(
        "Returns the five blueprint personas with their scoring weight presets. "
        "Use the id field to request experiments or filter results by persona."
    ),
)
def list_personas():
    return PERSONAS


@router.get(
    "/{persona_id}",
    response_model=PersonaPreset,
    summary="Get a specific persona by id",
    description="Returns weight presets and description for one persona.",
)
def get_persona(persona_id: str):
    persona = PERSONA_MAP.get(persona_id)
    if persona is None:
        raise HTTPException(
            status_code=404,
            detail=f"Persona '{persona_id}' not found. Available: {list(PERSONA_MAP)}",
        )
    return persona
