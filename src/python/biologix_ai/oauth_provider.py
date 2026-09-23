"""Persistent OAuth 2.1 provider for Claude Web MCP connections.

The provider uses MCP SDK authorization routes, encrypts all mutable OAuth
state at rest, and retains the existing static bearer token for Claude Code.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


OAUTH_SCOPE = "biologix:tools"
AUTHORIZATION_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3_600
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3_600
PENDING_APPROVAL_TTL_SECONDS = 600
MAX_APPROVAL_ATTEMPTS = 5
MAX_REGISTERED_CLIENTS = 1_000


class OAuthApprovalError(RuntimeError):
    """Raised when an approval request is missing, expired, or unauthorized."""


class _AdvisoryFileLock:
    """Process-safe advisory lock that preserves frozen OAuth exceptions."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_file: Any = None

    def __enter__(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._path.open("a+b")
        os.chmod(self._path, 0o600)
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)

    def __exit__(self, *_exc_info: object) -> None:
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()


def _token_hash(token: str) -> str:
    """Return a stable non-reversible lookup key for an OAuth token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet_key(secret: str) -> bytes:
    """Derive a Fernet key from an arbitrary high-entropy deployment secret."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


class EncryptedOAuthStore:
    """Atomic encrypted JSON storage suitable for a mounted Modal Volume."""

    def __init__(self, path: Path, encryption_secret: str) -> None:
        if len(encryption_secret) < 32:
            raise ValueError("BIOLOGIX_OAUTH_STORAGE_KEY must contain at least 32 characters")
        self.path = path
        self._fernet = Fernet(_fernet_key(encryption_secret))
        self._lock = threading.RLock()
        self._lock_path = path.with_name(f".{path.name}.lock")

    @staticmethod
    def empty_state() -> dict[str, dict[str, Any]]:
        """Return a new empty OAuth state document."""
        return {
            "clients": {},
            "pending": {},
            "authorization_codes": {},
            "access_tokens": {},
            "refresh_tokens": {},
        }

    def _process_lock(self) -> _AdvisoryFileLock:
        """Hold an advisory lock shared by all provider processes."""
        return _AdvisoryFileLock(self._lock_path)

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        """Decrypt state while the caller holds both store locks."""
        if not self.path.is_file():
            return self.empty_state()
        try:
            plaintext = self._fernet.decrypt(self.path.read_bytes())
            payload = json.loads(plaintext)
        except (InvalidToken, json.JSONDecodeError, OSError) as error:
            raise RuntimeError(
                f"OAuth state at {self.path} is unreadable or uses the wrong key"
            ) from error
        state = self.empty_state()
        for key in state:
            value = payload.get(key, {})
            if not isinstance(value, dict):
                raise RuntimeError(f"OAuth state section {key!r} is not a mapping")
            state[key] = value
        return state

    def read(self) -> dict[str, dict[str, Any]]:
        """Decrypt and return the current state document."""
        with self._lock, self._process_lock():
            return self._read_unlocked()

    def _write_unlocked(self, state: dict[str, dict[str, Any]]) -> None:
        """Atomically replace state while the caller holds both store locks."""
        plaintext = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = self._fernet.encrypt(plaintext)
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_bytes(encrypted)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def write(self, state: dict[str, dict[str, Any]]) -> None:
        """Encrypt and atomically replace the current state document."""
        with self._lock, self._process_lock():
            self._write_unlocked(state)

    def mutate(self, operation: Callable[[dict[str, dict[str, Any]]], Any]) -> Any:
        """Apply one state mutation under the process lock and persist it."""
        with self._lock, self._process_lock():
            state = self._read_unlocked()
            try:
                result = operation(state)
            except Exception:
                self._write_unlocked(state)
                raise
            self._write_unlocked(state)
            return result


class BiologixOAuthProvider:
    """MCP OAuth provider with DCR, approval, refresh, and legacy bearer support."""

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        storage_path: Path,
        approval_token: str,
        storage_key: str,
        legacy_bearer_token: str,
        clock: Callable[[], float] = time.time,
        max_registered_clients: int = MAX_REGISTERED_CLIENTS,
    ) -> None:
        if len(approval_token) < 32:
            raise ValueError(
                "BIOLOGIX_OAUTH_APPROVAL_TOKEN must contain at least 32 characters"
            )
        if not legacy_bearer_token.strip():
            raise ValueError("BIOLOGIX_MCP_TOKEN must be configured")
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self._approval_token = approval_token
        self._legacy_bearer_token = legacy_bearer_token
        self._clock = clock
        self._max_registered_clients = max_registered_clients
        self._store = EncryptedOAuthStore(storage_path, storage_key)

    def _now(self) -> float:
        return float(self._clock())

    @staticmethod
    def _cleanup_state(
        state: dict[str, dict[str, Any]],
        now: float,
    ) -> int:
        """Remove expired records from a loaded state document."""
        removed = 0
        expiry_fields = {
            "clients": "client_secret_expires_at",
            "pending": "expires_at",
            "authorization_codes": "expires_at",
            "access_tokens": "expires_at",
            "refresh_tokens": "expires_at",
        }
        for section, expiry_field in expiry_fields.items():
            expired_keys = [
                key
                for key, record in state[section].items()
                if isinstance(record, dict)
                and record.get(expiry_field) is not None
                and float(record[expiry_field]) < now
            ]
            for key in expired_keys:
                state[section].pop(key, None)
                removed += 1
        return removed

    def cleanup_expired(self) -> int:
        """Delete expired OAuth records and return the removal count."""
        now = self._now()
        return int(
            self._store.mutate(
                lambda state: self._cleanup_state(state, now)
            )
        )

    @staticmethod
    def _scopes(scope: str | None) -> list[str]:
        return scope.split() if scope else [OAUTH_SCOPE]

    async def get_client(
        self,
        client_id: str,
    ) -> OAuthClientInformationFull | None:
        """Load one dynamically registered OAuth client."""
        raw_client = self._store.read()["clients"].get(client_id)
        if not isinstance(raw_client, dict):
            return None
        client = OAuthClientInformationFull.model_validate(raw_client)
        if (
            client.client_secret_expires_at is not None
            and client.client_secret_expires_at < self._now()
        ):
            return None
        return client

    async def register_client(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        """Persist one dynamically registered OAuth client."""
        if not client_info.client_id:
            raise ValueError("registered OAuth client has no client_id")

        def register(state: dict[str, dict[str, Any]]) -> None:
            self._cleanup_state(state, self._now())
            if (
                client_info.client_id not in state["clients"]
                and len(state["clients"]) >= self._max_registered_clients
            ):
                raise RegistrationError(
                    "invalid_client_metadata",
                    "Dynamic client registration limit reached",
                )
            state["clients"][client_info.client_id or ""] = client_info.model_dump(
                mode="json"
            )

        self._store.mutate(register)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Create a pending browser approval and return its local approval URL."""
        if not client.client_id:
            raise ValueError("OAuth client has no client_id")
        if (
            params.resource is not None
            and params.resource.rstrip("/") != self.resource_url.rstrip("/")
        ):
            raise AuthorizeError(
                "invalid_request",
                "Requested resource does not match the Biologix MCP resource",
            )
        request_id = secrets.token_urlsafe(32)
        pending = {
            "client_id": client.client_id,
            "params": params.model_dump(mode="json"),
            "expires_at": self._now() + PENDING_APPROVAL_TTL_SECONDS,
            "attempts": 0,
        }

        def save_pending(state: dict[str, dict[str, Any]]) -> None:
            self._cleanup_state(state, self._now())
            state["pending"][request_id] = pending

        self._store.mutate(save_pending)
        return f"{self.issuer_url}/oauth/approve?{urlencode({'request_id': request_id})}"

    async def get_pending_approval(self, request_id: str) -> dict[str, str] | None:
        """Return display-safe context for one pending browser approval."""
        state = self._store.read()
        pending = state["pending"].get(request_id)
        if not isinstance(pending, dict) or pending.get("expires_at", 0) < self._now():
            return None
        client = state["clients"].get(str(pending.get("client_id", "")))
        if not isinstance(client, dict):
            return None
        params = pending.get("params") or {}
        return {
            "client_name": str(client.get("client_name") or "Claude"),
            "redirect_uri": str(params.get("redirect_uri") or ""),
            "scope": " ".join(params.get("scopes") or [OAUTH_SCOPE]),
        }

    async def approve_authorization(
        self,
        request_id: str,
        supplied_token: str,
        approved: bool = True,
    ) -> str:
        """Approve or deny a pending browser request and return the client redirect."""
        now = self._now()

        def complete(state: dict[str, dict[str, Any]]) -> str:
            self._cleanup_state(state, now)
            pending = state["pending"].get(request_id)
            if (
                not isinstance(pending, dict)
                or float(pending.get("expires_at", 0)) < now
            ):
                state["pending"].pop(request_id, None)
                raise OAuthApprovalError("Approval request not found or expired")

            if not hmac.compare_digest(
                supplied_token.encode("utf-8"),
                self._approval_token.encode("utf-8"),
            ):
                pending["attempts"] = int(pending.get("attempts", 0)) + 1
                if pending["attempts"] >= MAX_APPROVAL_ATTEMPTS:
                    state["pending"].pop(request_id, None)
                raise OAuthApprovalError("Invalid Biologix access token")

            params = AuthorizationParams.model_validate(pending["params"])
            state["pending"].pop(request_id, None)
            if not approved:
                return construct_redirect_uri(
                    str(params.redirect_uri),
                    error="access_denied",
                    state=params.state,
                )

            code_value = secrets.token_urlsafe(32)
            code = AuthorizationCode(
                code=code_value,
                scopes=params.scopes or [OAUTH_SCOPE],
                expires_at=now + AUTHORIZATION_CODE_TTL_SECONDS,
                client_id=str(pending["client_id"]),
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=(
                    params.redirect_uri_provided_explicitly
                ),
                resource=params.resource or self.resource_url,
                subject="biologix-owner",
            )
            state["authorization_codes"][code_value] = code.model_dump(mode="json")
            return construct_redirect_uri(
                str(params.redirect_uri),
                code=code_value,
                state=params.state,
            )

        return self._store.mutate(complete)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load a live authorization code for the requesting client."""
        raw_code = self._store.read()["authorization_codes"].get(authorization_code)
        if not isinstance(raw_code, dict):
            return None
        code = AuthorizationCode.model_validate(raw_code)
        if code.client_id != client.client_id or code.expires_at < self._now():
            return None
        return code

    def _issue_tokens(
        self,
        state: dict[str, dict[str, Any]],
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None,
    ) -> OAuthToken:
        now = int(self._now())
        access_value = secrets.token_urlsafe(32)
        refresh_value = secrets.token_urlsafe(48)
        access = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource or self.resource_url,
            subject=subject,
        )
        refresh = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
            resource=resource or self.resource_url,
            subject=subject,
        )
        access_hash = _token_hash(access_value)
        refresh_hash = _token_hash(refresh_value)
        state["access_tokens"][access_hash] = access.model_copy(
            update={"token": access_hash}
        ).model_dump(mode="json")
        state["refresh_tokens"][refresh_hash] = refresh.model_copy(
            update={"token": refresh_hash}
        ).model_dump(mode="json")
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh_value,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Consume one authorization code and issue access and refresh tokens."""

        def exchange(state: dict[str, dict[str, Any]]) -> OAuthToken:
            self._cleanup_state(state, self._now())
            raw_code = state["authorization_codes"].pop(
                authorization_code.code,
                None,
            )
            if not isinstance(raw_code, dict):
                raise TokenError("invalid_grant", "authorization code was already used")
            stored_code = AuthorizationCode.model_validate(raw_code)
            if (
                stored_code.client_id != client.client_id
                or stored_code.expires_at < self._now()
            ):
                raise TokenError("invalid_grant", "authorization code is invalid")
            return self._issue_tokens(
                state,
                client_id=stored_code.client_id,
                scopes=stored_code.scopes,
                resource=stored_code.resource,
                subject=stored_code.subject,
            )

        return self._store.mutate(exchange)

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load one live refresh token for its registered client."""
        raw_token = self._store.read()["refresh_tokens"].get(
            _token_hash(refresh_token)
        )
        if not isinstance(raw_token, dict):
            return None
        token = RefreshToken.model_validate(raw_token).model_copy(
            update={"token": refresh_token}
        )
        if (
            token.client_id != client.client_id
            or (token.expires_at is not None and token.expires_at < self._now())
        ):
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate a refresh token and issue a new access-token pair."""

        def exchange(state: dict[str, dict[str, Any]]) -> OAuthToken:
            self._cleanup_state(state, self._now())
            raw_token = state["refresh_tokens"].pop(
                _token_hash(refresh_token.token),
                None,
            )
            if not isinstance(raw_token, dict):
                raise TokenError("invalid_grant", "refresh token was already used")
            stored_token = RefreshToken.model_validate(raw_token)
            if (
                stored_token.client_id != client.client_id
                or (
                    stored_token.expires_at is not None
                    and stored_token.expires_at < self._now()
                )
            ):
                raise TokenError("invalid_grant", "refresh token is invalid")
            if not set(scopes).issubset(stored_token.scopes):
                raise TokenError("invalid_scope", "requested scope was not granted")
            return self._issue_tokens(
                state,
                client_id=stored_token.client_id,
                scopes=scopes,
                resource=stored_token.resource,
                subject=stored_token.subject,
            )

        return self._store.mutate(exchange)

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Validate either a generated OAuth token or the legacy Claude Code token."""
        if hmac.compare_digest(
            token.encode("utf-8"),
            self._legacy_bearer_token.encode("utf-8"),
        ):
            return AccessToken(
                token=token,
                client_id="claude-code-static",
                scopes=[OAUTH_SCOPE],
                expires_at=None,
                resource=self.resource_url,
                subject="biologix-owner",
            )
        raw_token = self._store.read()["access_tokens"].get(_token_hash(token))
        if not isinstance(raw_token, dict):
            return None
        access_token = AccessToken.model_validate(raw_token).model_copy(
            update={"token": token}
        )
        if (
            access_token.expires_at is not None
            and access_token.expires_at < self._now()
        ):
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke the supplied access or refresh token."""

        def revoke(state: dict[str, dict[str, Any]]) -> None:
            self._cleanup_state(state, self._now())
            token_key = _token_hash(token.token)
            state["access_tokens"].pop(token_key, None)
            state["refresh_tokens"].pop(token_key, None)

        self._store.mutate(revoke)
