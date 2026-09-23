"""Client identity must survive ChatGPT's new MCP session per tool call."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai import mcp_client  # noqa: E402


def _ctx(session_id: str, client_id: str = ""):
    user = SimpleNamespace(access_token=SimpleNamespace(client_id=client_id)) if client_id else None
    request = SimpleNamespace(scope={"user": user} if user else {}, headers={"mcp-session-id": session_id})
    return SimpleNamespace(request=request, session=object())


def test_authenticated_calls_from_new_sessions_share_one_client(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "_request_context", lambda: _ctx("session-1", "chatgpt-connector"))
    first = mcp_client.client_key()
    monkeypatch.setattr(mcp_client, "_request_context", lambda: _ctx("session-2", "chatgpt-connector"))
    assert mcp_client.client_key() == first == "oauth:chatgpt-connector"


def test_different_connectors_stay_separate(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "_request_context", lambda: _ctx("s", "claude-web"))
    claude = mcp_client.client_key()
    monkeypatch.setattr(mcp_client, "_request_context", lambda: _ctx("s", "chatgpt"))
    assert mcp_client.client_key() != claude


def test_unauthenticated_http_falls_back_to_the_session_and_stdio_to_local(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "_request_context", lambda: _ctx("abc"))
    assert mcp_client.client_key() == "mcp:abc"
    monkeypatch.setattr(mcp_client, "_request_context", lambda: None)
    assert mcp_client.client_key() == "local"


def test_bearer_only_servers_key_on_the_credential_hash(monkeypatch) -> None:
    def ctx(session_id):
        request = SimpleNamespace(scope={}, headers={"mcp-session-id": session_id, "authorization": "Bearer t0k"})
        return SimpleNamespace(request=request, session=object())

    monkeypatch.setattr(mcp_client, "_request_context", lambda: ctx("a"))
    first = mcp_client.client_key()
    monkeypatch.setattr(mcp_client, "_request_context", lambda: ctx("b"))
    assert mcp_client.client_key() == first and first.startswith("bearer:") and "t0k" not in first
