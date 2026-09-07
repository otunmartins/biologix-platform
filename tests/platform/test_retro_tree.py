"""Tree construction regressions, using the real RetroSynAgent Tree.

A production run returned a route object with no steps, no monomers and a
pathway_score of 1.0. Cause: Node.expand() treats any substance PubChem can
resolve as a purchasable leaf, and the target polymer resolves, so the root was
marked a leaf and find_all_paths() returned a single empty path.
"""

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "extern" / "RetroSynthesisAgent"

pytestmark = pytest.mark.skipif(
    not (AGENT / "RetroSynAgent" / "treeBuilder.py").is_file(),
    reason="RetroSynAgent checkout is not available",
)

EXTRACTION = """Reaction 1:
Reactants: ethylene oxide, water
Products: poly(ethylene glycol)
Conditions: KOH, 120 C, 6 h"""

TARGET = "poly(ethylene glycol)"


@pytest.fixture
def tree_factory(monkeypatch):
    sys.path.insert(0, str(AGENT))
    monkeypatch.chdir(AGENT)          # CommonSubstanceDB loads emol.json relative to cwd
    from RetroSynAgent.treeBuilder import Tree

    def build(guarded: bool):
        from biologix_ai.services.retrosynthesis_service import _guard_root_expansion

        tree = Tree(TARGET, result_dict={"paper": EXTRACTION})
        # Every substance resolves in PubChem, including the target. Seeded so the
        # test never touches the network.
        tree.db.common_sub_cache = {TARGET: True, "ethylene oxide": True, "water": True}
        tree.db.smiles_cache = {TARGET: "CCO", "ethylene oxide": "C1CO1", "water": "O"}
        tree.db.save_dict_as_json = lambda *args, **kwargs: None
        if guarded:
            _guard_root_expansion(tree, TARGET)
        tree.construct_tree()
        return tree

    yield build
    sys.path.remove(str(AGENT))


def test_an_unguarded_root_collapses_to_a_single_empty_path(tree_factory):
    tree = tree_factory(guarded=False)
    assert tree.root.is_leaf is True
    assert tree.find_all_paths() == [[]]


def test_the_guard_lets_the_root_expand_into_a_real_path(tree_factory):
    tree = tree_factory(guarded=True)
    assert tree.root.is_leaf is False
    paths = tree.find_all_paths()
    assert paths and paths[0], "the root must expand into at least one reaction"


def test_the_guarded_tree_yields_a_route_with_steps_and_monomers(tree_factory):
    from biologix_ai.services.retrosynthesis_service import _routes_from_tree

    route = _routes_from_tree(tree_factory(guarded=True), TARGET)[0]
    assert len(route.steps) == 1
    step = route.steps[0]
    assert sorted(step.reactant_names) == ["ethylene oxide", "water"]
    assert step.product_name == TARGET
    assert step.conditions == "KOH, 120 C, 6 h"
    assert sorted(monomer.name for monomer in route.monomers) == ["ethylene oxide", "water"]


def test_the_unguarded_tree_reproduces_the_hollow_production_route(tree_factory):
    from biologix_ai.services.retrosynthesis_service import _routes_from_tree

    route = _routes_from_tree(tree_factory(guarded=False), TARGET)[0]
    assert route.steps == []
    assert route.monomers == []
    assert route.pathway_score == 1.0
    assert route.recommended is True


def test_the_guard_only_exempts_the_target(tree_factory):
    tree = tree_factory(guarded=True)
    assert tree.db.is_common_chemical_cached(TARGET) is False
    assert tree.db.is_common_chemical_cached("ethylene oxide") is True


def test_named_precursors_resolve_as_purchasable():
    from biologix_ai.retrosynthesis.models import MonomerSource
    from biologix_ai.services.retrosynthesis_service import _check_purchasability

    assert _check_purchasability("C1CO1", "ethylene oxide") == MonomerSource.PURCHASABLE


def test_a_plain_name_is_not_stored_as_its_own_smiles(tree_factory):
    """CommonSubstanceDB returns punctuation-free names unchanged, so 'water' came
    back as the SMILES for water and every downstream RDKit call on it failed."""
    from biologix_ai.services.retrosynthesis_service import _routes_from_tree

    route = _routes_from_tree(tree_factory(guarded=True), TARGET)[0]
    water = next(monomer for monomer in route.monomers if monomer.name == "water")
    assert water.smiles == "O"


def test_every_monomer_smiles_is_parseable(tree_factory):
    from rdkit import Chem

    from biologix_ai.services.retrosynthesis_service import _routes_from_tree

    route = _routes_from_tree(tree_factory(guarded=True), TARGET)[0]
    for monomer in route.monomers:
        assert Chem.MolFromSmiles(monomer.smiles) is not None, monomer


@pytest.mark.parametrize("value,expected", [
    ("O", True), ("C1CO1", True), ("water", False), ("ethylene oxide", False), ("", False),
])
def test_smiles_validation(value, expected):
    from biologix_ai.services.retrosynthesis_service import _valid_smiles

    assert _valid_smiles(value) is expected


def test_purchasable_monomers_are_not_sent_to_aizynthfinder(monkeypatch):
    """Lactide and glycolide are purchasable, so a USPTO search for a route to
    them is meaningless - and its failure was blocking otherwise complete runs."""
    from biologix_ai.retrosynthesis.models import (
        MonomerInfo, MonomerSource, PolymerRoute, RetrosynthesisConstraints,
        RetrosynthesisRequest,
    )
    from biologix_ai.services import retrosynthesis_service as svc

    attempted = []
    monkeypatch.setattr(svc, "_is_aizynthfinder_available", lambda: True)
    monkeypatch.setattr(svc, "models_ready", lambda: True)
    monkeypatch.setattr(svc, "_run_aizynthfinder", lambda s: attempted.append(s))
    monkeypatch.setattr(svc, "_run_retrosynthesis_agent", lambda *a, **k: ([PolymerRoute(
        target_polymer="poly(lactic-co-glycolic acid)",
        steps=[],
        monomers=[
            MonomerInfo(smiles="CC1C(=O)OC(C(=O)O1)C", name="lactide", source=MonomerSource.PURCHASABLE),
            MonomerInfo(smiles="CCCCCCCCN", name="mystery amine", source=MonomerSource.UNKNOWN),
        ],
    )], "session_agent_llm", None))

    svc.plan_retrosynthesis(RetrosynthesisRequest(
        target="poly(lactic-co-glycolic acid)",
        constraints=RetrosynthesisConstraints(enrich_monomers_with_aizynth=True),
    ))
    assert "CC1C(=O)OC(C(=O)O1)C" not in attempted, "purchasable monomer must be skipped"
