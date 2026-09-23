"""Tests for name handling (model-authored PSMILES) and monomer auto-conversion helpers."""

import json

import pytest

from biologix_ai.material_mappings import (
    name_to_psmiles,
    monomer_smiles_to_psmiles,
    _vinyl_smiles_to_psmiles,
    _hydroxy_acid_smiles_to_psmiles,
    _amino_acid_smiles_to_psmiles,
)


class TestVinylConversion:

    def test_ethylene(self):
        r = _vinyl_smiles_to_psmiles("C=C")
        assert r is not None
        assert r.count("[*]") == 2

    def test_styrene(self):
        r = _vinyl_smiles_to_psmiles("C=Cc1ccccc1")
        assert r is not None
        assert r.count("[*]") == 2
        assert "c1ccccc1" in r

    def test_vinyl_chloride(self):
        r = _vinyl_smiles_to_psmiles("C=CCl")
        assert r is not None
        assert "Cl" in r

    def test_acrylic_acid(self):
        r = _vinyl_smiles_to_psmiles("C=CC(=O)O")
        assert r is not None
        assert r.count("[*]") == 2

    def test_no_double_bond_returns_none(self):
        assert _vinyl_smiles_to_psmiles("CCCC") is None

    def test_aromatic_only_returns_none(self):
        assert _vinyl_smiles_to_psmiles("c1ccccc1") is None


class TestHydroxyAcidConversion:

    def test_lactic_acid(self):
        r = _hydroxy_acid_smiles_to_psmiles("CC(O)C(=O)O")
        assert r is not None
        assert r.count("[*]") == 2

    def test_glycolic_acid(self):
        r = _hydroxy_acid_smiles_to_psmiles("OCC(=O)O")
        assert r is not None
        assert r.count("[*]") == 2

    def test_no_alcohol_returns_none(self):
        assert _hydroxy_acid_smiles_to_psmiles("CC(=O)O") is None

    def test_no_acid_returns_none(self):
        assert _hydroxy_acid_smiles_to_psmiles("CCCO") is None


class TestAminoAcidConversion:

    def test_glycine(self):
        r = _amino_acid_smiles_to_psmiles("NCC(=O)O")
        assert r is not None
        assert r.count("[*]") == 2

    def test_alanine(self):
        r = _amino_acid_smiles_to_psmiles("CC(N)C(=O)O")
        assert r is not None
        assert r.count("[*]") == 2

    def test_no_amine_returns_none(self):
        assert _amino_acid_smiles_to_psmiles("CC(=O)O") is None


class TestMonomerSmilesToPSMILES:

    def test_auto_vinyl(self):
        r = monomer_smiles_to_psmiles("C=Cc1ccccc1")
        assert r["ok"] is True
        assert r["mechanism"] == "vinyl"

    def test_auto_hydroxy_acid(self):
        r = monomer_smiles_to_psmiles("CC(O)C(=O)O")
        assert r["ok"] is True
        assert "condensation" in r["mechanism"]

    def test_saturated_no_functional_groups(self):
        r = monomer_smiles_to_psmiles("CCCCCC")
        assert r["ok"] is False

    def test_explicit_mechanism(self):
        r = monomer_smiles_to_psmiles("C=CC(=O)O", mechanism="vinyl")
        assert r["ok"] is True
        assert r["mechanism"] == "vinyl"


class TestNameToPSMILES:

    @pytest.mark.parametrize("name", [
        "PEG", "chitosan", "Polylactic Acid", "PLGA", "styrene", "lactic acid",
    ])
    def test_names_never_yield_a_structure(self, name):
        r = name_to_psmiles(name)
        assert r["ok"] is False
        assert r["source"] == "model_required"
        assert "psmiles" not in r
        assert r["material_name"] == name
        assert "validate_psmiles" in " ".join(r["hints"])

    def test_hints_carry_no_canned_repeat_unit(self):
        serialized = json.dumps(name_to_psmiles("chitosan"))
        for canned in ("[*]OCC[*]", "OC1C(N)", "pubchem_smiles"):
            assert canned not in serialized

    def test_empty_name(self):
        r = name_to_psmiles("")
        assert r["ok"] is False
