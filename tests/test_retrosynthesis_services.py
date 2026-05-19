"""Tests for the shared service layer: retrosynthesis, toxicity, results compiler."""

import pytest

from insulin_ai.retrosynthesis.models import (
    MonomerInfo,
    MonomerSource,
    PolymerRoute,
    PolymerRetroStep,
    PolymerizationType,
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
    RetrosynthesisResult,
)


class TestRetrosynthesisService:
    def test_plan_returns_result(self):
        from insulin_ai.services.retrosynthesis_service import plan_retrosynthesis

        request = RetrosynthesisRequest(
            target="PEG",
            biologic_target="insulin",
            constraints=RetrosynthesisConstraints(max_routes=2),
        )
        result = plan_retrosynthesis(request)
        assert isinstance(result, RetrosynthesisResult)
        assert result.request.target == "PEG"
        assert isinstance(result.polymer_routes, list)

    def test_plan_with_non_insulin_biologic(self):
        from insulin_ai.services.retrosynthesis_service import plan_retrosynthesis

        request = RetrosynthesisRequest(
            target="[*]OCC[*]",
            biologic_target="adalimumab",
        )
        result = plan_retrosynthesis(request)
        assert result.request.biologic_target == "adalimumab"

    def test_plan_metadata_reports_availability(self):
        from insulin_ai.services.retrosynthesis_service import plan_retrosynthesis

        request = RetrosynthesisRequest(target="test_polymer")
        result = plan_retrosynthesis(request)
        assert "retrosynthesis_agent_available" in result.metadata
        assert "aizynthfinder_available" in result.metadata


class TestToxicityService:
    def test_screen_monomer_returns_result(self):
        from insulin_ai.services.toxicity_service import screen_monomer

        result = screen_monomer("CCO")
        assert result.smiles == "CCO"
        assert isinstance(result.safe, bool)
        assert isinstance(result.warnings, list)

    def test_smarts_detects_acrylamide(self):
        from insulin_ai.services.toxicity_service import _run_smarts_screen

        hits = _run_smarts_screen("C=CC(=O)N")
        rdkit_available = False
        try:
            from rdkit import Chem  # noqa: F401
            rdkit_available = True
        except ImportError:
            pass

        if rdkit_available:
            names = [h.pattern_name for h in hits]
            assert "acrylamide" in names or "michael_acceptor" in names
        else:
            assert hits == []

    def test_batch_screening(self):
        from insulin_ai.services.toxicity_service import screen_monomers_batch

        results = screen_monomers_batch(["CCO", "CC(=O)O"])
        assert len(results) == 2
        assert all(r.smiles in ("CCO", "CC(=O)O") for r in results)


class TestResultsCompiler:
    def _make_retro_result(self) -> RetrosynthesisResult:
        return RetrosynthesisResult(
            request=RetrosynthesisRequest(
                target="test_polymer",
                biologic_target="trastuzumab",
            ),
            polymer_routes=[
                PolymerRoute(
                    target_polymer="test_polymer",
                    polymerization_type=PolymerizationType.RAFT,
                    steps=[PolymerRetroStep(product_name="test_polymer")],
                    monomers=[
                        MonomerInfo(
                            smiles="CCO",
                            name="ethanol",
                            source=MonomerSource.PURCHASABLE,
                        ),
                    ],
                    pathway_score=0.9,
                ),
                PolymerRoute(
                    target_polymer="test_polymer",
                    steps=[
                        PolymerRetroStep(product_name="test_polymer"),
                        PolymerRetroStep(product_name="intermediate"),
                    ],
                    monomers=[
                        MonomerInfo(smiles="C=CC(=O)N", name="acrylamide"),
                    ],
                    pathway_score=0.5,
                ),
            ],
        )

    def test_compile_produces_report(self):
        from insulin_ai.services.results_compiler import compile_results

        retro = self._make_retro_result()
        report = compile_results(retro)
        assert report.biologic_target == "trastuzumab"
        assert len(report.scorecards) == 2
        assert report.scorecards[0].recommended is True

    def test_compile_ranks_by_score(self):
        from insulin_ai.services.results_compiler import compile_results

        retro = self._make_retro_result()
        report = compile_results(retro)
        scores = [s.composite_score for s in report.scorecards]
        assert scores == sorted(scores, reverse=True)

    def test_compile_generates_narrative(self):
        from insulin_ai.services.results_compiler import compile_results

        retro = self._make_retro_result()
        report = compile_results(retro, generate_narrative=True)
        assert "Retrosynthesis Report" in report.narrative
        assert "trastuzumab" in report.narrative.lower()

    def test_compile_next_steps_nonempty(self):
        from insulin_ai.services.results_compiler import compile_results

        retro = self._make_retro_result()
        report = compile_results(retro)
        assert len(report.next_steps) > 0
