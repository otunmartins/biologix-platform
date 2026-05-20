"""Pydantic data models for retrosynthesis routes, steps, and results."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PolymerizationType(str, Enum):
    RAFT = "RAFT"
    ATRP = "ATRP"
    CONDENSATION = "condensation"
    RING_OPENING = "ring_opening"
    FREE_RADICAL = "free_radical"
    STEP_GROWTH = "step_growth"
    OTHER = "other"
    UNKNOWN = "unknown"


class MonomerSource(str, Enum):
    PURCHASABLE = "purchasable"
    NEEDS_SYNTHESIS = "needs_synthesis"
    UNKNOWN = "unknown"


class ADMETFlag(BaseModel):
    endpoint: str
    value: float
    unit: str = ""
    flagged: bool = False
    threshold: Optional[float] = None


class MonomerInfo(BaseModel):
    smiles: str
    name: Optional[str] = None
    source: MonomerSource = MonomerSource.UNKNOWN
    supplier: Optional[str] = None
    admet_flags: List[ADMETFlag] = Field(default_factory=list)
    synthesis_route: Optional[SmallMolRoute] = None


class SmallMolStep(BaseModel):
    reaction_smarts: Optional[str] = None
    reactants: List[str] = Field(default_factory=list)
    product: str = ""
    template_id: Optional[str] = None


class SmallMolRoute(BaseModel):
    """AiZynthFinder-produced route for a single monomer."""
    target_smiles: str
    steps: List[SmallMolStep] = Field(default_factory=list)
    score: float = 0.0
    is_solved: bool = False
    building_blocks: List[str] = Field(default_factory=list)


class PolymerRetroStep(BaseModel):
    """One step in a polymer retrosynthetic pathway."""
    reactant_names: List[str] = Field(default_factory=list)
    product_name: str = ""
    reaction_type: Optional[str] = None
    conditions: Optional[str] = None
    literature_source: Optional[str] = None


class PolymerRoute(BaseModel):
    """A complete polymer retrosynthetic route from RetroSynthesisAgent."""
    target_polymer: str
    polymerization_type: PolymerizationType = PolymerizationType.UNKNOWN
    steps: List[PolymerRetroStep] = Field(default_factory=list)
    monomers: List[MonomerInfo] = Field(default_factory=list)
    literature_refs: List[str] = Field(default_factory=list)
    pathway_score: float = 0.0
    recommended: bool = False


class RetrosynthesisRequest(BaseModel):
    target: str = Field(
        ..., description="Target polymer as PSMILES, SMILES, or common name"
    )
    biologic_target: str = Field(
        default="insulin",
        description="Biologic being stabilized (e.g. insulin, adalimumab, trastuzumab)",
    )
    biologic_pdb_path: Optional[str] = Field(
        default=None,
        description="Optional absolute path to biologic PDB for OpenMM matrix context",
    )
    constraints: Optional[RetrosynthesisConstraints] = None
    session_dir: Optional[str] = Field(
        default=None,
        description="Session directory for persistent retro workspace and cached extractions",
    )


class RetrosynthesisConstraints(BaseModel):
    allowed_mechanisms: Optional[List[PolymerizationType]] = None
    max_steps: int = 10
    banned_reagents: List[str] = Field(default_factory=list)
    require_purchasable_monomers: bool = False
    max_routes: int = 5
    enrich_monomers_with_aizynth: bool = Field(
        default=True,
        description=(
            "When AiZynthFinder models are ready, plan small-molecule synthesis routes "
            "for leaf monomers (e.g. lactide from purchasable blocks), not only when "
            "monomers are flagged non-purchasable."
        ),
    )


class RetrosynthesisResult(BaseModel):
    request: RetrosynthesisRequest
    polymer_routes: List[PolymerRoute] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# Fix forward references
MonomerInfo.model_rebuild()
RetrosynthesisRequest.model_rebuild()
