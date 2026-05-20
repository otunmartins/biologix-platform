"""Tests for retrosynthesis target resolution and adapters."""

import json
from pathlib import Path

import pytest


class TestResolveRetroTarget:
    def test_psmiles_resolves_to_material_name(self):
        from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target

        r = resolve_retro_target("[*]CC([*])C(=O)O")
        assert r["material_name"] == "poly(acrylic acid)"
        assert r["monomer_smiles"]

    def test_name_resolves_to_psmiles(self):
        from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target

        r = resolve_retro_target("poly(vinyl alcohol)")
        assert "[*]" in r["psmiles"]
        assert r["material_name"] == "poly(vinyl alcohol)"


class TestRetroAdapter:
    def test_write_and_read_llm_res(self, tmp_path):
        from biologix_ai.retrosynthesis.retro_adapter import (
            normalize_extractions,
            session_has_extractions,
            write_llm_res,
        )

        extractions = {
            "test_paper": (
                "Reaction 001:\n"
                "Reactants: acrylic acid\n"
                "Products: poly(acrylic acid)\n"
                "Conditions: RAFT, 70°C"
            ),
        }
        data = normalize_extractions(extractions)
        path, used = write_llm_res(tmp_path, "poly(acrylic acid)", data)
        assert path.is_file()
        assert isinstance(used, bool)
        assert session_has_extractions(tmp_path, "poly(acrylic acid)")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert "test_paper" in loaded


class TestPolymerTemplates:
    def test_lookup_paa_template(self):
        from biologix_ai.retrosynthesis.polymer_templates import lookup_template

        route = lookup_template("[*]CC([*])C(=O)O")
        assert route is not None
        assert route.steps
        assert route.monomers[0].smiles == "C=CC(=O)O"
