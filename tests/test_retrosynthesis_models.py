"""Tests for retrosynthesis data models and PSMILES bridge."""

import pytest

from biologix_ai.retrosynthesis.models import (
    ADMETFlag,
    MonomerInfo,
    MonomerSource,
    PolymerRoute,
    PolymerRetroStep,
    PolymerizationType,
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
    RetrosynthesisResult,
    SmallMolRoute,
    SmallMolStep,
)


def test_monomer_info_defaults():
    m = MonomerInfo(smiles="CCO")
    assert m.source == MonomerSource.UNKNOWN
    assert m.admet_flags == []
    assert m.synthesis_route is None


def test_polymer_route_construction():
    route = PolymerRoute(
        target_polymer="PEG",
        polymerization_type=PolymerizationType.CONDENSATION,
        steps=[
            PolymerRetroStep(product_name="PEG", reactant_names=["ethylene_oxide"]),
        ],
        monomers=[
            MonomerInfo(smiles="C(CO)O", name="ethylene_glycol", source=MonomerSource.PURCHASABLE),
        ],
        pathway_score=0.85,
    )
    assert route.target_polymer == "PEG"
    assert len(route.steps) == 1
    assert route.monomers[0].source == MonomerSource.PURCHASABLE


def test_retrosynthesis_request_defaults():
    req = RetrosynthesisRequest(target="[*]OCC[*]")
    assert req.biologic_target == "insulin"
    assert req.constraints is None


def test_retrosynthesis_request_custom_biologic():
    req = RetrosynthesisRequest(
        target="Polyimide",
        biologic_target="adalimumab",
        constraints=RetrosynthesisConstraints(
            max_routes=3,
            allowed_mechanisms=[PolymerizationType.CONDENSATION],
        ),
    )
    assert req.biologic_target == "adalimumab"
    assert req.constraints.max_routes == 3


def test_retrosynthesis_result_serialization():
    result = RetrosynthesisResult(
        request=RetrosynthesisRequest(target="test"),
        polymer_routes=[],
        warnings=["test warning"],
    )
    d = result.model_dump()
    assert d["warnings"] == ["test warning"]
    assert d["request"]["target"] == "test"


def test_small_mol_route():
    route = SmallMolRoute(
        target_smiles="CCO",
        steps=[SmallMolStep(product="CCO", reactants=["CC=O", "[H][H]"])],
        is_solved=True,
        building_blocks=["CC=O", "[H][H]"],
    )
    assert route.is_solved
    assert len(route.building_blocks) == 2
