"""FastAPI application: REST mirror of MCP capabilities.

Run:   uvicorn biologix_ai.http_api.app:app --reload
Docs:  http://localhost:8000/docs
OpenAPI JSON (for Claude Design / codegen): http://localhost:8000/openapi.json

Architecture
------------
All routes call the same biologix_ai.services.* layer as the MCP server.
No science code is duplicated. The REST layer is purely an interface adapter.

CORS
----
Set BIOLOGIX_AI_CORS_ORIGINS env var to a comma-separated list of allowed
origins (e.g. "http://localhost:3000,https://your-app.vercel.app").
Defaults to "*" (all origins) for local development.
"""

from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from biologix_ai.http_api.routers import (
    biologics,
    candidates,
    experiments,
    literature,
    openmm,
    personas,
    psmiles,
    reports,
    retrosynthesis,
)
from biologix_ai.http_api.sse import router as sse_router

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Biologics AI Platform API",
    description=(
        "REST API for any-biologic delivery materials discovery: "
        "retrosynthesis planning, monomer ADMET screening, excipient compliance, "
        "candidate profiling, batch screening, funnel checkpoints, pipeline audit trail, "
        "and live pipeline streaming. "
        "Same service layer as the MCP server for OpenCode. "
        "Point Claude Design or any frontend codegen tool at /openapi.json."
    ),
    version="0.5.0",
    openapi_tags=[
        {"name": "Health", "description": "Liveness and feature availability checks."},
        {"name": "Experiments", "description": "Campaign lifecycle: create sessions, poll state, retrieve results."},
        {"name": "Candidates", "description": "Per-candidate operations: validate, profile, batch screen, compliance."},
        {"name": "Literature", "description": "Literature mining, PaperQA2, and scientific search."},
        {"name": "PSMILES", "description": "Polymer SMILES generation, mutation, fingerprints, rendering."},
        {"name": "OpenMM", "description": "OpenMM matrix screening with background jobs and SSE."},
        {"name": "Retrosynthesis", "description": "Polymer retrosynthesis route planning."},
        {"name": "ADMET", "description": "Residual monomer toxicity and ADMET-AI predictions."},
        {"name": "Biologics", "description": "Biologic target resolution and discovery loops."},
        {"name": "Reports", "description": "Discovery summary and PDF report generation."},
        {"name": "Personas", "description": "Expert persona presets with scoring weight vectors."},
        {"name": "Streaming", "description": "Server-Sent Events for live pipeline progress."},
    ],
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

_cors_raw = os.environ.get("BIOLOGIX_AI_CORS_ORIGINS", "*")
_origins: List[str] = (
    ["*"] if _cors_raw.strip() == "*"
    else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_cors_raw.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(experiments.router)
app.include_router(candidates.router)
app.include_router(literature.router)
app.include_router(psmiles.router)
app.include_router(openmm.router)
app.include_router(biologics.router)
app.include_router(reports.router)
app.include_router(retrosynthesis.router)
app.include_router(personas.router)
app.include_router(sse_router)

# ---------------------------------------------------------------------------
# Health (kept on root for backward compatibility)
# ---------------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.5.0"
    retrosynthesis_agent_available: bool
    aizynthfinder_available: bool
    aizynthfinder_models_ready: bool
    admet_available: bool


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health():
    """Liveness check with feature availability flags."""
    from biologix_ai.retrosynthesis.aizynth_config import models_ready
    from biologix_ai.services.retrosynthesis_service import (
        _is_aizynthfinder_available,
        _is_retrosynthesisagent_available,
    )
    from biologix_ai.services.toxicity_service import _is_admet_available

    aizynth_pkg = _is_aizynthfinder_available()
    return HealthResponse(
        retrosynthesis_agent_available=_is_retrosynthesisagent_available(),
        aizynthfinder_available=aizynth_pkg,
        aizynthfinder_models_ready=models_ready() if aizynth_pkg else False,
        admet_available=_is_admet_available(),
    )
