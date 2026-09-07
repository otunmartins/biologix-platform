"""Curated polymer retrosynthesis templates when literature KG is unavailable."""

from __future__ import annotations

from typing import Optional

from biologix_ai.material_mappings import validate_psmiles
from biologix_ai.retrosynthesis.models import (
    MonomerInfo,
    MonomerSource,
    PolymerRoute,
    PolymerRetroStep,
    PolymerizationType,
)

_TEMPLATES: dict[str, PolymerRoute] = {
    "[*]CC([*])C(=O)O": PolymerRoute(
        target_polymer="poly(acrylic acid)",
        polymerization_type=PolymerizationType.FREE_RADICAL,
        steps=[
            PolymerRetroStep(
                reactant_names=["acrylic acid"],
                product_name="poly(acrylic acid)",
                reaction_type="free radical polymerization",
                conditions="RAFT or ATRP; AIBN initiator; 60-80°C; 4-24h",
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="C=CC(=O)O",
                name="acrylic acid",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]CC([*])C(=O)N": PolymerRoute(
        target_polymer="polyacrylamide",
        polymerization_type=PolymerizationType.FREE_RADICAL,
        steps=[
            PolymerRetroStep(
                reactant_names=["acrylamide"],
                product_name="polyacrylamide",
                reaction_type="free radical polymerization",
                conditions="AIBN or redox initiator; aqueous or organic solvent; 40-70°C",
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="C=CC(=O)N",
                name="acrylamide",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]CC([*])O": PolymerRoute(
        target_polymer="poly(vinyl alcohol)",
        polymerization_type=PolymerizationType.FREE_RADICAL,
        steps=[
            PolymerRetroStep(
                reactant_names=["vinyl acetate"],
                product_name="poly(vinyl acetate)",
                reaction_type="free radical polymerization",
                conditions="AIBN; 60-80°C",
                literature_source="curated template",
            ),
            PolymerRetroStep(
                reactant_names=["poly(vinyl acetate)"],
                product_name="poly(vinyl alcohol)",
                reaction_type="hydrolysis",
                conditions="NaOH or KOH; methanol/water; 60-80°C",
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="CC(=O)OC=C",
                name="vinyl acetate",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.45,
        recommended=True,
    ),
    "[*]OCC[*]": PolymerRoute(
        target_polymer="poly(ethylene glycol)",
        polymerization_type=PolymerizationType.RING_OPENING,
        steps=[
            PolymerRetroStep(
                reactant_names=["ethylene oxide"],
                product_name="poly(ethylene glycol)",
                reaction_type="anionic ring-opening polymerization",
                conditions="KOH or NaOH catalyst; 80-150°C",
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="C1CO1",
                name="ethylene oxide",
                source=MonomerSource.NEEDS_SYNTHESIS,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]OC(=O)C(C)[*]": PolymerRoute(
        target_polymer="poly(lactic acid)",
        polymerization_type=PolymerizationType.RING_OPENING,
        steps=[
            PolymerRetroStep(
                reactant_names=["lactide"],
                product_name="poly(lactic acid)",
                reaction_type="ring-opening polymerization",
                conditions="Sn(Oct)2 or DBU catalyst; 130-180°C",
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="CC1OC(=O)C(C)OC1=O",
                name="lactide",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]OC(=O)COC(=O)C(C)[*]": PolymerRoute(
        target_polymer="poly(lactic-co-glycolic acid)",
        polymerization_type=PolymerizationType.RING_OPENING,
        steps=[
            PolymerRetroStep(
                reactant_names=["lactide", "glycolide"],
                product_name="poly(lactic-co-glycolic acid)",
                reaction_type="ring-opening copolymerization",
                conditions=(
                    "Sn(Oct)2 catalyst with an alcohol initiator; inert atmosphere; "
                    "typically 130-180°C; feed ratio controls lactide:glycolide composition"
                ),
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="CC1OC(=O)C(C)OC1=O",
                name="lactide",
                source=MonomerSource.UNKNOWN,
            ),
            MonomerInfo(
                smiles="O=C1OCOC(=O)CO1",
                name="glycolide",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]OC(=O)CCCCC[*]": PolymerRoute(
        target_polymer="poly(caprolactone)",
        polymerization_type=PolymerizationType.RING_OPENING,
        steps=[
            PolymerRetroStep(
                reactant_names=["epsilon-caprolactone"],
                product_name="poly(caprolactone)",
                reaction_type="ring-opening polymerization",
                conditions=(
                    "Sn(Oct)2 with an alcohol initiator; inert atmosphere; "
                    "typically 100-160°C"
                ),
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="O=C1OCCCCC1",
                name="epsilon-caprolactone",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
    "[*]OC1C(N)C(O)C(CO)OC1[*]": PolymerRoute(
        target_polymer="chitosan",
        polymerization_type=PolymerizationType.OTHER,
        steps=[
            PolymerRetroStep(
                reactant_names=["chitin"],
                product_name="chitosan",
                reaction_type="alkaline deacetylation",
                conditions=(
                    "40-50 wt% aqueous NaOH; typically 80-120°C under an inert "
                    "atmosphere; wash to neutral pH and dry; time and temperature "
                    "control the degree of deacetylation"
                ),
                literature_source="curated template",
            ),
        ],
        monomers=[
            MonomerInfo(
                smiles="CC(=O)NC1C(O)OC(CO)C(O)C1O",
                name="chitin (N-acetyl-D-glucosamine repeat-unit precursor)",
                source=MonomerSource.UNKNOWN,
            ),
        ],
        pathway_score=0.5,
        recommended=True,
    ),
}


def _canonical_key(psmiles: str) -> str:
    v = validate_psmiles(psmiles)
    if v.get("valid") and v.get("canonical"):
        return v["canonical"]
    return psmiles.strip()


def lookup_template(psmiles: str) -> Optional[PolymerRoute]:
    if not psmiles or "[*]" not in psmiles:
        return None
    key = _canonical_key(psmiles)
    route = _TEMPLATES.get(key)
    if route is None:
        for tmpl_ps, tmpl_route in _TEMPLATES.items():
            if _canonical_key(tmpl_ps) == key:
                route = tmpl_route
                break
    return route.model_copy(deep=True) if route else None
