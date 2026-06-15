#!/usr/bin/env python3
"""
Serialize MCP stdio tool calls to prevent parallel CallTool deadlocks.

Stdio MCP uses a single JSON-RPC pipe; concurrent tool handlers can block stdout
and appear as client-side timeouts. This module wraps every FastMCP tool handler
with a non-blocking global lock and returns MCP_BUSY immediately when contended.
"""

from __future__ import annotations

import functools
import inspect
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Optional

from biologix_ai.mcp_tool_guard import log_tool_event

MCP_BUSY_ERROR = "MCP_BUSY"
_TOOL_LOCK = Lock()


def mcp_busy_json(session_dir: Optional[Path] = None) -> str:
    """Return JSON string for a contended stdio MCP tool call."""
    payload = {
        "ok": False,
        "error": MCP_BUSY_ERROR,
        "hint": (
            "Another biologix-ai MCP tool call is in flight. "
            "Call biologix-ai MCP tools one at a time and wait for JSON before the next. "
            "If a tool times out for any reason, the session latches to CLI-only mode — do not call MCP again; "
            "use bash CLI per .opencode/MCP_CLI_FALLBACK.md."
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
        if not _TOOL_LOCK.acquire(blocking=False):
            session = _extract_session_dir(sig, args, kwargs)
            return mcp_busy_json(session)
        try:
            return fn(*args, **kwargs)
        finally:
            _TOOL_LOCK.release()

    return wrapped


def install_stdio_guards(mcp: Any) -> None:
    """Wrap all registered FastMCP tool handlers with the stdio serialization lock."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return
    for name, tool in list(tools.items()):
        fn = getattr(tool, "fn", None)
        if callable(fn):
            tool.fn = _wrap_tool_fn(str(name), fn)
