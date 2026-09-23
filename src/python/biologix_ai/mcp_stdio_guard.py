#!/usr/bin/env python3
"""
Serialize MCP tool calls per client to prevent parallel CallTool deadlocks.

Stdio MCP uses a single JSON-RPC pipe; concurrent tool handlers can block stdout
and appear as client-side timeouts. This module wraps every FastMCP tool handler
with a non-blocking lock and returns MCP_BUSY immediately when contended.

The lock is per client (``mcp_client.client_key``): stdio has one client, so
behaviour there is unchanged; over HTTP one user's long OpenMM call no longer
turns every other user's call into MCP_BUSY.
"""

from __future__ import annotations

import functools
import inspect
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Optional

from biologix_ai.mcp_client import client_key
from biologix_ai.mcp_tool_guard import log_tool_event

MCP_BUSY_ERROR = "MCP_BUSY"
_LOCKS_GUARD = Lock()
_CLIENT_LOCKS: Dict[str, Lock] = {}


def client_lock(key: Optional[str] = None) -> Lock:
    """The serialization lock of one MCP client (the calling client by default)."""
    key = key or client_key()
    with _LOCKS_GUARD:
        lock = _CLIENT_LOCKS.get(key)
        if lock is None:
            lock = _CLIENT_LOCKS[key] = Lock()
        return lock


def mcp_busy_json(session_dir: Optional[Path] = None) -> str:
    """Return JSON string for a contended MCP tool call."""
    payload = {
        "ok": False,
        "error": MCP_BUSY_ERROR,
        "hint": (
            "Another biologix-ai MCP tool call is in flight. "
            "Call biologix-ai MCP tools one at a time and wait for JSON before the next."
        ),
    }
    log_tool_event(
        session_dir,
        tool="mcp_stdio_guard",
        status="failed",
        stage="serialize",
        error=MCP_BUSY_ERROR,
        message="parallel MCP call rejected",
    )
    return json.dumps(payload, indent=2)


def _extract_session_dir(sig: inspect.Signature, args: tuple, kwargs: dict) -> Optional[Path]:
    try:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return None
    for key in ("run_dir", "session_dir", "artifacts_dir"):
        raw = bound.arguments.get(key)
        if raw:
            try:
                return Path(str(raw)).resolve()
            except (OSError, ValueError):
                continue
    return None


def _wrap_tool_fn(name: str, fn: Callable[..., str]) -> Callable[..., str]:
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        lock = client_lock()
        if not lock.acquire(blocking=False):
            session = _extract_session_dir(sig, args, kwargs)
            return mcp_busy_json(session)
        try:
            return fn(*args, **kwargs)
        finally:
            lock.release()

    setattr(wrapped, "_biologix_mcp_serial_guard", True)
    return wrapped


# Tools that must answer while the client's own job holds its lock.
UNSERIALIZED_TOOLS = frozenset({"await_biologix_job", "biologix_runtime_status"})


def install_stdio_guards(mcp: Any) -> None:
    """Wrap registered synchronous FastMCP tool handlers with the serialization lock.

    Async handlers and :data:`UNSERIALIZED_TOOLS` are left alone: waiting for a
    running job must not be refused because that job holds the lock.
    """
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return
    for name, tool in list(tools.items()):
        fn = getattr(tool, "fn", None)
        already_wrapped = getattr(fn, "_biologix_mcp_serial_guard", False)
        if str(name) in UNSERIALIZED_TOOLS or inspect.iscoroutinefunction(fn):
            continue
        if callable(fn) and not already_wrapped:
            tool.fn = _wrap_tool_fn(str(name), fn)
