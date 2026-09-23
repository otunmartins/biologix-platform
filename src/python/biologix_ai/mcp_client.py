#!/usr/bin/env python3
"""
Identify the MCP client that made the current tool call.

One Modal web container serves every connected client (Claude, ChatGPT,
Cursor, ...). Protocol state, the serialization lock, and background jobs are
keyed by this id so one client's run never blocks or redirects another's.

The key is the OAuth client (the connector registration) when the request is
authenticated, not the MCP session: ChatGPT opens a new Streamable HTTP session
(a new ``Mcp-Session-Id``) for every tool call, so a session-keyed gate forgot
``begin_biologix_discovery`` between two calls of the same chat. The session id
is used only for unauthenticated HTTP, and stdio is always ``local``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

LOCAL_CLIENT = "local"
_SESSION_HEADER = "mcp-session-id"


def _request_context() -> Optional[Any]:
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:
        return None
    try:
        return request_ctx.get()
    except LookupError:
        return None


def _authenticated_client(request: Any) -> str:
    """OAuth ``client_id`` of the request, from Starlette's auth scope or MCP's auth context."""
    scope = getattr(request, "scope", None)
    user = scope.get("user") if isinstance(scope, dict) else None
    token = getattr(user, "access_token", None)
    if token is None:
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token

            token = get_access_token()
        except ImportError:
            token = None
    client_id = getattr(token, "client_id", "") if token is not None else ""
    return str(client_id or "")


def client_key() -> str:
    """``oauth:<client_id>``, else ``bearer:<hash>``, else ``mcp:<Mcp-Session-Id>``, else ``local``.

    The value survives ``contextvars.copy_context()`` into worker threads.
    """
    ctx = _request_context()
    if ctx is None:
        return LOCAL_CLIENT
    request = getattr(ctx, "request", None)
    client_id = _authenticated_client(request)
    if client_id:
        return f"oauth:{client_id}"
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            authorization = headers.get("authorization") or ""
            session_id = headers.get(_SESSION_HEADER)
        except Exception:
            authorization, session_id = "", None
        if authorization:
            # Bearer-only deployments: one credential is one client. Hashed so
            # the token never appears in keys or logs.
            return "bearer:" + hashlib.sha256(authorization.encode("utf-8")).hexdigest()[:16]
        if session_id:
            return f"mcp:{session_id}"
    session = getattr(ctx, "session", None)
    return f"session:{id(session)}" if session is not None else LOCAL_CLIENT
