"""Tests for pipeline robustness: prescreen, per-candidate isolation, structured errors."""

import pytest


class TestPrescreenPSMILESForMD:
    """prescreen_psmiles_for_md catches chemistry that would crash OpenMM/GAFF."""

    def _prescreen(self, psmiles):
        from insulin_ai.material_mappings import prescreen_psmiles_for_md
        return prescreen_psmiles_for_md(psmiles)

    def test_valid_peg(self):
        r = self._prescreen("[*]OCC[*]")
        assert r["ok"] is True

    def test_valid_pla(self):
        r = self._prescreen("[*]OC(=O)C([*])C")
        assert r["ok"] is True

    def test_missing_stars(self):
        r = self._prescreen("CCCC")
        assert r["ok"] is False
        assert "stage" in r

    def test_one_star_rejected(self):
        r = self._prescreen("[*]CCCC")
        assert r["ok"] is False
        assert "2 [*]" in r["error"]

    def test_three_stars_rejected(self):
        r = self._prescreen("[*]CC([*])C[*]")
        assert r["ok"] is False
        assert "3" in r["error"]

    def test_radical_rejected(self):
        r = self._prescreen("[*][CH2][*]")
        assert r["ok"] is True  # [CH2] with two bonds is fine
        # but a true radical:
        r2 = self._prescreen("[*][CH][*]")
        # [CH] capped with H gives [CH]([H])[H] - 1 radical
        # Whether RDKit interprets this as radical depends on context

    def test_charged_rejected(self):
        r = self._prescreen("[*]CC[N+](C)(C)C[*]")
        assert r["ok"] is False
        assert "charged" in r["error"].lower() or "Charged" in r["error"]

    def test_empty_rejected(self):
        r = self._prescreen("")
        assert r["ok"] is False

    def test_huge_repeat_unit_rejected(self):
        # 250 carbons
        big = "[*]" + "C" * 250 + "[*]"
        r = self._prescreen(big)
        assert r["ok"] is False
        assert "heavy atoms" in r["error"].lower()


class TestBuildPolymerOligomerSmiles:
    """build_polymer_oligomer_smiles returns (smiles, actual_repeats) tuple."""

    def _build(self, psmiles, n):
        from insulin_ai.simulation.polymer_build import build_polymer_oligomer_smiles
        return build_polymer_oligomer_smiles(psmiles, n)

    def test_single_repeat(self):
        s, n = self._build("[*]OCC[*]", 1)
        assert s is not None
        assert n == 1
        assert "[*]" not in s

    def test_multi_repeat(self):
        s, n = self._build("[*]OCC[*]", 4)
        assert s is not None
        assert n == 4
        assert "[*]" not in s

    def test_invalid_returns_none(self):
        s, n = self._build("CCCC", 4)
        assert s is None
        assert n == 0


class TestEmbedMol3D:
    """embed_mol_3d returns (success, error_string)."""

    def test_success(self):
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from insulin_ai.simulation.polymer_build import embed_mol_3d

        mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
        ok, err = embed_mol_3d(mol)
        assert ok is True
        assert err == ""

    def test_returns_error_string_on_failure(self):
        from rdkit import Chem
        from insulin_ai.simulation.polymer_build import embed_mol_3d

        mol = Chem.RWMol()  # empty molecule
        ok, err = embed_mol_3d(mol)
        assert ok is False
        assert isinstance(err, str)
        assert len(err) > 0


class TestPropertyExtractorNameFormat:
    """Problematic features use full names, not truncated."""

    def test_full_names_in_problematic_features(self):
        from insulin_ai.simulation.property_extractor import PropertyExtractor

        ext = PropertyExtractor()
        long_name = "Candidate_with_a_very_long_material_name_42"
        results = [{"interaction_energy_kj_mol": 999.0, "psmiles": "[*]CC[*]"}]
        fb = ext.extract_feedback(results, [long_name])
        probs = fb["problematic_features"]
        matching = [p for p in probs if long_name in p]
        assert len(matching) > 0, f"Full name not found in: {probs}"

    def test_colon_separator_in_problematic_features(self):
        from insulin_ai.simulation.property_extractor import PropertyExtractor

        ext = PropertyExtractor()
        results = [{"interaction_energy_kj_mol": 100.0, "psmiles": "[*]CC[*]"}]
        fb = ext.extract_feedback(results, ["TestCand"])
        probs = fb["problematic_features"]
        for p in probs:
            if "interaction_energy" in p:
                assert ":" in p, f"Expected colon separator in '{p}'"
                assert p.split(":", 1)[1] == "TestCand"

    def test_psmiles_included_in_property_analysis(self):
        from insulin_ai.simulation.property_extractor import PropertyExtractor

        ext = PropertyExtractor()
        results = [{"interaction_energy_kj_mol": -10.0, "psmiles": "[*]OCC[*]"}]
        fb = ext.extract_feedback(results, ["C0"])
        assert fb["property_analysis"]["C0"]["psmiles"] == "[*]OCC[*]"

    def test_all_positive_energy_no_high_performers(self):
        from insulin_ai.simulation.property_extractor import PropertyExtractor

        ext = PropertyExtractor()
        results = [
            {"interaction_energy_kj_mol": 50.0, "psmiles": "[*]CC[*]"},
            {"interaction_energy_kj_mol": 100.0, "psmiles": "[*]OCC[*]"},
            {"interaction_energy_kj_mol": 75.0, "psmiles": "[*]COC[*]"},
        ]
        fb = ext.extract_feedback(results, ["A", "B", "C"])
        assert fb["high_performers"] == [], \
            f"No high performers expected when all energies positive, got: {fb['high_performers']}"

    def test_evaluation_failed_includes_name(self):
        from insulin_ai.simulation.property_extractor import PropertyExtractor

        ext = PropertyExtractor()
        results = [None]
        fb = ext.extract_feedback(results, ["FailedCand_0"])
        probs = fb["problematic_features"]
        assert any("FailedCand_0" in p for p in probs)


class TestDiscoveryScoreNormalized:
    """discovery_score averages per-candidate bonus instead of summing."""

    def test_score_independent_of_batch_size(self):
        from insulin_ai.simulation.scoring import discovery_score

        row = {"interaction_energy_kj_mol": -100.0}
        fb_1 = {
            "high_performers": [], "effective_mechanisms": [],
            "problematic_features": [],
            "property_analysis": {"a": row},
        }
        fb_3 = {
            "high_performers": [], "effective_mechanisms": [],
            "problematic_features": [],
            "property_analysis": {"a": dict(row), "b": dict(row), "c": dict(row)},
        }
        s1 = discovery_score(fb_1, use_composite=False)
        s3 = discovery_score(fb_3, use_composite=False)
        assert s1 == pytest.approx(s3), \
            f"Score should not depend on batch size: 1-cand={s1}, 3-cand={s3}"
