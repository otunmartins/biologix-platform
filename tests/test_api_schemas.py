"""Round-trip serialisation tests for http_api/schemas.py.

Verifies that all Pydantic models serialise to JSON cleanly and can be
reconstructed from dict/JSON without data loss. Does not require a running
server or any heavy optional dependencies.
"""

from __future__ import annotations

import json

import pytest

try:
    from biologix_ai.http_api.schemas import (
        APIError,
        CandidateProfileRequest,
        CandidateProfileResponse,
        ComplianceRequest,
        ComplianceResponse,
        ExperimentStatus,
        FunnelCheckpoint,
        FunnelManifestEntry,
        LibraryScreenItem,
        PersonaPreset,
        PersonaWeights,
        PipelineAuditRecord,
        RetrosynthesisSummary,
        ScreenLibraryRequest,
        SessionResponse,
        StartExperimentRequest,
        ValidateRequest,
        ValidationResponse,
    )
    HAS_SCHEMAS = True
except ImportError as _e:
    HAS_SCHEMAS = False
    _IMPORT_ERR = str(_e)

pytestmark = pytest.mark.skipif(
    not HAS_SCHEMAS,
    reason=f"schemas import failed: {_IMPORT_ERR if not HAS_SCHEMAS else ''}",
)


# ---------------------------------------------------------------------------
# ComplianceResponse
# ---------------------------------------------------------------------------

class TestComplianceResponse:
    def test_minimal_round_trip(self):
        obj = ComplianceResponse(psmiles="[*]OCC[*]", overall_status="approved")
        data = json.loads(obj.model_dump_json())
        rebuilt = ComplianceResponse(**data)
        assert rebuilt.psmiles == "[*]OCC[*]"
        assert rebuilt.overall_status == "approved"

    def test_defaults(self):
        obj = ComplianceResponse(psmiles="[*]CC[*]")
        assert obj.overall_status == "unknown"
        assert obj.immunogenicity_flags == []
        assert obj.aggregation_flags == []
        assert obj.jurisdictions_matched == []

    def test_flagged_status(self):
        obj = ComplianceResponse(
            psmiles="[*]OCC[*]",
            overall_status="flagged",
            immunogenicity_flags=[{"name": "anti_PEG", "severity": "warning"}],
        )
        data = obj.model_dump()
        assert data["immunogenicity_flags"][0]["name"] == "anti_PEG"


# ---------------------------------------------------------------------------
# ValidationResponse
# ---------------------------------------------------------------------------

class TestValidationResponse:
    def test_valid_round_trip(self):
        obj = ValidationResponse(psmiles="[*]OCC[*]", valid=True, canonical="[*]CCO[*]")
        data = json.loads(obj.model_dump_json())
        rebuilt = ValidationResponse(**data)
        assert rebuilt.valid is True
        assert rebuilt.canonical == "[*]CCO[*]"

    def test_invalid_with_errors(self):
        obj = ValidationResponse(psmiles="not_valid", valid=False, errors=["RDKit parse failure"])
        assert not obj.valid
        assert "RDKit parse failure" in obj.errors


# ---------------------------------------------------------------------------
# CandidateProfileResponse
# ---------------------------------------------------------------------------

class TestCandidateProfileResponse:
    def test_minimal(self):
        obj = CandidateProfileResponse(psmiles="[*]OCC[*]")
        data = obj.model_dump()
        assert data["psmiles"] == "[*]OCC[*]"
        assert data["biologic_target"] == "insulin"
        assert data["validation"] is None
        assert data["admet"] is None

    def test_with_nested_compliance(self):
        comp = ComplianceResponse(psmiles="[*]OCC[*]", overall_status="approved")
        retro = RetrosynthesisSummary(n_routes=2)
        obj = CandidateProfileResponse(
            psmiles="[*]OCC[*]",
            compliance=comp,
            retrosynthesis=retro,
        )
        data = json.loads(obj.model_dump_json())
        assert data["compliance"]["overall_status"] == "approved"
        assert data["retrosynthesis"]["n_routes"] == 2


# ---------------------------------------------------------------------------
# LibraryScreenItem
# ---------------------------------------------------------------------------

class TestLibraryScreenItem:
    def test_inherits_profile_fields(self):
        item = LibraryScreenItem(psmiles="[*]CC[*]", library_disposition="pass")
        assert item.biologic_target == "insulin"
        assert item.library_disposition == "pass"

    def test_disposition_values(self):
        for disp in ("pass", "warning", "fail"):
            item = LibraryScreenItem(psmiles="[*]CC[*]", library_disposition=disp)
            assert item.library_disposition == disp


# ---------------------------------------------------------------------------
# PipelineAuditRecord
# ---------------------------------------------------------------------------

class TestPipelineAuditRecord:
    def test_round_trip(self):
        obj = PipelineAuditRecord(
            audit_id="a1b2",
            timestamp="2026-05-17T12:00:00Z",
            candidate_psmiles="[*]OCC[*]",
            stage="admet",
            disposition="pass",
            detail="No alerts found",
        )
        data = json.loads(obj.model_dump_json())
        rebuilt = PipelineAuditRecord(**data)
        assert rebuilt.audit_id == "a1b2"
        assert rebuilt.disposition == "pass"

    def test_disposition_literal(self):
        with pytest.raises(Exception):
            PipelineAuditRecord(
                audit_id="x",
                timestamp="t",
                candidate_psmiles="s",
                stage="s",
                disposition="invalid_value",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# FunnelCheckpoint
# ---------------------------------------------------------------------------

class TestFunnelCheckpoint:
    def test_round_trip(self):
        obj = FunnelCheckpoint(
            stage="admet_screen",
            saved_at="2026-05-17T12:00:00Z",
            data={"n_pass": 5, "n_fail": 2},
        )
        data = json.loads(obj.model_dump_json())
        assert data["data"]["n_pass"] == 5

    def test_empty_data(self):
        obj = FunnelCheckpoint(stage="init", saved_at="t")
        assert obj.data == {}


# ---------------------------------------------------------------------------
# FunnelManifestEntry
# ---------------------------------------------------------------------------

class TestFunnelManifestEntry:
    def test_fields(self):
        obj = FunnelManifestEntry(stage="admet", file="admet.json", saved_at="t")
        assert obj.stage == "admet"


# ---------------------------------------------------------------------------
# PersonaPreset
# ---------------------------------------------------------------------------

class TestPersonaPreset:
    def test_weights_sum_to_one(self):
        from biologix_ai.http_api.routers.personas import _PERSONAS

        for persona in _PERSONAS:
            w = persona.weights
            total = (
                w.thermal_stability
                + w.aggregation_suppression
                + w.excipient_safety
                + w.synthetic_accessibility
                + w.regulatory_precedent
                + w.literature_support
                + w.other
            )
            assert abs(total - 1.0) < 1e-6, (
                f"Persona '{persona.id}' weights sum to {total:.4f}, expected 1.0"
            )

    def test_all_five_personas_present(self):
        from biologix_ai.http_api.routers.personas import _PERSONAS

        ids = {p.id for p in _PERSONAS}
        expected = {
            "formulation-scientist",
            "computational-chemist",
            "regulatory-affairs",
            "synthetic-chemist",
            "academic-researcher",
        }
        assert ids == expected

    def test_persona_round_trip(self):
        obj = PersonaPreset(
            id="test",
            name="Test",
            description="A test persona",
            weights=PersonaWeights(
                thermal_stability=0.5,
                aggregation_suppression=0.0,
                excipient_safety=0.1,
                synthetic_accessibility=0.1,
                regulatory_precedent=0.1,
                literature_support=0.2,
            ),
        )
        data = json.loads(obj.model_dump_json())
        rebuilt = PersonaPreset(**data)
        assert rebuilt.weights.thermal_stability == 0.5


# ---------------------------------------------------------------------------
# SessionResponse
# ---------------------------------------------------------------------------

class TestSessionResponse:
    def test_serialises(self):
        from biologix_ai.services.biologic_resolver import BiologicTarget

        bio = BiologicTarget(query="insulin", canonical_name="insulin", pdb_id="")
        obj = SessionResponse(
            session_dir="/tmp/test",
            biologic_resolution=bio,
            note="Test session",
        )
        data = json.loads(obj.model_dump_json())
        assert data["session_dir"] == "/tmp/test"
        assert data["note"] == "Test session"


# ---------------------------------------------------------------------------
# ExperimentStatus
# ---------------------------------------------------------------------------

class TestExperimentStatus:
    def test_defaults(self):
        obj = ExperimentStatus(
            experiment_id="abc123",
            session_dir="/tmp/abc",
            biologic_target="adalimumab",
        )
        assert obj.candidates_count == 0
        assert obj.top_candidate is None
        assert obj.last_iteration == 0


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TestRequestModels:
    def test_start_experiment_defaults(self):
        req = StartExperimentRequest(biologic_target="adalimumab")
        assert req.fetch_pdb is True
        assert req.polymer_target == ""

    def test_screen_library_request(self):
        req = ScreenLibraryRequest(psmiles_list=["[*]OCC[*]", "[*]CC[*]"])
        assert req.biologic_target == "insulin"
        assert req.run_admet is True
        assert req.max_candidates == 50

    def test_validate_request_defaults(self):
        req = ValidateRequest(psmiles="[*]OCC[*]")
        assert req.material_name == ""
        assert req.crosscheck_web is False

    def test_api_error(self):
        err = APIError(error="not found", detail="Experiment ID not found")
        assert err.error == "not found"
