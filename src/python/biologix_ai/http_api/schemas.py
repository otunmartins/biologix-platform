"""Pydantic response/request schemas for the Biologics AI Platform REST API.

All schemas mirror the JSON shapes produced by MCP tools and underlying
services. Existing domain models (RetrosynthesisResult, ToxicityResult,
CompiledReport, BiologicTarget) are re-exported here so the frontend-facing
API has a single import surface.

These models generate the OpenAPI spec consumed by frontend developers and
tools like Claude Design.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Re-export existing typed domain models
# (frontend only needs to import from this module)
# ---------------------------------------------------------------------------
from biologix_ai.retrosynthesis.models import (  # noqa: F401
    ADMETFlag,
    MonomerInfo,
    PolymerRoute,
    PolymerizationType,
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
    RetrosynthesisResult,
    SmallMolRoute,
    SmallMolStep,
)
from biologix_ai.services.biologic_resolver import BiologicTarget  # noqa: F401
from biologix_ai.services.results_compiler import (  # noqa: F401
    CompiledReport,
    RouteScorecard,
)
from biologix_ai.services.toxicity_service import (  # noqa: F401
    ADMETProfile,
    ToxicityResult,
)


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class StructuralAlert(BaseModel):
    """A single immunogenicity or aggregation structural alert."""

    name: str
    severity: Literal["info", "warning", "error"]
    note: str = ""


class ComplianceResponse(BaseModel):
    """Regulatory excipient compliance result for one PSMILES."""

    psmiles: str
    approved_match: Optional[str] = None
    approved_name: Optional[str] = None
    gras: Optional[bool] = None
    jurisdictions_matched: List[str] = Field(default_factory=list)
    precedent_count: int = 0
    immunogenicity_flags: List[Dict[str, str]] = Field(default_factory=list)
    aggregation_flags: List[Dict[str, str]] = Field(default_factory=list)
    jurisdiction_clear: bool = True
    overall_status: Literal["approved", "no_match", "flagged", "unknown"] = "unknown"
    notes: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PSMILES validation
# ---------------------------------------------------------------------------

class ValidationResponse(BaseModel):
    """Structural validation result for one PSMILES."""

    psmiles: str
    valid: bool
    canonical: Optional[str] = None
    material_name: Optional[str] = None
    functional_groups: Dict[str, Any] = Field(default_factory=dict)
    name_consistency: Optional[Dict[str, Any]] = None
    pubchem_lookup: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Candidate profile (composite single-call dossier)
# ---------------------------------------------------------------------------

class RetrosynthesisSummary(BaseModel):
    """Compact retrosynthesis summary inside a candidate profile."""

    n_routes: int = 0
    routes_summary: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CandidateProfileResponse(BaseModel):
    """Single-call candidate dossier: validation + ADMET + retro + compliance."""

    psmiles: str
    biologic_target: str = "insulin"
    validation: Optional[ValidationResponse] = None
    admet: Optional[ToxicityResult] = None
    retrosynthesis: Optional[RetrosynthesisSummary] = None
    compliance: Optional[ComplianceResponse] = None

    model_config = {"arbitrary_types_allowed": True}


class LibraryScreenItem(CandidateProfileResponse):
    """One candidate inside a batch library screen, with an overall disposition."""

    library_disposition: Literal["pass", "warning", "fail"] = "warning"


# ---------------------------------------------------------------------------
# Pipeline audit trail
# ---------------------------------------------------------------------------

class PipelineAuditRecord(BaseModel):
    """One append-only audit record for a single pipeline stage on one candidate."""

    audit_id: str
    timestamp: str
    candidate_psmiles: str
    stage: str
    disposition: Literal["pass", "fail", "warning"]
    detail: str = ""


# ---------------------------------------------------------------------------
# Funnel context (pipeline checkpoints)
# ---------------------------------------------------------------------------

class FunnelManifestEntry(BaseModel):
    """Entry in the checkpoint manifest (stage + file + saved_at)."""

    stage: str
    file: str
    saved_at: str


class FunnelCheckpoint(BaseModel):
    """A full named pipeline checkpoint including arbitrary payload data."""

    stage: str
    saved_at: str
    data: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session / experiment lifecycle
# ---------------------------------------------------------------------------

class StartExperimentRequest(BaseModel):
    """Request body for POST /api/experiments."""

    biologic_target: str = Field(
        ...,
        description="Biologic name (e.g. 'adalimumab') or 4-letter PDB ID (e.g. '3WD5').",
    )
    polymer_target: str = Field(
        default="",
        description="PSMILES or common name of the starting polymer excipient. Empty = suggest candidates.",
    )
    run_name: str = Field(
        default="",
        description="Optional human-readable session label.",
    )
    fetch_pdb: bool = Field(
        default=True,
        description="Download the PDB from RCSB if not cached locally.",
    )


class SessionResponse(BaseModel):
    """Response for a newly created or resumed biologics session."""

    session_dir: str
    biologic_resolution: BiologicTarget
    note: str = ""


class ExperimentStatus(BaseModel):
    """High-level status of a discovery session."""

    experiment_id: str
    session_dir: str
    biologic_target: str
    objective: str = ""
    last_iteration: int = 0
    candidates_count: int = 0
    top_candidate: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Persona presets
# ---------------------------------------------------------------------------

class PersonaWeights(BaseModel):
    """Scoring dimension weights for one persona (values should sum to 1.0)."""

    thermal_stability: float
    aggregation_suppression: float
    excipient_safety: float
    synthetic_accessibility: float
    regulatory_precedent: float
    literature_support: float
    other: float = 0.0


class PersonaPreset(BaseModel):
    """One of the five expert personas with its scoring weight vector."""

    id: str
    name: str
    description: str
    weights: PersonaWeights


# ---------------------------------------------------------------------------
# Batch screening request
# ---------------------------------------------------------------------------

class ScreenLibraryRequest(BaseModel):
    """Request body for POST /api/candidates/screen."""

    psmiles_list: List[str] = Field(..., description="List of PSMILES strings to screen.")
    biologic_target: str = "insulin"
    run_retro: bool = False
    run_admet: bool = True
    run_compliance: bool = True
    jurisdiction: str = "FDA,EMA"
    max_candidates: int = Field(default=50, ge=1, le=200)


class CandidateProfileRequest(BaseModel):
    """Request body for POST /api/candidates/profile."""

    psmiles: str
    biologic_target: str = "insulin"
    run_retro: bool = True
    run_admet: bool = True
    run_compliance: bool = True
    jurisdiction: str = "FDA,EMA"


class ComplianceRequest(BaseModel):
    """Request body for POST /api/candidates/compliance."""

    psmiles: str
    jurisdiction: str = "FDA,EMA"
    check_gras: bool = True
    check_immunogenicity: bool = True


class ValidateRequest(BaseModel):
    """Request body for POST /api/candidates/validate."""

    psmiles: str
    material_name: str = ""
    crosscheck_web: bool = False


# ---------------------------------------------------------------------------
# Shared error envelope
# ---------------------------------------------------------------------------

class APIError(BaseModel):
    """Standard error response envelope."""

    error: str
    detail: Optional[str] = None
