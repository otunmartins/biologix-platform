"""Download packaging: signed links, the zip, and the route that serves them."""

import json
import zipfile
from pathlib import Path

import pytest

from biologix_ai import session_export as export


@pytest.fixture(autouse=True)
def _signing_env(monkeypatch):
    monkeypatch.setenv("BIOLOGIX_OAUTH_STORAGE_KEY", "test-key")
    monkeypatch.setenv("BIOLOGIX_MCP_RESOURCE_URL", "https://example.test/mcp")
    monkeypatch.delenv("BIOLOGIX_PUBLIC_URL", raising=False)


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    session = root / "demo"
    (session / "structures").mkdir(parents=True)
    (session / "audit").mkdir()
    (session / ".discovery_pdf_cache").mkdir()
    (session / "SUMMARY_REPORT.pdf").write_bytes(b"%PDF")
    (session / "structures" / "Candidate_0_complex_minimized.pdb").write_text("ATOM")
    (session / "structures" / "Candidate_0_complex_chemviz.png").write_bytes(b"png")
    (session / "structures" / "Candidate_0_complex_meta.json").write_text("{}")
    (session / "audit" / "pipeline_audit.jsonl").write_text("{}\n")
    (session / "tool_events.jsonl").write_text("{}\n")
    (session / ".discovery_pdf_cache" / "junk.bin").write_bytes(b"x")
    return root, session


def test_token_round_trip_and_expiry():
    token = export.sign_token("demo/a.pdb", ttl_s=60, now=1000)
    assert export.verify_token(token, now=1030) == "demo/a.pdb"
    assert export.verify_token(token, now=1061) is None


def test_tampered_or_unsigned_token_is_refused(monkeypatch):
    token = export.sign_token("demo/a.pdb")
    body, sig = token.split(".")
    other = export.sign_token("demo/b.pdb").split(".")[0]
    assert export.verify_token(f"{other}.{sig}") is None
    assert export.verify_token("garbage") is None
    monkeypatch.setenv("BIOLOGIX_OAUTH_STORAGE_KEY", "another-key")
    assert export.verify_token(token) is None


def test_download_cannot_leave_the_runs_root(runs, tmp_path):
    root, _ = runs
    (tmp_path / "secret.txt").write_text("nope")
    token = export.sign_token("../secret.txt")
    assert export.resolve_download(token, root) is None
    assert export.resolve_download(export.sign_token("demo/SUMMARY_REPORT.pdf"), root)


def test_zip_holds_every_deliverable_and_no_cache(runs):
    root, session = runs
    result = export.package_session(session, root)
    assert result["ok"]
    with zipfile.ZipFile(session / "exports" / result["zip"]["name"]) as bundle:
        names = set(bundle.namelist())
    assert "demo/SUMMARY_REPORT.pdf" in names
    assert "demo/structures/Candidate_0_complex_minimized.pdb" in names
    assert "demo/structures/Candidate_0_complex_chemviz.png" in names
    assert "demo/audit/pipeline_audit.jsonl" in names
    assert "demo/MANIFEST.md" in names
    assert not any("discovery_pdf_cache" in n or n.endswith(".zip") for n in names)


def test_second_export_does_not_nest_the_first(runs):
    root, session = runs
    export.package_session(session, root)
    second = export.package_session(session, root)
    with zipfile.ZipFile(session / "exports" / second["zip"]["name"]) as bundle:
        assert not any("exports/" in n for n in bundle.namelist())


def test_links_are_signed_and_point_at_the_public_origin(runs):
    root, session = runs
    result = export.package_session(session, root)
    url = result["zip"]["download_url"]
    assert url.startswith("https://example.test/downloads/")
    assert url.endswith(result["zip"]["name"])
    token = url.split("/downloads/")[1].split("/")[0]
    assert export.resolve_download(token, root).name == result["zip"]["name"]
    key = {f["path"]: f for f in result["key_files"]}
    assert key["SUMMARY_REPORT.pdf"]["download_url"]
    assert list(key)[0] == "SUMMARY_REPORT.pdf"  # reports come first


def test_logs_can_be_left_out_of_the_key_files_but_stay_in_the_zip(runs):
    root, session = runs
    result = export.package_session(session, root, include_logs=False)
    assert result["counts_by_category"]["log"] == 2


def test_no_public_url_means_no_links_but_still_a_zip(runs, monkeypatch):
    root, session = runs
    monkeypatch.delenv("BIOLOGIX_MCP_RESOURCE_URL")
    result = export.package_session(session, root)
    assert result["zip"]["download_url"] is None
    assert Path(result["zip"]["server_path"]).is_file()


def test_route_serves_a_valid_link_and_refuses_the_rest(runs, monkeypatch):
    from starlette.testclient import TestClient

    import biologix_ai_mcp_server as server

    root, session = runs
    monkeypatch.setattr(server, "runs_root", lambda: root)
    result = export.package_session(session, root)
    client = TestClient(server.mcp.streamable_http_app())
    path = result["zip"]["download_url"].split("example.test")[1]
    ok = client.get(path)
    assert ok.status_code == 200
    assert ok.headers["content-disposition"].startswith("attachment")
    assert ok.content[:2] == b"PK"
    png = next(f for f in result["key_files"] if f["path"].endswith(".png"))
    png_response = client.get(png["download_url"].split("example.test")[1])
    assert png_response.headers["content-disposition"].startswith("inline")
    assert client.get(path.replace("/downloads/", "/downloads/x")).status_code == 404


def test_bearer_only_server_still_lets_a_signed_link_through():
    import asyncio

    import biologix_ai_mcp_server as server

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])

    guarded = server.BearerTokenAuth(app, "secret")
    sent = []

    async def send(message):
        sent.append(message)

    async def run(path):
        await guarded({"type": "http", "path": path, "headers": []}, None, send)

    asyncio.run(run("/downloads/tok/a.zip"))
    asyncio.run(run("/mcp"))
    assert seen == ["/downloads/tok/a.zip"]
    assert sent[0]["status"] == 401


def test_tool_exports_the_active_session(runs, monkeypatch):
    import biologix_ai_mcp_server as server

    root, session = runs
    monkeypatch.setattr(server, "runs_root", lambda: root)
    payload = json.loads(server.export_session_outputs(run_dir=str(session)))
    assert payload["ok"] and payload["files_in_zip"] >= 5
    outside = json.loads(server.export_session_outputs(run_dir=str(root.parent)))
    assert outside["ok"] is False
