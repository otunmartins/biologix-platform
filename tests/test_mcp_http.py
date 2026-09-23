"""Tests for the authenticated Streamable HTTP MCP transport."""

from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from starlette.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "biologix_ai_mcp_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("biologix_ai_mcp_http_test", SERVER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_app_requires_configured_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_server()
    monkeypatch.delenv("BIOLOGIX_MCP_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="BIOLOGIX_MCP_TOKEN"):
        module.create_http_app()


def test_http_app_rejects_missing_and_wrong_bearer_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_server()
    monkeypatch.setenv("BIOLOGIX_MCP_TOKEN", "test-secret-token")

    with TestClient(module.create_http_app()) as client:
        missing = client.post("/mcp", json={})
        wrong = client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )

    for response in (missing, wrong):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_http_app_accepts_correct_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_server()
    monkeypatch.setenv("BIOLOGIX_MCP_TOKEN", "test-secret-token")

    with TestClient(module.create_http_app()) as client:
        response = client.post(
            "/mcp",
            json={},
            headers={
                "Authorization": "Bearer test-secret-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code != 401


def test_http_transport_is_opt_in() -> None:
    module = _load_server()
    assert module.selected_transport({}) == "stdio"
    assert module.selected_transport({"BIOLOGIX_MCP_TRANSPORT": "http"}) == "http"
    with pytest.raises(ValueError, match="BIOLOGIX_MCP_TRANSPORT"):
        module.selected_transport({"BIOLOGIX_MCP_TRANSPORT": "websocket"})


def test_http_server_host_and_port_defaults() -> None:
    module = _load_server()
    assert module.http_bind_address({}) == ("0.0.0.0", 8000)
    assert module.http_bind_address(
        {
            "BIOLOGIX_MCP_HOST": "127.0.0.1",
            "BIOLOGIX_MCP_PORT": "9123",
        }
    ) == ("127.0.0.1", 9123)
    with pytest.raises(ValueError, match="BIOLOGIX_MCP_PORT"):
        module.http_bind_address({"BIOLOGIX_MCP_PORT": "70000"})


def test_oauth_dynamic_registration_pkce_and_legacy_bearer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    public_url = "https://example.modal.run"
    monkeypatch.setenv("BIOLOGIX_MCP_TRANSPORT", "http")
    monkeypatch.setenv("BIOLOGIX_OAUTH_ISSUER_URL", public_url)
    monkeypatch.setenv("BIOLOGIX_MCP_RESOURCE_URL", f"{public_url}/mcp")
    monkeypatch.setenv("BIOLOGIX_MCP_TOKEN", "legacy-token")
    monkeypatch.setenv(
        "BIOLOGIX_OAUTH_APPROVAL_TOKEN",
        "approval-token-with-at-least-32-bytes",
    )
    monkeypatch.setenv(
        "BIOLOGIX_OAUTH_STORAGE_KEY",
        "storage-key-with-at-least-32-random-bytes",
    )
    monkeypatch.setenv(
        "BIOLOGIX_OAUTH_STORE_PATH",
        str(tmp_path / "oauth-state.enc"),
    )
    module = _load_server()
    verifier = "oauth-verifier-with-enough-randomness"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )

    with TestClient(module.create_http_app()) as client:
        metadata = client.get("/.well-known/oauth-authorization-server")
        protected = client.get("/.well-known/oauth-protected-resource/mcp")
        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "biologix:tools",
                "client_name": "Claude Web",
            },
        )
        assert metadata.status_code == 200
        assert metadata.json()["registration_endpoint"] == f"{public_url}/register"
        assert protected.status_code == 200
        assert protected.json()["resource"] == f"{public_url}/mcp"
        root_protected = client.get("/.well-known/oauth-protected-resource")
        assert root_protected.status_code == 200
        assert root_protected.json()["resource"] == protected.json()["resource"]
        assert root_protected.json()["authorization_servers"] == protected.json()["authorization_servers"]
        assert "none" in metadata.json()["token_endpoint_auth_methods_supported"]
        public_registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://chatgpt.com/connector/oauth/test-callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "biologix:tools",
                "client_name": "ChatGPT",
            },
        )
        assert public_registration.status_code == 201
        assert not public_registration.json().get("client_secret")
        assert registration.status_code == 201
        registered = registration.json()

        rejected_redirect = client.get(
            "/authorize?"
            + urlencode(
                {
                    "client_id": registered["client_id"],
                    "redirect_uri": "https://attacker.example/callback",
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "scope": "biologix:tools",
                    "resource": f"{public_url}/mcp",
                }
            ),
            follow_redirects=False,
        )
        assert rejected_redirect.status_code == 400
        assert "location" not in rejected_redirect.headers

        authorize_query = urlencode(
            {
                "client_id": registered["client_id"],
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "claude-state",
                "scope": "biologix:tools",
                "resource": f"{public_url}/mcp",
            }
        )
        authorization = client.get(
            f"/authorize?{authorize_query}",
            follow_redirects=False,
        )
        assert authorization.status_code == 302
        approval_url = authorization.headers["location"]
        request_id = parse_qs(urlparse(approval_url).query)["request_id"][0]
        approval_page = client.get(approval_url)
        assert approval_page.status_code == 200
        assert "Claude Web" in approval_page.text
        assert "background: #fff" in approval_page.text
        assert approval_page.headers["x-frame-options"] == "DENY"

        wrong_approval = client.post(
            "/oauth/approve",
            data={
                "request_id": request_id,
                "access_token": "wrong-token",
                "action": "approve",
            },
            follow_redirects=False,
        )
        assert wrong_approval.status_code == 401

        approval = client.post(
            "/oauth/approve",
            data={
                "request_id": request_id,
                "access_token": "approval-token-with-at-least-32-bytes",
                "action": "approve",
            },
            follow_redirects=False,
        )
        assert approval.status_code == 200
        assert "Approved" in approval.text
        continue_url = html.unescape(
            approval.text.split('href="', 1)[1].split('"', 1)[0]
        )
        callback_query = parse_qs(urlparse(continue_url).query)
        assert callback_query["state"] == ["claude-state"]
        code = callback_query["code"][0]
        assert approval.headers["content-security-policy"].count("form-action 'self'") == 1

        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": registered["client_id"],
                "client_secret": registered["client_secret"],
                "code_verifier": verifier,
                "resource": f"{public_url}/mcp",
            },
        )
        assert token_response.status_code == 200
        oauth_token = token_response.json()["access_token"]

        unauthenticated = client.post("/mcp", json={})
        oauth_authenticated = client.post(
            "/mcp",
            json={},
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        legacy_authenticated = client.post(
            "/mcp",
            json={},
            headers={
                "Authorization": "Bearer legacy-token",
                "Accept": "application/json, text/event-stream",
            },
        )

        public_client = public_registration.json()
        public_verifier = "chatgpt-verifier-with-enough-randomness"
        public_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(public_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        public_authorize = client.get(
            "/authorize?"
            + urlencode(
                {
                    "client_id": public_client["client_id"],
                    "redirect_uri": "https://chatgpt.com/connector/oauth/test-callback",
                    "response_type": "code",
                    "code_challenge": public_challenge,
                    "code_challenge_method": "S256",
                    "state": "chatgpt-state",
                    "scope": "biologix:tools",
                    "resource": f"{public_url}/mcp",
                }
            ),
            follow_redirects=False,
        )
        public_request_id = parse_qs(urlparse(public_authorize.headers["location"]).query)["request_id"][0]
        public_approval = client.post(
            "/oauth/approve",
            data={
                "request_id": public_request_id,
                "access_token": "approval-token-with-at-least-32-bytes",
                "action": "approve",
            },
            follow_redirects=False,
        )
        public_continue = html.unescape(public_approval.text.split('href="', 1)[1].split('"', 1)[0])
        public_code = parse_qs(urlparse(public_continue).query)["code"][0]
        public_token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": public_code,
                "redirect_uri": "https://chatgpt.com/connector/oauth/test-callback",
                "client_id": public_client["client_id"],
                "code_verifier": public_verifier,
                "resource": f"{public_url}/mcp",
            },
        )
        assert public_token.status_code == 200
        assert public_token.json()["access_token"]

    assert unauthenticated.status_code == 401
    assert "resource_metadata=" in unauthenticated.headers["www-authenticate"]
    assert oauth_authenticated.status_code != 401
    assert legacy_authenticated.status_code != 401
