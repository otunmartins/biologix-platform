"""Tests for pipeline audit trail service."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))

from biologix_ai.services.pipeline_audit import save_pipeline_stage, get_pipeline_audit


PSMILES_A = "[*]OCC[*]"
PSMILES_B = "[*]CC(O)[*]"


def test_save_and_retrieve(tmp_path):
    record = save_pipeline_stage(tmp_path, PSMILES_A, "admet", "pass", "alerts=0")
    assert record["disposition"] == "pass"
    assert "audit_id" in record
    records = get_pipeline_audit(tmp_path)
    assert len(records) == 1
    assert records[0]["candidate_psmiles"] == PSMILES_A


def test_append_only(tmp_path):
    save_pipeline_stage(tmp_path, PSMILES_A, "validation", "pass")
    save_pipeline_stage(tmp_path, PSMILES_A, "admet", "fail", "alert=acrylamide")
    records = get_pipeline_audit(tmp_path, candidate_psmiles=PSMILES_A)
    assert len(records) == 2
    stages = [r["stage"] for r in records]
    assert "validation" in stages
    assert "admet" in stages


def test_filter_by_candidate(tmp_path):
    save_pipeline_stage(tmp_path, PSMILES_A, "admet", "pass")
    save_pipeline_stage(tmp_path, PSMILES_B, "admet", "fail")
    records_a = get_pipeline_audit(tmp_path, candidate_psmiles=PSMILES_A)
    assert all(r["candidate_psmiles"] == PSMILES_A for r in records_a)
    assert len(records_a) == 1


def test_empty_session_returns_empty(tmp_path):
    assert get_pipeline_audit(tmp_path) == []


def test_disposition_warning_default(tmp_path):
    record = save_pipeline_stage(tmp_path, PSMILES_A, "compliance", "invalid_disp")
    assert record["disposition"] == "warning"


def test_audit_file_is_jsonl(tmp_path):
    save_pipeline_stage(tmp_path, PSMILES_A, "retro", "pass", "routes=2")
    audit_file = tmp_path / "audit" / "pipeline_audit.jsonl"
    assert audit_file.is_file()
    lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    import json
    rec = json.loads(lines[0])
    assert rec["stage"] == "retro"
