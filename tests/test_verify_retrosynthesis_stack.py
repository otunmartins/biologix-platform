"""Tests for the full RetroSynAgent and AiZynthFinder smoke validator."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from scripts import verify_retrosynthesis_stack as verifier
from scripts.verify_retrosynthesis_stack import (
    RetrosynthesisStackError,
    RetrosynthesisStackReport,
    prime_offline_tree_cache,
    validate_retrosynthesis_result,
)


def _successful_result() -> dict:
    return {
        "errors": [],
        "polymer_routes": [
            {
                "monomers": [
                    {
                        "smiles": "C=CC(=O)NCCO",
                        "synthesis_route": {"is_solved": True},
                    }
                ]
            }
        ],
        "metadata": {
            "retrosynthesis_agent_available": True,
            "aizynthfinder_available": True,
            "aizynthfinder_models_ready": True,
            "route_provenance": "session_agent_llm",
            "aizynth_monomers_attempted": 1,
            "aizynth_monomers_solved": 1,
        },
    }


def test_validate_retrosynthesis_result_accepts_complete_route() -> None:
    report = validate_retrosynthesis_result(_successful_result())

    assert report.aizynth_monomers_attempted == 1
    assert report.aizynth_monomers_solved == 1
    assert report.polymer_routes == 1


def test_validate_retrosynthesis_result_rejects_skipped_aizynth() -> None:
    result = _successful_result()
    result["metadata"]["aizynth_monomers_attempted"] = 0
    result["metadata"]["aizynth_monomers_solved"] = 0
    result["polymer_routes"][0]["monomers"][0]["synthesis_route"] = None

    with pytest.raises(RetrosynthesisStackError) as error_info:
        validate_retrosynthesis_result(result)

    message = str(error_info.value)
    assert "AiZynthFinder attempted no monomers" in message
    assert "No solved AiZynthFinder monomer route" in message


def test_main_stdout_contains_only_machine_readable_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def noisy_verification() -> RetrosynthesisStackReport:
        print("RetroSynAgent progress")
        return RetrosynthesisStackReport(
            polymer_routes=1,
            aizynth_monomers_attempted=1,
            aizynth_monomers_solved=1,
        )

    standard_output = io.StringIO()
    standard_error = io.StringIO()
    monkeypatch.setattr(verifier, "verify_retrosynthesis_stack", noisy_verification)
    monkeypatch.setattr(sys, "stdout", standard_output)
    monkeypatch.setattr(sys, "stderr", standard_error)

    verifier.main()

    assert json.loads(standard_output.getvalue())["ok"] is True
    assert "RetroSynAgent progress" not in standard_output.getvalue()
    assert "RetroSynAgent progress" in standard_error.getvalue()


def test_prime_offline_tree_cache_blocks_root_network_lookup(
    tmp_path: Path,
) -> None:
    prime_offline_tree_cache(
        workspace=tmp_path,
        root_name="poly(example)",
        monomer_name="example monomer",
        monomer_smiles="CCO",
    )

    substance_cache = json.loads(
        (tmp_path / "substance_query_result.json").read_text(encoding="utf-8")
    )
    smiles_cache = json.loads(
        (tmp_path / "smiles_cache.json").read_text(encoding="utf-8")
    )
    assert substance_cache == {
        "poly(example)": False,
        "example monomer": True,
    }
    assert smiles_cache == {
        "poly(example)": "poly(example)",
        "example monomer": "CCO",
    }
