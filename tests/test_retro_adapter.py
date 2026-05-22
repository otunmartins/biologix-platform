"""Tests for retro_adapter extraction normalization and validation."""

import json
import pytest

from biologix_ai.retrosynthesis.retro_adapter import (
    infer_polymer_name_from_extractions,
    normalize_extractions,
    normalize_for_tree_root,
    normalize_reaction_text,
    require_root_product_in_extractions,
    resolve_material_name,
    validate_extractions_for_tree,
    write_llm_res,
)


class TestNormalizeReactionText:
    def test_lowercase_fields_normalized(self):
        text = (
            "Reaction 001:\n"
            "reactants: acrylic acid\n"
            "products: poly(acrylic acid)\n"
            "conditions: RAFT, 70°C"
        )
        out, stats = normalize_reaction_text(text)
        assert "Reactants: acrylic acid" in out
        assert "Products: poly(acrylic acid)" in out
        assert "Conditions: RAFT, 70°C" in out
        assert stats["reactions_out"] == 1

    def test_missing_conditions_filled(self):
        text = (
            "Reaction 001:\n"
            "Reactants: acrylic acid\n"
            "Products: poly(acrylic acid)"
        )
        out, stats = normalize_reaction_text(text)
        assert "Conditions: not specified" in out
        assert stats["blocks_missing_conditions"] == 1
        assert stats["reactions_out"] == 1

    def test_multi_reaction_paper(self):
        text = (
            "Reaction 001:\n"
            "Reactants: a\n"
            "Products: b\n"
            "Conditions: c\n\n"
            "Reaction 002:\n"
            "Reactants: b\n"
            "Products: poly(lactic acid)\n"
        )
        out, stats = normalize_reaction_text(text)
        assert stats["reactions_in"] == 2
        assert stats["reactions_out"] == 2
        assert stats["blocks_missing_conditions"] == 1
        assert "Conditions: not specified" in out


class TestRequireRootProduct:
    def test_rejects_missing_root_product(self):
        extractions = {
            "paper": (
                "Reaction 001:\n"
                "Reactants: a\n"
                "Products: b\n"
                "Conditions: c"
            ),
        }
        with pytest.raises(ValueError, match="Products containing"):
            require_root_product_in_extractions(extractions, "poly(acrylic acid)")

    def test_accepts_when_root_in_products(self):
        extractions = {
            "paper": (
                "Reaction 001:\n"
                "Reactants: acrylic acid\n"
                "Products: poly(acrylic acid)\n"
                "Conditions: RAFT"
            ),
        }
        require_root_product_in_extractions(extractions, "poly(acrylic acid)")


class TestNormalizeExtractions:
    def test_normalizes_all_papers(self):
        raw = {
            "p1": (
                "Reaction 001:\n"
                "reactants: acrylic acid\n"
                "products: poly(acrylic acid)"
            ),
        }
        out = normalize_extractions(raw)
        assert "Conditions: not specified" in out["p1"]
        assert "Reactants:" in out["p1"]


class TestWriteLlmRes:
    def test_write_rejects_missing_root(self, tmp_path):
        with pytest.raises(ValueError, match="Products containing"):
            write_llm_res(
                tmp_path,
                "poly(acrylic acid)",
                {"paper": "Reaction 001:\nReactants: a\nProducts: b\nConditions: c"},
            )

    def test_write_succeeds_with_valid_extraction(self, tmp_path):
        extractions = {
            "test_paper": (
                "Reaction 001:\n"
                "Reactants: acrylic acid\n"
                "Products: poly(acrylic acid)\n"
                "Conditions: RAFT, 70°C"
            ),
        }
        llm_path, stats = write_llm_res(tmp_path, "poly(acrylic acid)", extractions)
        assert llm_path.is_file()
        loaded = json.loads(llm_path.read_text(encoding="utf-8"))
        assert "test_paper" in loaded
        assert stats["reactions_out"] >= 1

    def test_validate_no_connector_fields(self, tmp_path):
        extractions = {
            "paper": (
                "Reaction 001:\n"
                "Reactants: acrylic acid\n"
                "Products: poly(acrylic acid)\n"
                "Conditions: RAFT"
            ),
        }
        val = validate_extractions_for_tree(extractions, "poly(acrylic acid)")
        assert val["root_product_found"] is True
        assert "used_root_connector" not in val


class TestResolveMaterialName:
    def test_prefers_agent_provided_name_over_psmiles(self):
        name = resolve_material_name(
            "[*]CC([*])C(=O)NCCO",
            agent_provided_name="poly(N-hydroxyethyl acrylamide)",
        )
        assert name == "poly(N-hydroxyethyl acrylamide)"

    def test_uses_mapping_when_no_agent_name(self):
        name = resolve_material_name("[*]CC([*])O")
        assert name == "poly(vinyl alcohol)"


class TestNormalizeForTreeRoot:
    def test_strips_psmiles_suffix_from_products(self):
        text = (
            "Reaction 002:\n"
            "Reactants: N-hydroxyethyl acrylamide\n"
            "Products: poly(N-hydroxyethyl acrylamide) [*]CC([*])C(=O)NCCO\n"
            "Conditions: RAFT"
        )
        out = normalize_for_tree_root(text, "poly(N-hydroxyethyl acrylamide)")
        assert "Products: poly(N-hydroxyethyl acrylamide)" in out
        assert "[*]" not in out.split("Products:")[1]

    def test_strips_smiles_annotations_from_reactants(self):
        text = (
            "Reaction 001:\n"
            "Reactants: acryloyl chloride (C=CC(=O)Cl), ethanolamine (NCCO)\n"
            "Products: N-hydroxyethyl acrylamide\n"
            "Conditions: Schotten-Baumann"
        )
        out = normalize_for_tree_root(text, "poly(N-hydroxyethyl acrylamide)")
        assert "acryloyl chloride" in out
        assert "C=CC(=O)Cl" not in out
        assert "ethanolamine" in out

    def test_infer_polymer_name_from_extractions(self):
        extractions = {
            "paper": (
                "Reaction 002:\n"
                "Reactants: monomer\n"
                "Products: poly(N-hydroxyethyl acrylamide) [*]CC([*])C(=O)NCCO\n"
                "Conditions: RAFT"
            ),
        }
        inferred = infer_polymer_name_from_extractions(
            extractions, "[*]CC([*])C(=O)NCCO"
        )
        assert inferred == "poly(N-hydroxyethyl acrylamide)"
