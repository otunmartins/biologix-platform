"""Tests for MCP vs HTTP API parity endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
    from biologix_ai.http_api.app import app

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def client():
    return TestClient(app)


class TestLiteratureParity:
    def test_paper_qa_status(self, client):
        resp = client.get("/api/literature/paper-qa/status")
        assert resp.status_code == 200
        assert "ready" in resp.json()

    def test_semantic_scholar_search(self, client):
        resp = client.post("/api/literature/search/semantic-scholar", json={"query": "PEG insulin"})
        assert resp.status_code == 200
        data = resp.json()
        assert "papers" in data

    def test_lookup_material_validation(self, client):
        resp = client.post("/api/literature/lookup-material", json={"material_name": ""})
        assert resp.status_code == 422


class TestPSMILESParity:
    def test_generate_psmiles(self, client):
        resp = client.post("/api/psmiles/generate", json={"material_name": "PEG"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True or "psmiles" in data

    def test_mutate_psmiles(self, client):
        resp = client.post("/api/psmiles/mutate", json={"library_size": 3})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestExperimentsParity:
    def test_materials_status(self, client):
        resp = client.get("/api/experiments/materials-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "md_simulation" in data
        assert "paper_qa" in data

    def test_patch_world(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-parity").mkdir(parents=True)
        world = {"objective": "test", "simulation_entries": []}
        (runs_dir / "exp-parity" / "discovery_world.json").write_text(
            json.dumps(world), encoding="utf-8"
        )
        import biologix_ai.http_api.deps as deps
        import biologix_ai.http_api.routers.experiments as exp_module

        monkeypatch.setattr(deps, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(exp_module, "RUNS_DIR", runs_dir)

        resp = client.patch(
            "/api/experiments/exp-parity/world",
            json={"patch": {"objective": "updated objective"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        updated = json.loads((runs_dir / "exp-parity" / "discovery_world.json").read_text())
        assert updated["objective"] == "updated objective"

    def test_save_and_load_discovery_state(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-state").mkdir(parents=True)
        import biologix_ai.http_api.deps as deps
        import biologix_ai.http_api.routers.experiments as exp_module

        monkeypatch.setattr(deps, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(exp_module, "RUNS_DIR", runs_dir)

        save = client.post(
            "/api/experiments/exp-state/discovery-state",
            json={"iteration": 1, "feedback": {"high_performer_psmiles": ["[*]OCC[*]"]}},
        )
        assert save.status_code == 200

        load = client.get("/api/experiments/exp-state/discovery-state")
        assert load.status_code == 200
        assert load.json()["iteration"] == 1

    def test_save_funnel_and_audit(self, client, tmp_path, monkeypatch):
        runs_dir = tmp_path / "runs"
        (runs_dir / "exp-audit").mkdir(parents=True)
        import biologix_ai.http_api.deps as deps
        import biologix_ai.http_api.routers.experiments as exp_module

        monkeypatch.setattr(deps, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(exp_module, "RUNS_DIR", runs_dir)

        funnel = client.post(
            "/api/experiments/exp-audit/funnel",
            json={"stage": "post_screening", "checkpoint_data": {"top": 3}},
        )
        assert funnel.status_code == 200
        assert funnel.json()["saved"] is True

        audit = client.post(
            "/api/experiments/exp-audit/audit",
            json={
                "candidate_psmiles": "[*]OCC[*]",
                "stage": "validation",
                "disposition": "pass",
                "detail": "ok",
            },
        )
        assert audit.status_code == 200
        assert audit.json()["recorded"] is True

        trail = client.get("/api/experiments/exp-audit/audit")
        assert trail.status_code == 200
        assert len(trail.json()) == 1


class TestBiologicsParity:
    def test_resolve_insulin(self, client):
        resp = client.post("/api/biologics/resolve", json={"name_or_pdb_id": "insulin"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["pdb_id"]


class TestReportsParity:
    def test_write_summary_missing_session(self, client):
        resp = client.post(
            "/api/reports/write-summary",
            json={"experiment_id": "does-not-exist"},
        )
        assert resp.status_code == 404


class TestOpenAPIParityTags:
    def test_new_tags_present(self, client):
        resp = client.get("/openapi.json")
        spec = resp.json()
        tag_names = {t["name"] for t in spec.get("tags", [])}
        for expected in ("Literature", "PSMILES", "OpenMM", "Biologics", "Reports"):
            assert expected in tag_names

    def test_parity_paths_in_spec(self, client):
        paths = client.get("/openapi.json").json().get("paths", {})
        assert "/api/literature/mine" in paths
        assert "/api/psmiles/generate" in paths
        assert "/api/openmm/evaluate" in paths
        assert "/api/biologics/resolve" in paths
        assert "/api/reports/compile-pdf" in paths
        assert "/api/experiments/{experiment_id}/world" in paths
        methods = paths["/api/experiments/{experiment_id}/world"]
        assert "patch" in methods
