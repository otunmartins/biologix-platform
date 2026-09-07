"""Repeat-unit vs monomer composition check.

The old check took Tanimoto similarity between a capped repeat unit and the
PubChem monomer reference and warned below 0.4. Those are different molecules by
construction, so the canonical PSMILES for PEG (0.38), PLGA (0.26) and PVA (0.09)
were all flagged as possibly wrong - the app's own curated structures.
"""

import pytest

from biologix_ai.material_mappings import _compare_repeat_to_monomer, _repeat_unit_counts


@pytest.mark.parametrize("label,psmiles,reference", [
    ("PEG from ethylene oxide", "[*]OCC[*]", "C1CO1"),
    ("PVA from vinyl alcohol", "[*]CC([*])O", "C=CO"),
    ("PCL from caprolactone", "[*]OC(=O)CCCCC[*]", "O=C1CCCCCO1"),
])
def test_addition_and_ring_opening_conserve_the_monomer_formula(label, psmiles, reference):
    assert _compare_repeat_to_monomer(psmiles, reference)["relation"] == "conserved", label


def test_condensation_is_recognised_as_a_water_loss():
    assert _compare_repeat_to_monomer("[*]OC(=O)C(C)[*]", "CC(O)C(=O)O")["relation"] == \
        "condensation_water_loss"


@pytest.mark.parametrize("psmiles,reference", [
    ("[*]OCC[*]", "O=C1CCCCCO1"),            # PEG repeat claimed as caprolactone
    ("[*]CC([*])c1ccccc1", "C1CO1"),          # styrene repeat claimed as ethylene oxide
])
def test_a_genuinely_wrong_structure_is_still_flagged(psmiles, reference):
    result = _compare_repeat_to_monomer(psmiles, reference)
    assert result["relation"] == "mismatch"
    assert result["difference"]


def test_repeat_unit_counts_exclude_the_capping_hydrogens():
    assert _repeat_unit_counts("[*]OCC[*]") == {"O": 1, "H": 4, "C": 2}


def test_unparseable_input_is_reported_as_unknown():
    assert _compare_repeat_to_monomer("[*]OCC[*]", "not-a-smiles")["relation"] == "unknown"


def test_copolymer_mixtures_do_not_produce_a_warning():
    from biologix_ai.material_mappings import _apply_pubchem_similarity

    out = {"ok": True, "pubchem_smiles": "CC(C(=O)O)O.C(C(=O)O)O"}
    _apply_pubchem_similarity(out, "[*]OC(=O)COC(=O)C(C)[*]")
    assert out["composition_check"]["relation"] == "reference_is_a_mixture"
    assert "warning" not in out


def test_a_real_mismatch_still_warns_through_the_public_path():
    from biologix_ai.material_mappings import _apply_pubchem_similarity

    out = {"ok": True, "pubchem_smiles": "O=C1CCCCCO1"}
    _apply_pubchem_similarity(out, "[*]OCC[*]")
    assert "may not represent this material" in out["warning"]
