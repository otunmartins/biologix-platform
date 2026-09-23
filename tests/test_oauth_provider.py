"""Unit tests for the persistent Claude Web OAuth provider."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from pydantic import AnyUrl

from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull
from biologix_ai.oauth_provider import (
    BiologixOAuthProvider,
    EncryptedOAuthStore,
    OAuthApprovalError,
)


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="claude-web-client",
        client_secret="client-secret",
        redirect_uris=[AnyUrl("https://claude.ai/api/mcp/auth_callback")],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="biologix:tools",
        client_name="Claude Web",
    )


def _params() -> AuthorizationParams:
    return AuthorizationParams(
        state="state-token",
        scopes=["biologix:tools"],
        code_challenge="challenge-value",
        redirect_uri=AnyUrl("https://claude.ai/api/mcp/auth_callback"),
        redirect_uri_provided_explicitly=True,
        resource="https://example.modal.run/mcp",
    )


def _provider(tmp_path: Path, clock=lambda: 1_000.0) -> BiologixOAuthProvider:
    return BiologixOAuthProvider(
        issuer_url="https://example.modal.run",
        resource_url="https://example.modal.run/mcp",
        storage_path=tmp_path / "oauth-state.enc",
        approval_token="approval-token-with-at-least-32-bytes",
        storage_key="storage-key-with-at-least-32-random-bytes",
        legacy_bearer_token="legacy-bearer-token",
        clock=clock,
    )


def test_registered_clients_survive_encrypted_store_reload(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    client = _client()

    asyncio.run(provider.register_client(client))

    encrypted = (tmp_path / "oauth-state.enc").read_bytes()
    assert b"claude-web-client" not in encrypted
    assert b"client-secret" not in encrypted
    reloaded = _provider(tmp_path)
    loaded = asyncio.run(reloaded.get_client("claude-web-client"))
    assert loaded is not None
    assert loaded.client_secret == "client-secret"


def test_authorization_code_exchange_is_one_time_and_refresh_rotates(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    client = _client()
    asyncio.run(provider.register_client(client))
    approval_url = asyncio.run(provider.authorize(client, _params()))
    request_id = parse_qs(urlparse(approval_url).query)["request_id"][0]

    redirect = asyncio.run(
        provider.approve_authorization(
            request_id=request_id,
            supplied_token="approval-token-with-at-least-32-bytes",
        )
    )
    query = parse_qs(urlparse(redirect).query)
    code = query["code"][0]
    assert query["state"] == ["state-token"]

    authorization_code = asyncio.run(provider.load_authorization_code(client, code))
    assert authorization_code is not None
    first_tokens = asyncio.run(
        provider.exchange_authorization_code(client, authorization_code)
    )
    encryption_key = base64.urlsafe_b64encode(
        hashlib.sha256(
            b"storage-key-with-at-least-32-random-bytes"
        ).digest()
    )
    decrypted_store = Fernet(encryption_key).decrypt(
        (tmp_path / "oauth-state.enc").read_bytes()
    )
    assert first_tokens.access_token.encode() not in decrypted_store
    assert (first_tokens.refresh_token or "").encode() not in decrypted_store
    assert asyncio.run(provider.load_authorization_code(client, code)) is None
    access = asyncio.run(provider.load_access_token(first_tokens.access_token))
    assert access is not None
    assert access.scopes == ["biologix:tools"]

    refresh = asyncio.run(
        provider.load_refresh_token(client, first_tokens.refresh_token or "")
    )
    assert refresh is not None
    second_tokens = asyncio.run(
        provider.exchange_refresh_token(client, refresh, ["biologix:tools"])
    )
    assert second_tokens.refresh_token != first_tokens.refresh_token
    assert (
        asyncio.run(
            provider.load_refresh_token(client, first_tokens.refresh_token or "")
        )
        is None
    )


def test_approval_rejects_wrong_bootstrap_token_and_limits_attempts(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    client = _client()
    asyncio.run(provider.register_client(client))
    approval_url = asyncio.run(provider.authorize(client, _params()))
    request_id = parse_qs(urlparse(approval_url).query)["request_id"][0]

    for _attempt in range(5):
        with pytest.raises(OAuthApprovalError, match="Invalid Biologix access token"):
            asyncio.run(
                provider.approve_authorization(
                    request_id=request_id,
                    supplied_token="wrong-token",
                )
            )
    with pytest.raises(OAuthApprovalError, match="not found or expired"):
        asyncio.run(
            provider.approve_authorization(
                request_id=request_id,
                supplied_token="approval-token-with-at-least-32-bytes",
            )
        )


def test_expired_access_tokens_and_revoked_tokens_are_rejected(
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    provider = _provider(tmp_path, clock=lambda: now[0])
    client = _client()
    asyncio.run(provider.register_client(client))
    approval_url = asyncio.run(provider.authorize(client, _params()))
    request_id = parse_qs(urlparse(approval_url).query)["request_id"][0]
    redirect = asyncio.run(
        provider.approve_authorization(
            request_id,
            "approval-token-with-at-least-32-bytes",
        )
    )
    code = parse_qs(urlparse(redirect).query)["code"][0]
    authorization_code = asyncio.run(provider.load_authorization_code(client, code))
    assert authorization_code is not None
    tokens = asyncio.run(provider.exchange_authorization_code(client, authorization_code))
    access = asyncio.run(provider.load_access_token(tokens.access_token))
    assert access is not None

    asyncio.run(provider.revoke_token(access))
    assert asyncio.run(provider.load_access_token(tokens.access_token)) is None

    now[0] += 3_601
    assert asyncio.run(provider.load_access_token("missing")) is None


def test_legacy_bearer_token_remains_valid_for_claude_code(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    token = asyncio.run(provider.load_access_token("legacy-bearer-token"))

    assert token is not None
    assert token.client_id == "claude-code-static"
    assert token.scopes == ["biologix:tools"]


def test_cleanup_removes_expired_pending_codes_and_tokens(tmp_path: Path) -> None:
    now = [1_000.0]
    provider = _provider(tmp_path, clock=lambda: now[0])
    client = _client()
    asyncio.run(provider.register_client(client))
    approval_url = asyncio.run(provider.authorize(client, _params()))
    request_id = parse_qs(urlparse(approval_url).query)["request_id"][0]
    redirect = asyncio.run(
        provider.approve_authorization(
            request_id,
            "approval-token-with-at-least-32-bytes",
        )
    )
    code = parse_qs(urlparse(redirect).query)["code"][0]
    authorization_code = asyncio.run(provider.load_authorization_code(client, code))
    assert authorization_code is not None
    tokens = asyncio.run(provider.exchange_authorization_code(client, authorization_code))
    second_approval_url = asyncio.run(provider.authorize(client, _params()))
    second_request_id = parse_qs(urlparse(second_approval_url).query)["request_id"][0]

    now[0] += 31 * 24 * 3_600
    removed = provider.cleanup_expired()

    assert removed >= 3
    assert asyncio.run(provider.get_pending_approval(second_request_id)) is None
    assert asyncio.run(provider.load_access_token(tokens.access_token)) is None
    assert (
        asyncio.run(provider.load_refresh_token(client, tokens.refresh_token or ""))
        is None
    )


def test_authorization_rejects_token_for_another_resource(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    client = _client()
    asyncio.run(provider.register_client(client))
    params = _params().model_copy(update={"resource": "https://attacker.example/mcp"})

    with pytest.raises(AuthorizeError, match="resource"):
        asyncio.run(provider.authorize(client, params))


def test_dynamic_registration_has_a_bounded_persistent_client_set(
    tmp_path: Path,
) -> None:
    provider = BiologixOAuthProvider(
        issuer_url="https://example.modal.run",
        resource_url="https://example.modal.run/mcp",
        storage_path=tmp_path / "oauth-state.enc",
        approval_token="approval-token-with-at-least-32-bytes",
        storage_key="storage-key-with-at-least-32-random-bytes",
        legacy_bearer_token="legacy-bearer-token",
        max_registered_clients=1,
    )
    asyncio.run(provider.register_client(_client()))
    second_client = _client().model_copy(update={"client_id": "second-client"})

    with pytest.raises(RegistrationError, match="registration limit"):
        asyncio.run(provider.register_client(second_client))


def test_store_serializes_mutations_across_provider_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oauth-state.enc"
    key = "storage-key-with-at-least-32-random-bytes"
    first_store = EncryptedOAuthStore(path, key)
    second_store = EncryptedOAuthStore(path, key)
    first_started = threading.Event()

    def slow_first_mutation(state):
        state["clients"]["first"] = {"client_id": "first"}
        first_started.set()
        time.sleep(0.2)

    first_thread = threading.Thread(
        target=lambda: first_store.mutate(slow_first_mutation)
    )
    first_thread.start()
    assert first_started.wait(timeout=1)
    second_store.mutate(
        lambda state: state["clients"].update(
            {"second": {"client_id": "second"}}
        )
    )
    first_thread.join(timeout=1)

    assert set(first_store.read()["clients"]) == {"first", "second"}
