"""Tests for retrosynthesis markdown reporting and cached plans."""

import json
from pathlib import Path

import pytest

from biologix_ai.retrosynthesis.models import (
    MonomerInfo,
    MonomerSource,
    PolymerRoute,
    PolymerRetroStep,
    PolymerizationType,
    RetrosynthesisRequest,
    RetrosynthesisResult,
)
from biologix_ai.retrosynthesis.retro_adapter import (
    resolve_material_name,
    write_llm_res,
)
from biologix_ai.retrosynthesis.retro_report import (
    assemble_session_retrosynthesis_markdown,
    format_plan_result_markdown,
    load_cached_plan_artifact,
)
from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target


class TestPolyacrylamideResolution:
    def test_psmiles_resolves_to_name(self):
        r = resolve_retro_target("[*]CC([*])C(=O)N")
        assert r["material_name"] in ("polyacrylamide", "poly(acrylamide)")


class TestSubmitValidation:
    def test_rejects_missing_root_product(self):
        from biologix_ai.retrosynthesis.retro_adapter import (
            require_root_product_in_extractions,
        )

        extractions = {
            "paper": "Reaction 001:\nReactants: a\nProducts: b\nConditions: c",
        }
        with pytest.raises(ValueError, match="Products containing"):
            require_root_product_in_extractions(extractions, "poly(acrylic acid)")


class TestRetroReportMarkdown:
    def test_format_plan_includes_steps_and_provenance(self):
        wrapper = {
            "target": "[*]CC([*])C(=O)O",
            "result": RetrosynthesisResult(
                request=RetrosynthesisRequest(target="[*]CC([*])C(=O)O"),
                polymer_routes=[
                    PolymerRoute(
                        target_polymer="poly(acrylic acid)",
                        polymerization_type=PolymerizationType.FREE_RADICAL,
                        steps=[
                            PolymerRetroStep(
                                reactant_names=["acrylic acid"],
                                product_name="poly(acrylic acid)",
                                conditions="RAFT, 70C",
                                literature_source="test",
                            ),
                        ],
                        monomers=[
                            MonomerInfo(
                                smiles="C=CC(=O)O",
                                name="acrylic acid",
                                source=MonomerSource.NEEDS_SYNTHESIS,
                            ),
                        ],
                        recommended=True,
                    ),
                ],
                metadata={
                    "route_provenance": "template",
                    "retro_stages_completed": ["template_fallback", "aizynth_monomer"],
                    "aizynth_monomers_attempted": 1,
                    "reporting_honesty": "Provenance: template (curated fallback); not literature KG.",
                },
            ).model_dump(),
        }
        md = format_plan_result_markdown(wrapper)
        assert "route_provenance" in md
        assert "template" in md
        assert "acrylic acid" in md
        assert "Provenance: template" in md

    def test_assemble_session_from_fixture(self, tmp_path):
        retro = tmp_path / "retrosynthesis"
        retro.mkdir(parents=True)
        plan = {
            "target": "[*]CC([*])C(=O)O",
            "biologic_target": "insulin",
            "result": {
                "request": {"target": "[*]CC([*])C(=O)O", "biologic_target": "insulin"},
                "polymer_routes": [
                    {
                        "target_polymer": "poly(acrylic acid)",
                        "polymerization_type": "free_radical",
                        "steps": [],
                        "monomers": [],
                        "pathway_score": 0.5,
                        "recommended": True,
                    }
                ],
                "warnings": [],
                "metadata": {"route_provenance": "template"},
            },
        }
        (retro / "plan_test.json").write_text(json.dumps(plan), encoding="utf-8")
        md = assemble_session_retrosynthesis_markdown(tmp_path)
        assert "## Retrosynthesis" in md
        assert "poly(acrylic acid)" in md or "CC([*])" in md


class TestLoadCachedPlan:
    def test_load_cached_plan_artifact(self, tmp_path):
        from biologix_ai.services.retrosynthesis_service import load_cached_plan_result

        result = RetrosynthesisResult(
            request=RetrosynthesisRequest(
                target="[*]CC([*])C(=O)O",
                session_dir=str(tmp_path),
            ),
            metadata={"route_provenance": "template"},
        )
        retro = tmp_path / "retrosynthesis"
        retro.mkdir(parents=True)
        (retro / "plan_fixture.json").write_text(
            json.dumps(
                {
                    "target": "[*]CC([*])C(=O)O",
                    "biologic_target": "insulin",
                    "result": result.model_dump(),
                }
            ),
            encoding="utf-8",
        )

        loaded = load_cached_plan_result(tmp_path, "[*]CC([*])C(=O)O")
        assert loaded is not None
        assert loaded.metadata.get("route_provenance") == "template"

        artifact = load_cached_plan_artifact(tmp_path, "[*]CC([*])C(=O)O")
        assert artifact is not None
