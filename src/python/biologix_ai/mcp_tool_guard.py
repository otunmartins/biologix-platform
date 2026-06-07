#!/usr/bin/env python3
"""
Session-scoped logging and structured envelopes for expensive MCP tools.

Writes progress to stderr (visible in the terminal running the MCP server) and
persists events under ``<session>/tool_events.jsonl`` / ``tool_errors.log``.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def log_tool_event(
    session_dir: Optional[Path],
    *,
    tool: str,
    status: str,
    stage: str = "",
    elapsed_seconds: Optional[float] = None,
    message: str = "",
    artifact: str = "",
    error: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a JSONL event and mirror a short line to stderr."""
    payload: Dict[str, Any] = {
        "ts": _utc_now(),
        "tool": tool,
        "status": status,
    }
    if stage:
        payload["stage"] = stage
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = round(elapsed_seconds, 3)
    if message:
        payload["message"] = message
    if artifact:
        payload["artifact"] = artifact
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)

    parts = [f"[biologix-ai] tool={tool}", f"status={status}"]
    if stage:
        parts.append(f"stage={stage}")
    if elapsed_seconds is not None:
        parts.append(f"elapsed={elapsed_seconds:.1f}s")
    if message:
        parts.append(message)
    if artifact:
        parts.append(f"artifact={artifact}")
    if error:
        parts.append(f"error={error[:200]}")
    _stderr(" ".join(parts))

    if session_dir is None:
        return
    try:
        session_dir = Path(session_dir).resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        with (session_dir / "tool_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
        if error:
            with (session_dir / "tool_errors.log").open("a", encoding="utf-8") as f:
                f.write(f"{_utc_now()} [{tool}] {error}\n")
    except OSError:
        pass


def _append_traceback(session_dir: Optional[Path], tool: str, tb: str) -> None:
    if session_dir is None or not tb:
        return
    try:
        with (Path(session_dir) / "tool_errors.log").open("a", encoding="utf-8") as f:
            f.write(f"{_utc_now()} [{tool}] traceback:\n{tb}\n")
    except OSError:
        pass


def enrich_tool_result(
    result: Dict[str, Any],
    *,
    tool: str,
    elapsed_seconds: float,
    status: str,
    stage: str = "done",
    hint: str = "",
) -> Dict[str, Any]:
    """Add standard metadata fields expected by OpenCode agents."""
    out = dict(result)
    out.setdefault("tool", tool)
    out.setdefault("status", status)
    out.setdefault("stage", stage)
    out["elapsed_seconds"] = round(elapsed_seconds, 3)
    if hint:
        out.setdefault("hint", hint)
    if not out.get("ok", True):
        out.setdefault(
            "hint",
            hint
            or "Check <session>/tool_events.jsonl and tool_errors.log; retry or simplify inputs.",
        )
    return out


def run_guarded_tool(
    tool: str,
    session_dir: Optional[Union[str, Path]],
    fn: Callable[[], Dict[str, Any]],
    *,
    stage: str = "execute",
    artifact_key: str = "",
    failure_hint: str = "",
) -> Dict[str, Any]:
    """
    Run *fn* with start/complete/failed logging and structured error capture.

    *fn* must return a dict (typically with ``ok`` bool).
    """
    sess = Path(session_dir).resolve() if session_dir else None
    t0 = time.perf_counter()
    log_tool_event(sess, tool=tool, status="started", stage=stage)

    try:
        result = fn()
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        elapsed = time.perf_counter() - t0
        ok = bool(result.get("ok", True))
        artifact = ""
        if artifact_key and result.get(artifact_key):
            artifact = str(result[artifact_key])
        status = "completed" if ok else "failed"
        log_tool_event(
            sess,
            tool=tool,
            status=status,
            stage=stage,
            elapsed_seconds=elapsed,
            artifact=artifact,
            error="" if ok else str(result.get("error", "unknown error")),
        )
        return enrich_tool_result(
            result,
            tool=tool,
            elapsed_seconds=elapsed,
            status=status,
            stage=stage,
            hint="" if ok else failure_hint,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        err = str(exc)
        log_tool_event(
            sess,
            tool=tool,
            status="failed",
            stage=stage,
            elapsed_seconds=elapsed,
            error=err,
        )
        _append_traceback(sess, tool, tb)
        return enrich_tool_result(
            {
                "ok": False,
                "error": err,
                "traceback": tb,
            },
            tool=tool,
            elapsed_seconds=elapsed,
            status="failed",
            stage=stage,
            hint=failure_hint
            or "Unexpected tool exception; inspect tool_errors.log in the session folder.",
        )
