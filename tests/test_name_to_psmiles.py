"""Tests for name → PSMILES pipeline (known lookup + PubChem + auto-conversion)."""

import pytest

from insulin_ai.material_mappings import (
    name_to_psmiles,
    monomer_smiles_to_psmiles,
    _try_known_polymer_lookup,
    _vinyl_smiles_to_psmiles,
    _hydroxy_acid_smiles_to_psmiles,
    _amino_acid_smiles_to_psmiles,
)


class TestKnownPolymerLookup:

    @pytest.mark.parametrize("name,expected", [
        ("PEG", "[*]OCC[*]"),
        ("peg", "[*]OCC[*]"),
        ("polyethylene glycol", "[*]OCC[*]"),
        ("PLA", "[*]OC(=O)C(C)[*]"),
        ("poly(lactic acid)", "[*]OC(=O)C(C)[*]"),
        ("PMMA", "[*]CC([*])(C)C(=O)OC"),
        ("polystyrene", "[*]CC([*])c1ccccc1"),
        ("PDMS", "[*]O[Si](C)(C)[*]"),
        ("chitosan", "[*]OC1C(N)C(O)C(CO)OC1[*]"),
    ])
    def test_known_polymers(self, name, expected):
        result = _try_known_polymer_lookup(name)
        assert result == expected

    def test_unknown_returns_none(self):
        assert _try_known_polymer_lookup("unobtainium_polymer") is None


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

    def test_known_polymer(self):
        r = name_to_psmiles("PEG")
        assert r["ok"] is True
        assert r["psmiles"] == "[*]OCC[*]"
        assert r["source"] == "known_polymer_table"
        assert r["confidence"] == "high"

    def test_known_polymer_case_insensitive(self):
        r = name_to_psmiles("Polylactic Acid")
        assert r["ok"] is True
        assert r["source"] == "known_polymer_table"

    def test_empty_name(self):
        r = name_to_psmiles("")
        assert r["ok"] is False

    @pytest.mark.parametrize("name", [
        "poly(lactic acid)", "PLA", "PCL", "PLGA", "PVA",
        "PMMA", "PS", "PE", "PP", "PVC", "PTFE", "PVDF",
    ])
    def test_common_abbreviations_all_resolve(self, name):
        r = name_to_psmiles(name)
        assert r["ok"] is True, f"{name} should resolve but got: {r}"
        assert r["psmiles"].count("[*]") == 2

    def test_pubchem_lactic_acid(self):
        """Lactic acid → hydroxy-acid condensation PSMILES via PubChem."""
        r = name_to_psmiles("lactic acid")
        if r.get("source") == "known_polymer_table":
            pytest.skip("Resolved via known table")
        if not r["ok"]:
            pytest.skip(f"PubChem unavailable: {r.get('error')}")
        assert r["psmiles"].count("[*]") == 2
        assert r["source"] == "pubchem_auto"

    def test_pubchem_styrene(self):
        """Styrene → vinyl PSMILES via PubChem."""
        r = name_to_psmiles("styrene")
        if not r["ok"]:
            pytest.skip(f"PubChem unavailable: {r.get('error')}")
        assert r["psmiles"].count("[*]") == 2
