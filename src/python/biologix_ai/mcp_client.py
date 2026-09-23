#!/usr/bin/env python3
"""
Identify the MCP client connection that made the current tool call.

One Modal web container serves every connected client (Claude, ChatGPT,
Cursor, ...). Protocol state, the serialization lock, and background jobs are
keyed by this id so one client's run never blocks or redirects another's.
"""

from __future__ import annotations

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


def client_key() -> str:
    """Streamable HTTP ``Mcp-Session-Id``, else the server session object, else ``local``.

    stdio serves exactly one client, so it always maps to ``local``. The value
    survives ``contextvars.copy_context()`` into worker threads.
    """
    ctx = _request_context()
    if ctx is None:
        return LOCAL_CLIENT
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            session_id = headers.get(_SESSION_HEADER)
        except Exception:
            session_id = None
        if session_id:
            return f"mcp:{session_id}"
    session = getattr(ctx, "session", None)
    return f"session:{id(session)}" if session is not None else LOCAL_CLIENT
