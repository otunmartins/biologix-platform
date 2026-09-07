import pytest
from fastapi.testclient import TestClient

from biologix_ai.platform.extractions import apply_to_parameters, preflight, render_sources

from test_platform_api import app


SOURCES = [{
    "name": "Smith 2019",
    "reactions": [{
        "reactants": ["ethylene oxide", "water"],
        "products": ["poly(ethylene glycol)"],
        "conditions": "KOH, 120 C",
    }],
}]


def signed_in_client(email: str) -> TestClient:
    client = TestClient(app)
    client.post("/api/platform/auth/signup", json={"email": email, "password": "secure-pass"})
    return client


def test_sources_render_to_the_engine_reaction_format():
    rendered = render_sources(SOURCES)
    assert rendered["Smith 2019"] == (
        "Reaction 1:\n"
        "Reactants: ethylene oxide, water\n"
        "Products: poly(ethylene glycol)\n"
        "Conditions: KOH, 120 C"
    )


def test_missing_conditions_still_produce_a_conditions_line():
    rendered = render_sources([{
        "name": "Jones 2021",
        "reactions": [{"reactants": ["lactide"], "products": ["poly(lactic acid)"]}],
    }])
    assert "Conditions: not specified" in rendered["Jones 2021"]


def test_preflight_reports_the_tree_root_and_reachable_leaves():
    report = preflight("poly(ethylene glycol)", SOURCES)
    assert report["root_product_found"] is True
    assert report["blocking_reactants"] == []


def test_preflight_flags_a_target_no_reaction_produces():
    report = preflight("PEG", SOURCES)
    assert report["root_product_found"] is False
    assert any("peg" in warning.lower() for warning in report["warnings"])


def test_evidence_whose_products_miss_the_target_is_rejected_at_creation():
    client = signed_in_client("mismatch@example.com")
    response = client.post("/api/platform/experiments", json={
        "name": "PEG screen",
        "biologic_target": "insulin",
        "polymer_target": "PEG",
        "parameters": {"retrosynthesis_sources": SOURCES},
    })
    assert response.status_code == 422
    assert "No reaction produces 'PEG'" in response.json()["detail"]


def test_creation_renders_evidence_into_the_parameters_the_worker_reads():
    client = signed_in_client("evidence@example.com")
    response = client.post("/api/platform/experiments", json={
        "name": "PEG screen",
        "biologic_target": "insulin",
        "polymer_target": "poly(ethylene glycol)",
        "parameters": {"retrosynthesis_sources": SOURCES},
    })
    assert response.status_code == 201
    extractions = response.json()["parameters"]["retrosynthesis_extractions"]
    assert "Products: poly(ethylene glycol)" in extractions["Smith 2019"]


def test_retry_attaches_evidence_to_an_experiment_that_needed_input():
    client = signed_in_client("evidence-retry@example.com")
    created = client.post("/api/platform/experiments", json={
        "name": "PEG screen",
        "biologic_target": "insulin",
        "polymer_target": "poly(ethylene glycol)",
        "parameters": {},
    }).json()
    assert "retrosynthesis_extractions" not in created["parameters"]

    response = client.post(
        f"/api/platform/experiments/{created['id']}/retry",
        json={"parameters": {"retrosynthesis_sources": SOURCES}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert "Products: poly(ethylene glycol)" in response.json()["parameters"]["retrosynthesis_extractions"]["Smith 2019"]


def test_retry_without_a_body_still_requeues_unchanged():
    client = signed_in_client("plain-retry@example.com")
    created = client.post("/api/platform/experiments", json={
        "name": "PEG screen", "biologic_target": "insulin", "polymer_target": "PEG", "parameters": {},
    }).json()
    response = client.post(f"/api/platform/experiments/{created['id']}/retry")
    assert response.status_code == 200
    assert response.json()["parameters"] == {}


def test_preflight_endpoint_requires_authentication():
    assert TestClient(app).post(
        "/api/platform/retrosynthesis/preflight",
        json={"material_name": "poly(ethylene glycol)", "sources": SOURCES},
    ).status_code == 401


def test_preflight_endpoint_returns_engine_diagnostics():
    client = signed_in_client("preflight@example.com")
    response = client.post("/api/platform/retrosynthesis/preflight", json={
        "material_name": "poly(ethylene glycol)", "sources": SOURCES,
    })
    assert response.status_code == 200
    assert response.json()["root_product_found"] is True
    assert response.json()["tree_root"] == "poly(ethylene glycol)"


@pytest.mark.parametrize("sources,message", [
    ([], "at least one literature source"),
    ([{"name": "", "reactions": [{"reactants": ["a"], "products": ["b"]}]}], "citation or title"),
    ([{"name": "A", "reactions": []}], "at least one reaction"),
    ([{"name": "A", "reactions": [{"reactants": [], "products": ["b"]}]}], "at least one reactant"),
])
def test_malformed_evidence_is_rejected_with_a_usable_message(sources, message):
    with pytest.raises(ValueError, match=message):
        render_sources(sources)


def test_parameters_without_evidence_pass_through_untouched():
    assert apply_to_parameters({"md_steps": 50}, "PEG") == {"md_steps": 50}


def test_ui_evidence_reaches_the_engine_workspace(tmp_path, monkeypatch):
    """The full path: UI sources -> stored parameters -> worker -> llm_res.json on disk."""
    import json
    from uuid import uuid4

    from biologix_ai.platform.models import Experiment
    from biologix_ai.platform.worker import run_retrosynthesis
    from biologix_ai.retrosynthesis.models import PolymerRoute, RetrosynthesisResult
    from biologix_ai.retrosynthesis.retro_adapter import session_has_extractions
    from biologix_ai.services import retrosynthesis_service

    def fake_plan(request):
        return RetrosynthesisResult(
            request=request,
            polymer_routes=[PolymerRoute(target_polymer="poly(ethylene glycol)")],
            metadata={"requires_agent_extractions": False, "route_provenance": "session_agent_llm"},
        )

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(retrosynthesis_service, "plan_retrosynthesis", fake_plan)

    parameters = apply_to_parameters({"retrosynthesis_sources": SOURCES}, "poly(ethylene glycol)")
    experiment = Experiment(
        id=uuid4(), owner_id=uuid4(), name="PEG",
        biologic_target="human insulin", polymer_target="poly(ethylene glycol)",
        parameters=parameters,
    )

    structure = {"psmiles": "[*]OCC[*]", "material_name": "poly(ethylene glycol)"}
    result = run_retrosynthesis(experiment, structure, {"retrosynthesis_agent": True})

    assert result["status"] == "completed"
    session_dir = tmp_path / str(experiment.id)
    assert session_has_extractions(session_dir, "poly(ethylene glycol)")
    written = json.loads(
        (session_dir / "retrosynthesis" / "retro_workspace" / "poly_ethylene_glycol"
         / "results" / "llm_res.json").read_text()
    )
    assert "Products: poly(ethylene glycol)" in written["Smith 2019"]
    assert "Reactants: ethylene oxide, water" in written["Smith 2019"]


def test_experiment_without_evidence_still_reports_requires_input(tmp_path, monkeypatch):
    from uuid import uuid4

    from biologix_ai.platform.models import Experiment
    from biologix_ai.platform.worker import run_retrosynthesis
    from biologix_ai.retrosynthesis.models import RetrosynthesisResult
    from biologix_ai.services import retrosynthesis_service

    monkeypatch.setenv("BIOLOGIX_PLATFORM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(
        retrosynthesis_service, "plan_retrosynthesis",
        lambda request: RetrosynthesisResult(request=request, metadata={"requires_agent_extractions": True}),
    )
    experiment = Experiment(
        id=uuid4(), owner_id=uuid4(), name="PEG",
        biologic_target="insulin", polymer_target="poly(ethylene glycol)", parameters={},
    )
    structure = {"psmiles": "[*]OCC[*]", "material_name": "poly(ethylene glycol)"}
    assert run_retrosynthesis(experiment, structure, {})["status"] == "requires_input"
