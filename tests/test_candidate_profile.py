"""Tests for get_candidate_profile and screen_candidate_library MCP tools."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))


def _import_mcp_server():
    import importlib
    spec = importlib.util.spec_from_file_location(
        "biologix_ai_mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "biologix_ai_mcp_server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["biologix_ai_mcp_server"] = mod
    try:
        from mcp.server.fastmcp import FastMCP
        orig = FastMCP.run
        FastMCP.run = lambda self, *a, **kw: None
    except Exception:
        orig = None
    spec.loader.exec_module(mod)
    if orig is not None:
        FastMCP.run = orig
    return mod


class TestGetCandidateProfile:
    def setup_method(self):
        self.server = _import_mcp_server()

    def test_returns_valid_json(self):
        result = self.server.get_candidate_profile(
            psmiles="[*]OCC[*]",
            biologic_target="insulin",
            run_retro=False,
            run_admet=True,
            run_compliance=True,
        )
        parsed = json.loads(result)
        assert parsed["psmiles"] == "[*]OCC[*]"
        assert "validation" in parsed
        assert "admet" in parsed
        assert "compliance" in parsed

    def test_compliance_peg_approved(self):
        result = self.server.get_candidate_profile(
            psmiles="[*]OCC[*]",
            biologic_target="insulin",
            run_retro=False,
            run_admet=False,
            run_compliance=True,
            jurisdiction="FDA,EMA",
        )
        parsed = json.loads(result)
        comp = parsed.get("compliance", {})
        assert comp.get("overall_status") in ("approved", "flagged")

    def test_retro_included_when_requested(self):
        result = self.server.get_candidate_profile(
            psmiles="[*]OCC[*]",
            biologic_target="insulin",
            run_retro=True,
            run_admet=False,
            run_compliance=False,
        )
        parsed = json.loads(result)
        assert "retrosynthesis" in parsed

    def test_run_dir_writes_audit(self, tmp_path):
        from biologix_ai.discovery_world import ensure_world_for_session
        ensure_world_for_session(tmp_path, objective="unit")
        self.server.get_candidate_profile(
            psmiles="[*]OCC[*]",
            biologic_target="insulin",
            run_retro=False,
            run_admet=True,
            run_compliance=True,
            run_dir=str(tmp_path),
        )
        audit_file = tmp_path / "audit" / "pipeline_audit.jsonl"
        assert audit_file.is_file()
        lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
        assert len(lines) >= 1


class TestScreenCandidateLibrary:
    def setup_method(self):
        self.server = _import_mcp_server()

    def test_returns_list(self):
        result = self.server.screen_candidate_library(
            psmiles_list="[*]OCC[*],[*]CC(O)[*]",
            biologic_target="insulin",
            run_retro=False,
            run_admet=True,
            run_compliance=True,
        )
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_each_has_disposition(self):
        result = self.server.screen_candidate_library(
            psmiles_list="[*]OCC[*]",
            biologic_target="insulin",
            run_retro=False,
        )
        parsed = json.loads(result)
        for item in parsed:
            assert "library_disposition" in item
            assert item["library_disposition"] in ("pass", "warning", "fail")

    def test_max_candidates_respected(self):
        many = ",".join(["[*]OCC[*]"] * 10)
        result = self.server.screen_candidate_library(
            psmiles_list=many,
            max_candidates=3,
        )
        parsed = json.loads(result)
        assert len(parsed) <= 3


class TestNewComplianceMCPTool:
    def setup_method(self):
        self.server = _import_mcp_server()

    def test_check_excipient_compliance_peg(self):
        result = self.server.check_excipient_compliance(
            psmiles="[*]OCC[*]",
            jurisdiction="FDA,EMA",
        )
        parsed = json.loads(result)
        assert "overall_status" in parsed
        assert "psmiles" in parsed

    def test_unknown_smiles_no_match(self):
        result = self.server.check_excipient_compliance(
            psmiles="[*]C(=O)NC([*])(CCCC)C",
            jurisdiction="FDA",
        )
        parsed = json.loads(result)
        assert parsed["overall_status"] in ("no_match", "flagged")


class TestFunnelContextMCPTools:
    def setup_method(self):
        self.server = _import_mcp_server()

    def test_save_and_get_funnel(self, tmp_path):
        data = json.dumps({"top": ["[*]OCC[*]"]})
        save_result = self.server.save_funnel_context(
            stage="post_screening",
            checkpoint_data=data,
            run_dir=str(tmp_path),
        )
        assert json.loads(save_result).get("saved") is True
        get_result = json.loads(self.server.get_funnel_context(
            stage="post_screening",
            run_dir=str(tmp_path),
        ))
        assert get_result["checkpoint"]["stage"] == "post_screening"
        assert "post_screening" in get_result["stages_available"]

    def test_get_empty_session(self, tmp_path):
        result = json.loads(self.server.get_funnel_context(run_dir=str(tmp_path)))
        assert result["checkpoint"] is None


class TestPipelineAuditMCPTools:
    def setup_method(self):
        self.server = _import_mcp_server()

    def test_save_and_retrieve_audit(self, tmp_path):
        self.server.save_pipeline_stage(
            candidate_psmiles="[*]OCC[*]",
            stage="admet",
            disposition="pass",
            detail="safe=True",
            run_dir=str(tmp_path),
        )
        records = json.loads(self.server.get_pipeline_audit(
            candidate_psmiles="[*]OCC[*]",
            run_dir=str(tmp_path),
        ))
        assert len(records) == 1
        assert records[0]["stage"] == "admet"
        assert records[0]["disposition"] == "pass"
