"""Expert persona scoring presets shared by HTTP API and MCP server."""

from __future__ import annotations

from typing import Dict, List

from biologix_ai.http_api.schemas import PersonaPreset, PersonaWeights

PERSONAS: List[PersonaPreset] = [
    PersonaPreset(
        id="formulation-scientist",
        name="Formulation Scientist",
        description=(
            "Focused on drug delivery performance, device compatibility, and "
            "physicochemical stability. Prioritises thermal stability and safety "
            "over synthesis complexity."
        ),
        weights=PersonaWeights(
            thermal_stability=0.35,
            aggregation_suppression=0.10,
            excipient_safety=0.30,
            synthetic_accessibility=0.10,
            regulatory_precedent=0.05,
            literature_support=0.10,
            other=0.0,
        ),
    ),
    PersonaPreset(
        id="computational-chemist",
        name="Computational Chemist",
        description=(
            "Prioritises physics-based scoring and aggregation suppression. "
            "Treats MD trajectory data as the ground truth; regulatory precedent "
            "is out of scope for this persona."
        ),
        weights=PersonaWeights(
            thermal_stability=0.50,
            aggregation_suppression=0.30,
            excipient_safety=0.05,
            synthetic_accessibility=0.00,
            regulatory_precedent=0.00,
            literature_support=0.15,
            other=0.0,
        ),
    ),
    PersonaPreset(
        id="regulatory-affairs",
        name="Regulatory Affairs",
        description=(
            "Focuses on EMA/FDA approved excipient precedent, GRAS status, "
            "and CMC Module 3.2.P.4 compliance. Synthesis route and physics "
            "scores are lower priority."
        ),
        weights=PersonaWeights(
            thermal_stability=0.15,
            aggregation_suppression=0.00,
            excipient_safety=0.35,
            synthetic_accessibility=0.00,
            regulatory_precedent=0.40,
            literature_support=0.10,
            other=0.0,
        ),
    ),
    PersonaPreset(
        id="synthetic-chemist",
        name="Synthetic Chemist",
        description=(
            "Prioritises practical synthesis: RAFT/ATRP route accessibility, "
            "monomer cost, purchasability, and step count. Physics and regulatory "
            "scores are secondary."
        ),
        weights=PersonaWeights(
            thermal_stability=0.25,
            aggregation_suppression=0.00,
            excipient_safety=0.25,
            synthetic_accessibility=0.40,
            regulatory_precedent=0.10,
            literature_support=0.00,
            other=0.0,
        ),
    ),
    PersonaPreset(
        id="academic-researcher",
        name="Academic Researcher",
        description=(
            "Balances structural novelty with thermal stability and scientific "
            "literature support. Regulatory precedent and synthesis difficulty "
            "are lower priorities for exploratory research."
        ),
        weights=PersonaWeights(
            thermal_stability=0.35,
            aggregation_suppression=0.00,
            excipient_safety=0.15,
            synthetic_accessibility=0.00,
            regulatory_precedent=0.00,
            literature_support=0.30,
            other=0.20,
        ),
    ),
]

PERSONA_MAP: Dict[str, PersonaPreset] = {p.id: p for p in PERSONAS}
