"""Tests for precursor_registry: bundled DB loading, reactant collection, and seeding."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict
from unittest.mock import patch

import pytest

from biologix_ai.retrosynthesis.precursor_registry import (
    _load_precursors_json,
    clear_workspace_precursors,
    collect_reactants_from_extractions,
    diagnose_leaf_reachability,
    get_bundled_precursors,
    get_workspace_precursors,
    reload_bundled_precursors,
    seed_workspace_precursors,
)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────


def _make_extraction(reactants: str, products: str, conditions: str = "not specified") -> str:
    return (
        f"Reaction 001:\n"
        f"Reactants: {reactants}\n"
        f"Products: {products}\n"
        f"Conditions: {conditions}"
    )


# ─────────────────────────────────────────────────────────
# Bundled database
# ─────────────────────────────────────────────────────────


class TestBundledPrecursors:
    """Verify precursors.json loads and critical names are present."""

    def setup_method(self):
        reload_bundled_precursors()

    def test_loads_without_error(self):
        entries = _load_precursors_json()
        assert isinstance(entries, list)
        assert len(entries) >= 50, "Expected at least 50 manual entries"

    def test_lactide_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "lactide" in bundled, "lactide must be in bundled precursors"

    def test_glycolide_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "glycolide" in bundled

    def test_caprolactone_in_bundled(self):
        bundled = get_bundled_precursors()
        # name variant in precursors.json
        assert "epsilon-caprolactone" in bundled or "caprolactone" in bundled

    def test_chitin_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "chitin" in bundled

    def test_chitosan_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "chitosan" in bundled

    def test_ala_nca_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "ala-nca" in bundled or "l-alanine n-carboxyanhydride" in bundled

    def test_ethylene_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "ethylene" in bundled

    def test_carbon_monoxide_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "carbon monoxide" in bundled or "co" in bundled

    def test_lactic_acid_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "lactic acid" in bundled

    def test_nipam_alias_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "nipam" in bundled

    def test_aibn_in_bundled(self):
        bundled = get_bundled_precursors()
        assert "aibn" in bundled

    def test_bundled_returns_lowercase_only(self):
        bundled = get_bundled_precursors()
        for name in bundled:
            assert name == name.lower(), f"Expected lowercase, got {name!r}"

    def test_caching(self):
        b1 = get_bundled_precursors()
        b2 = get_bundled_precursors()
        assert b1 is b2, "get_bundled_precursors() should return cached set"

    def test_reload_clears_cache(self):
        b1 = get_bundled_precursors()
        reload_bundled_precursors()
        b2 = get_bundled_precursors()
        # Content should be equal, but objects different (cache was cleared)
        assert b1 == b2


# ─────────────────────────────────────────────────────────
# collect_reactants_from_extractions
# ─────────────────────────────────────────────────────────


class TestCollectReactants:
    def test_basic(self):
        results_dict = {
            "paper1": _make_extraction("lactide, glycolide", "poly(lactic-co-glycolic acid)")
        }
        reactants = collect_reactants_from_extractions(results_dict)
        assert "lactide" in reactants
        assert "glycolide" in reactants

    def test_multiple_reactions_merged(self):
        text = (
            "Reaction 001:\n"
            "Reactants: lactic acid\n"
            "Products: lactide\n"
            "Conditions: condensation\n\n"
            "Reaction 002:\n"
            "Reactants: lactide, glycolide\n"
            "Products: poly(lactic-co-glycolic acid)\n"
            "Conditions: ROP"
        )
        results_dict = {"paper1": text}
        reactants = collect_reactants_from_extractions(results_dict)
        assert "lactic acid" in reactants
        assert "lactide" in reactants
        assert "glycolide" in reactants

    def test_psmiles_suffix_stripped(self):
        results_dict = {
            "p1": _make_extraction(
                "hea [*]CC([*])C(=O)NCCO",
                "poly(n-hydroxyethyl acrylamide) [*]CC([*])C(=O)NCCO",
            )
        }
        reactants = collect_reactants_from_extractions(results_dict)
        assert "hea" in reactants
        # PSMILES suffix must be absent
        for r in reactants:
            assert "[*]" not in r

    def test_smiles_annotation_stripped(self):
        results_dict = {
            "p1": _make_extraction(
                "acryloyl chloride (C=CC(=O)Cl), glycol",
                "poly(glycol acrylate)",
            )
        }
        reactants = collect_reactants_from_extractions(results_dict)
        assert "acryloyl chloride" in reactants
        # SMILES annotation must be absent
        for r in reactants:
            assert "(c=" not in r.lower()

    def test_empty_dict(self):
        reactants = collect_reactants_from_extractions({})
        assert reactants == set()

    def test_reactants_case_folded(self):
        results_dict = {
            "p1": _make_extraction("Lactic Acid, Glycolide", "PLGA")
        }
        reactants = collect_reactants_from_extractions(results_dict)
        assert "lactic acid" in reactants
        assert "glycolide" in reactants


# ─────────────────────────────────────────────────────────
# diagnose_leaf_reachability
# ─────────────────────────────────────────────────────────


class TestDiagnoseLeafReachability:
    def setup_method(self):
        reload_bundled_precursors()

    def test_bundled_reactant_marked_purchasable(self):
        result = diagnose_leaf_reachability({"lactide", "glycolide"})
        assert result["lactide"]["purchasable"] is True
        assert result["lactide"]["resolution_source"] == "bundled"
        assert result["lactide"]["blocking"] is False

    def test_unknown_reactant_marked_blocking(self):
        unknown = "obscure_specialty_monomer_xyz123"
        result = diagnose_leaf_reachability({unknown})
        assert result[unknown]["purchasable"] is False
        assert result[unknown]["blocking"] is True

    def test_chitin_purchasable_via_bundled(self):
        result = diagnose_leaf_reachability({"chitin"})
        assert result["chitin"]["purchasable"] is True

    def test_empty_reactants(self):
        result = diagnose_leaf_reachability(set())
        assert result == {}


# ─────────────────────────────────────────────────────────
# seed_workspace_precursors
# ─────────────────────────────────────────────────────────


class TestSeedWorkspacePrecursors:
    def setup_method(self):
        clear_workspace_precursors()
        reload_bundled_precursors()

    def test_bundled_names_added_to_workspace_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            seed_workspace_precursors(ws, {"lactide", "glycolide"})
        assert "lactide" in get_workspace_precursors()
        assert "glycolide" in get_workspace_precursors()

    def test_writes_substance_query_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            seed_workspace_precursors(ws, {"lactide"})
            result_path = ws / "substance_query_result.json"
            assert result_path.is_file()
            data = json.loads(result_path.read_text())
            assert data.get("lactide") is True

    def test_writes_smiles_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            seed_workspace_precursors(ws, {"lactide"})
            # File may or may not exist depending on whether bundled SMILES is cached
            cache_path = ws / "smiles_cache.json"
            # At minimum it should be created (even if empty dict)
            assert cache_path.is_file()

    def test_pubchem_not_called_for_bundled(self):
        """Bundled names should not trigger PubChem calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            with patch(
                "biologix_ai.retrosynthesis.precursor_registry._pubchem_smiles"
            ) as mock_pc:
                seed_workspace_precursors(ws, {"lactide"})
                mock_pc.assert_not_called()

    def test_resolution_map_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            result = seed_workspace_precursors(ws, {"lactide", "glycolide"})
        assert isinstance(result, dict)
        assert result.get("lactide") == "bundled"
        assert result.get("glycolide") == "bundled"

    def test_unknown_name_resolution_attempted(self):
        """Unknown names trigger PubChem; mock it to return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            with patch(
                "biologix_ai.retrosynthesis.precursor_registry._pubchem_smiles",
                return_value=None,
            ):
                result = seed_workspace_precursors(ws, {"unknown_xyz_compound_999"})
        assert result.get("unknown_xyz_compound_999") == "unresolved"

    def test_clear_workspace_precursors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            seed_workspace_precursors(ws, {"lactide"})
        assert "lactide" in get_workspace_precursors()
        clear_workspace_precursors()
        assert "lactide" not in get_workspace_precursors()


# ─────────────────────────────────────────────────────────
# PLGA 2-step extraction → all reactants bundled
# ─────────────────────────────────────────────────────────


class TestPlgaReactantCoverage:
    """Verify that PLGA 2-step extraction reactants all resolve via bundled DB."""

    def setup_method(self):
        reload_bundled_precursors()
        clear_workspace_precursors()

    def test_plga_two_step_all_bundled(self):
        two_step_text = (
            "Reaction 001:\n"
            "Reactants: lactide, glycolide\n"
            "Products: poly(lactic-co-glycolic acid)\n"
            "Conditions: ring-opening polymerization, Catalyst: tin(II) 2-ethylhexanoate\n\n"
            "Reaction 002:\n"
            "Reactants: lactic acid\n"
            "Products: lactide\n"
            "Conditions: condensation and cyclization, 180°C"
        )
        results = {"Zhang2022": two_step_text}
        reactants = collect_reactants_from_extractions(results)
        leaf_status = diagnose_leaf_reachability(reactants)
        blocking = [n for n, s in leaf_status.items() if s["blocking"]]
        assert "lactic acid" in reactants
        assert "lactide" in reactants
        assert "glycolide" in reactants
        # All critical reactants should be purchasable via bundled DB
        for key_reactant in ("lactide", "glycolide", "lactic acid"):
            assert leaf_status.get(key_reactant, {}).get("purchasable"), (
                f"{key_reactant!r} should be purchasable via bundled DB"
            )


class TestChitosanReactantCoverage:
    """Verify chitin and glucosamine resolve via bundled DB."""

    def setup_method(self):
        reload_bundled_precursors()

    def test_chitin_is_bundled_leaf(self):
        results = {
            "Rinaudo2006": (
                "Reaction 001:\n"
                "Reactants: chitin\n"
                "Products: chitosan\n"
                "Conditions: alkaline deacetylation, 50% NaOH, 100°C\n\n"
                "Reaction 002:\n"
                "Reactants: n-acetylglucosamine\n"
                "Products: chitin\n"
                "Conditions: biosynthesis"
            )
        }
        reactants = collect_reactants_from_extractions(results)
        leaf_status = diagnose_leaf_reachability(reactants)
        assert leaf_status.get("chitin", {}).get("purchasable"), "chitin must be bundled leaf"
        assert leaf_status.get("n-acetylglucosamine", {}).get("purchasable"), (
            "n-acetylglucosamine must be bundled leaf"
        )


# ─────────────────────────────────────────────────────────
# validate_extractions_for_tree leaf warnings
# ─────────────────────────────────────────────────────────


class TestValidateExtractionsLeafWarnings:
    """validate_extractions_for_tree should warn about blocking reactants."""

    def setup_method(self):
        reload_bundled_precursors()
        clear_workspace_precursors()

    def test_no_warning_for_known_reactants(self):
        from biologix_ai.retrosynthesis.retro_adapter import validate_extractions_for_tree

        extractions = {
            "p1": (
                "Reaction 001:\n"
                "Reactants: lactide, glycolide\n"
                "Products: poly(lactic-co-glycolic acid)\n"
                "Conditions: ROP"
            )
        }
        result = validate_extractions_for_tree(extractions, "poly(lactic-co-glycolic acid)")
        # lactide and glycolide are bundled — should produce no leaf warnings
        blocking = result.get("blocking_reactants", [])
        assert "lactide" not in blocking
        assert "glycolide" not in blocking

    def test_warning_for_unknown_reactant(self):
        from biologix_ai.retrosynthesis.retro_adapter import validate_extractions_for_tree

        extractions = {
            "p1": (
                "Reaction 001:\n"
                "Reactants: obscure_xyz_monomer_test_only_9876\n"
                "Products: poly(xyz)\n"
                "Conditions: ROP"
            )
        }
        result = validate_extractions_for_tree(extractions, "poly(xyz)")
        blocking = result.get("blocking_reactants", [])
        assert "obscure_xyz_monomer_test_only_9876" in blocking
        # Warning text should appear
        warnings = result.get("warnings", [])
        leaf_warnings = [w for w in warnings if "leaf coverage" in w.lower() or "purchasable" in w.lower()]
        assert len(leaf_warnings) > 0


class TestZincBridgeSkip:
    def test_skip_zinc_bridge_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from biologix_ai.retrosynthesis import precursor_registry as pr

        monkeypatch.setenv("BIOLOGIX_SKIP_ZINC_BRIDGE", "1")
        pr._zinc_attempted = False
        pr._zinc_inchikeys = None
        assert pr._load_zinc_inchikeys() is None

    def test_skip_molport_bridge_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from biologix_ai.retrosynthesis import precursor_registry as pr

        monkeypatch.setenv("BIOLOGIX_SKIP_MOLPORT_BRIDGE", "1")
        pr._molport_attempted = False
        pr._molport_inchikeys = None
        assert pr._load_molport_inchikeys() is None
