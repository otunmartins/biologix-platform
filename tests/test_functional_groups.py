"""Tests for PSMILES functional-group annotation, name-structure consistency, and PubChem lookup."""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))

from insulin_ai.material_mappings import (  # noqa: E402
    annotate_functional_groups,
    check_name_structure_consistency,
    clear_pubchem_lookup_cache,
    lookup_monomer_pubchem,
    morgan_fingerprint_bit_vect,
    _strip_poly_prefix,
    _tanimoto_similarity,
)


# ---------------------------------------------------------------------------
# Layer 1: annotate_functional_groups
# ---------------------------------------------------------------------------
class TestAnnotateFunctionalGroups:
    def test_dialdehyde_psmiles_has_ketone_no_acid(self):
        """The wrong 'poly(glutaric acid)' PSMILES is actually a diketone (CH3-capped)."""
        r = annotate_functional_groups("[*]C(=O)CCC([*])=O")
        assert r["ok"] is True
        g = r["groups"]
        assert g["carboxylic_acid"] == 0
        assert g["ester"] == 0
        assert g["ketone"] == 2

    def test_correct_polyester_has_ester(self):
        """A correct poly(glutaric acid) polyester has ester linkages."""
        r = annotate_functional_groups("[*]OC(=O)CCC(=O)O[*]")
        assert r["ok"] is True
        assert r["groups"]["ester"] == 2

    def test_peg_has_ether(self):
        """PEG repeat unit detected as ether with CH3 capping."""
        r = annotate_functional_groups("[*]OCC[*]")
        assert r["ok"] is True
        assert r["groups"]["ether"] >= 1
        assert r["groups"]["hydroxyl"] == 0

    def test_pva_has_hydroxyl(self):
        r = annotate_functional_groups("[*]CC(O)[*]")
        assert r["ok"] is True
        assert r["groups"]["hydroxyl"] >= 1

    def test_amide_detected(self):
        r = annotate_functional_groups("[*]NC(=O)[*]")
        assert r["ok"] is True
        assert r["groups"]["amide"] >= 1

    def test_amine_detected(self):
        r = annotate_functional_groups("[*]CC(O)C(O)C(O)C([*])N")
        assert r["ok"] is True
        assert r["groups"]["amine"] >= 1

    def test_aromatic_detected(self):
        r = annotate_functional_groups("[*]c1ccc(cc1)[*]")
        assert r["ok"] is True
        assert r["groups"]["aromatic"] >= 1

    def test_carbonate_detected(self):
        r = annotate_functional_groups("[*]OC(=O)O[*]")
        assert r["ok"] is True
        assert r["groups"]["carbonate"] >= 1

    def test_invalid_smiles_returns_error(self):
        r = annotate_functional_groups("[*]XYZ[*]")
        assert r["ok"] is False
        assert "error" in r

    def test_carboxylic_acid_pendant(self):
        """Polyacrylic acid has pendant COOH."""
        r = annotate_functional_groups("[*]CC(C(=O)O)[*]")
        assert r["ok"] is True
        assert r["groups"]["carboxylic_acid"] >= 1


# ---------------------------------------------------------------------------
# Layer 2: check_name_structure_consistency
# ---------------------------------------------------------------------------
class TestNameStructureConsistency:
    def test_acid_vs_diketone_fails(self):
        r = check_name_structure_consistency("poly(glutaric acid)", "[*]C(=O)CCC([*])=O")
        assert r["consistent"] is False
        assert "carboxylic_acid or ester" in r["missing"]

    def test_acid_with_correct_polyester_passes(self):
        r = check_name_structure_consistency("poly(glutaric acid)", "[*]OC(=O)CCC(=O)O[*]")
        assert r["consistent"] is True

    def test_acid_with_pendant_cooh_passes(self):
        r = check_name_structure_consistency("polyacrylic acid", "[*]CC(C(=O)O)[*]")
        assert r["consistent"] is True

    def test_peg_passes(self):
        r = check_name_structure_consistency("PEG", "[*]OCC[*]")
        assert r["consistent"] is True
        assert r["missing"] == []

    def test_pla_passes(self):
        r = check_name_structure_consistency("PLA", "[*]OC(=O)C(C)[*]")
        assert r["consistent"] is True

    def test_chitosan_passes(self):
        r = check_name_structure_consistency("chitosan", "[*]CC(O)C(O)C(O)C([*])N")
        assert r["consistent"] is True

    def test_amide_label_needs_amide(self):
        r = check_name_structure_consistency("polyamide", "[*]CC[*]")
        assert r["consistent"] is False
        assert "amide" in r["missing"]

    def test_no_name_returns_true(self):
        r = check_name_structure_consistency("", "[*]CC[*]")
        assert r["consistent"] is True

    def test_generic_name_no_rules(self):
        """Trade names with no keyword rules pass with a note."""
        r = check_name_structure_consistency("SuperPolymer X", "[*]CC[*]")
        assert r["consistent"] is True
        assert any("No keyword rules" in w for w in r["warnings"])


# ---------------------------------------------------------------------------
# Layer 3: PubChem monomer lookup
# ---------------------------------------------------------------------------
class TestPubChemLookup:
    def test_strip_poly_prefix(self):
        assert _strip_poly_prefix("poly(glutaric acid)") == "glutaric acid"
        assert _strip_poly_prefix("polyethylene glycol") == "ethylene glycol"
        assert _strip_poly_prefix("PEG") == "PEG"

    def test_morgan_fingerprint_bit_vect_nonzero(self):
        pytest.importorskip("rdkit")
        from rdkit import Chem

        m = Chem.MolFromSmiles("CCO")
        fp = morgan_fingerprint_bit_vect(m, radius=2, n_bits=2048)
        assert fp.GetNumOnBits() > 0

    def test_tanimoto_identical(self):
        sim = _tanimoto_similarity("CCO", "CCO")
        assert sim is not None
        assert sim == 1.0

    def test_tanimoto_different(self):
        sim = _tanimoto_similarity("CCO", "c1ccccc1")
        assert sim is not None
        assert sim < 0.5

    @pytest.mark.slow
    def test_pubchem_cache_recomputes_similarity(self):
        """Second call with same monomer uses cache but similarity depends on PSMILES."""
        clear_pubchem_lookup_cache()
        r1 = lookup_monomer_pubchem("glutaric acid", "[*]OCC[*]")
        r2 = lookup_monomer_pubchem("glutaric acid", "[*]CC[*]")
        assert r1["ok"] and r2["ok"]
        assert r1["pubchem_smiles"] == r2["pubchem_smiles"]
        assert r1.get("similarity") != r2.get("similarity")

    @pytest.mark.slow
    def test_lookup_glutaric_acid_real(self):
        """Live PubChem query for glutaric acid monomer."""
        clear_pubchem_lookup_cache()
        r = lookup_monomer_pubchem("poly(glutaric acid)", "[*]C(=O)CCC([*])=O")
        assert r["ok"] is True
        assert r["monomer_name"] == "glutaric acid"
        assert "C(=O)O" in r["pubchem_smiles"] or "C(O)=O" in r["pubchem_smiles"]
        assert r["similarity"] < 0.5  # bad PSMILES should have low similarity

    @pytest.mark.slow
    def test_lookup_glutaric_acid_correct_psmiles(self):
        """Correct polyester should have higher similarity than wrong one."""
        clear_pubchem_lookup_cache()
        r_bad = lookup_monomer_pubchem("poly(glutaric acid)", "[*]C(=O)CCC([*])=O")
        r_good = lookup_monomer_pubchem("poly(glutaric acid)", "[*]OC(=O)CCC(=O)O[*]")
        assert r_bad["ok"] and r_good["ok"]
        assert r_good["similarity"] > r_bad["similarity"]

    @pytest.mark.slow
    def test_lookup_succinic_acid(self):
        r = lookup_monomer_pubchem("poly(succinic acid)")
        assert r["ok"] is True
        assert r["pubchem_cid"] is not None

    def test_lookup_empty_name(self):
        r = lookup_monomer_pubchem("")
        assert r["ok"] is False

    @pytest.mark.slow
    def test_lookup_unknown_polymer(self):
        r = lookup_monomer_pubchem("poly(xyzzyplughfoo)")
        assert r["ok"] is False


# ---------------------------------------------------------------------------
# MCP integration: validate_psmiles includes new fields
# ---------------------------------------------------------------------------
class TestMCPValidatePSMILES:
    @pytest.fixture(autouse=True)
    def _load_mcp(self):
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "mcp_server", os.path.join(ROOT, "insulin_ai_mcp_server.py")
            )
            self.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.mod)
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"MCP dependencies unavailable: {e}")

    def test_fg_always_present(self):
        out = json.loads(self.mod.validate_psmiles("[*]OCC[*]"))
        assert out.get("valid") is True
        assert "functional_groups" in out
        assert out["functional_groups"]["ether"] >= 1

    def test_name_consistency_present_when_name_given(self):
        out = json.loads(
            self.mod.validate_psmiles("[*]C(=O)CCC([*])=O", material_name="poly(glutaric acid)")
        )
        assert "name_consistency" in out
        assert out["name_consistency"]["consistent"] is False

    def test_pubchem_lookup_present_when_name_given(self):
        out = json.loads(
            self.mod.validate_psmiles("[*]OCC[*]", material_name="PEG")
        )
        assert "pubchem_lookup" in out

    def test_no_name_no_consistency(self):
        out = json.loads(self.mod.validate_psmiles("[*]CC[*]"))
        assert "name_consistency" not in out
        assert "pubchem_lookup" not in out
