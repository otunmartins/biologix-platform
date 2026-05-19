"""Tests for the FastAPI HTTP surface using TestClient."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
    from insulin_ai.http_api.app import app

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Existing endpoint tests (unchanged behaviour)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "retrosynthesis_agent_available" in data
        assert "aizynthfinder_available" in data
        assert "admet_available" in data

    def test_health_version_updated(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["version"] == "0.4.0"


class TestRetrosynthesisEndpoint:
    def test_plan_returns_result(self, client):
        resp = client.post("/api/retrosynthesis/plan", json={
            "target": "PEG",
            "biologic_target": "insulin",
            "max_routes": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "polymer_routes" in data
        assert "request" in data

    def test_plan_with_custom_biologic(self, client):
        resp = client.post("/api/retrosynthesis/plan", json={
            "target": "[*]OCC[*]",
            "biologic_target": "adalimumab",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["request"]["biologic_target"] == "adalimumab"

    def test_plan_validation_error(self, client):
        resp = client.post("/api/retrosynthesis/plan", json={})
        assert resp.status_code == 422

    def test_compile_returns_report(self, client):
        resp = client.post("/api/retrosynthesis/compile", json={
            "target": "PEG",
            "biologic_target": "insulin",
            "max_routes": 2,
            "run_admet": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "scorecards" in data
        assert "next_steps" in data
        assert data["biologic_target"] == "insulin"

    def test_templates_endpoint(self, client):
        resp = client.get("/api/retrosynthesis/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "polymerization_types" in data
        assert "RAFT" in data["polymerization_types"]


class TestADMETEndpoint:
    def test_screen_single(self, client):
        resp = client.post("/api/admet/screen", json={"smiles": "CCO"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["smiles"] == "CCO"
        assert "safe" in data

    def test_screen_batch(self, client):
        resp = client.post("/api/admet/batch", json={"smiles_list": ["CCO", "CC(=O)O"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


# ---------------------------------------------------------------------------
# New: Candidates endpoints
# ---------------------------------------------------------------------------

class TestCandidatesEndpoints:
    def test_validate_valid_psmiles(self, client):
        resp = client.post("/api/candidates/validate", json={"psmiles": "[*]OCC[*]"})
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data
        assert data["psmiles"] == "[*]OCC[*]"

    def test_validate_missing_psmiles(self, client):
        resp = client.post("/api/candidates/validate", json={})
        assert resp.status_code == 422

    def test_compliance_known_peg(self, client):
        resp = client.post("/api/candidates/compliance", json={"psmiles": "[*]OCC[*]"})
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_status" in data
        assert data["psmiles"] == "[*]OCC[*]"

    def test_compliance_missing_psmiles(self, client):
        resp = client.post("/api/candidates/compliance", json={})
        assert resp.status_code == 422

    def test_profile_single_candidate(self, client):
        resp = client.post("/api/candidates/profile", json={
            "psmiles": "[*]OCC[*]",
            "biologic_target": "insulin",
            "run_retro": False,
            "run_admet": True,
            "run_compliance": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["psmiles"] == "[*]OCC[*]"
        assert "validation" in data
        assert "compliance" in data

    def test_screen_batch_library(self, client):
        resp = client.post("/api/candidates/screen", json={
            "psmiles_list": ["[*]OCC[*]", "[*]CC(O)[*]"],
            "run_retro": False,
            "run_admet": False,
            "run_compliance": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        for item in data:
            assert "library_disposition" in item
            assert item["library_disposition"] in ("pass", "warning", "fail")

    def test_screen_empty_list(self, client):
        resp = client.post("/api/candidates/screen", json={"psmiles_list": []})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_screen_sorted_by_disposition(self, client):
        resp = client.post("/api/candidates/screen", json={
            "psmiles_list": ["[*]OCC[*]", "[*]CC[*]"],
            "run_retro": False,
            "run_admet": False,
            "run_compliance": True,
        })
        data = resp.json()
        order = {"pass": 0, "warning": 1, "fail": 2}
        dispositions = [order[d["library_disposition"]] for d in data]
        assert dispositions == sorted(dispositions)


# ---------------------------------------------------------------------------
# New: Personas endpoint
# ---------------------------------------------------------------------------

class TestPersonasEndpoints:
    def test_list_personas(self, client):
        resp = client.get("/api/personas")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        ids = {p["id"] for p in data}
        assert "formulation-scientist" in ids
        assert "regulatory-affairs" in ids

    def test_get_persona_by_id(self, client):
        resp = client.get("/api/personas/computational-chemist")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "computational-chemist"
        assert "weights" in data
        assert "thermal_stability" in data["weights"]

    def test_get_persona_not_found(self, client):
        resp = client.get("/api/personas/nonexistent-persona")
        assert resp.status_code == 404

    def test_all_personas_have_valid_weights(self, client):
        resp = client.get("/api/personas")
        data = resp.json()
        for persona in data:
            w = persona["weights"]
            total = (
                w["thermal_stability"]
                + w["aggregation_suppression"]
                + w["excipient_safety"]
                + w["synthetic_accessibility"]
                + w["regulatory_precedent"]
                + w["literature_support"]
                + w.get("other", 0.0)
            )
            assert abs(total - 1.0) < 1e-6, f"Persona '{persona['id']}' weights sum to {total}"


# ---------------------------------------------------------------------------
# New: Experiments endpoints (session state reading)
# ---------------------------------------------------------------------------

class TestExperimentsEndpoints:
    def test_get_nonexistent_experiment(self, client):
        resp = client.get("/api/experiments/nonexistent-id-xyz")
        assert resp.status_code == 404

    def test_get_world_nonexistent(self, client):
        resp = client.get("/api/experiments/nonexistent-id-xyz/world")
        assert resp.status_code == 404

    def test_get_audit_nonexistent(self, client):
        resp = client.get("/api/experiments/nonexistent-id-xyz/audit")
        assert resp.status_code == 404

    def test_get_funnel_nonexistent(self, client):
        resp = client.get("/api/experiments/nonexistent-id-xyz/funnel")
        assert resp.status_code == 404

    def test_get_experiment_with_world_file(self, client, tmp_path, monkeypatch):
        """Simulate a real session directory with a discovery_world.json."""
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-001").mkdir(parents=True)
        world = {
            "objective": "Discover materials for adalimumab",
            "simulation_entries": [
                {"psmiles": "[*]OCC[*]", "interaction_energy_kj_mol": -45.2}
            ],
            "retrosynthesis_entries": [{"biologic_target": "adalimumab"}],
            "meta": {"last_iteration": 3, "updated_at": "2026-05-17T10:00:00Z"},
        }
        (runs_dir / "exp-001" / "discovery_world.json").write_text(
            json.dumps(world), encoding="utf-8"
        )
        import insulin_ai.http_api.routers.experiments as exp_module
        monkeypatch.setattr(exp_module, "_RUNS", runs_dir)

        resp = client.get("/api/experiments/exp-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["experiment_id"] == "exp-001"
        assert data["candidates_count"] == 1
        assert data["top_candidate"] == "[*]OCC[*]"
        assert data["last_iteration"] == 3

    def test_get_candidates_sorted(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-002").mkdir(parents=True)
        world = {
            "simulation_entries": [
                {"psmiles": "[*]CC[*]", "interaction_energy_kj_mol": -10.0},
                {"psmiles": "[*]OCC[*]", "interaction_energy_kj_mol": -45.0},
            ]
        }
        (runs_dir / "exp-002" / "discovery_world.json").write_text(
            json.dumps(world), encoding="utf-8"
        )
        import insulin_ai.http_api.routers.experiments as exp_module
        monkeypatch.setattr(exp_module, "_RUNS", runs_dir)

        resp = client.get("/api/experiments/exp-002/candidates")
        assert resp.status_code == 200
        data = resp.json()
        energies = [d["interaction_energy_kj_mol"] for d in data]
        assert energies == sorted(energies)

    def test_get_world_returns_full_json(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-003").mkdir(parents=True)
        world = {"objective": "test", "custom_key": 42}
        (runs_dir / "exp-003" / "discovery_world.json").write_text(
            json.dumps(world), encoding="utf-8"
        )
        import insulin_ai.http_api.routers.experiments as exp_module
        monkeypatch.setattr(exp_module, "_RUNS", runs_dir)

        resp = client.get("/api/experiments/exp-003/world")
        assert resp.status_code == 200
        data = resp.json()
        assert data["custom_key"] == 42

    def test_get_audit_empty_session(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-004").mkdir(parents=True)
        import insulin_ai.http_api.routers.experiments as exp_module
        monkeypatch.setattr(exp_module, "_RUNS", runs_dir)

        resp = client.get("/api/experiments/exp-004/audit")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_funnel_empty_session(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-005").mkdir(parents=True)
        import insulin_ai.http_api.routers.experiments as exp_module
        monkeypatch.setattr(exp_module, "_RUNS", runs_dir)

        resp = client.get("/api/experiments/exp-005/funnel")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# OpenAPI spec completeness
# ---------------------------------------------------------------------------

class TestOpenAPISpec:
    def test_openapi_json_reachable(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["info"]["title"] == "Biologics AI Platform API"
        assert spec["info"]["version"] == "0.4.0"

    def test_all_tags_present(self, client):
        resp = client.get("/openapi.json")
        spec = resp.json()
        tag_names = {t["name"] for t in spec.get("tags", [])}
        for expected in ("Experiments", "Candidates", "Retrosynthesis", "ADMET", "Personas", "Streaming"):
            assert expected in tag_names, f"Tag '{expected}' missing from OpenAPI spec"

    def test_experiments_routes_in_spec(self, client):
        resp = client.get("/openapi.json")
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/api/experiments" in paths
        assert "/api/experiments/{experiment_id}" in paths
        assert "/api/experiments/{experiment_id}/stream" in paths

    def test_candidates_routes_in_spec(self, client):
        resp = client.get("/openapi.json")
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/api/candidates/validate" in paths
        assert "/api/candidates/profile" in paths
        assert "/api/candidates/screen" in paths
        assert "/api/candidates/compliance" in paths

    def test_personas_routes_in_spec(self, client):
        resp = client.get("/openapi.json")
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/api/personas" in paths
