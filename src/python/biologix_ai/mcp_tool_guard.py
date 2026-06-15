#!/usr/bin/env python3
"""
Session-scoped logging and structured envelopes for expensive MCP tools.

Writes progress to stderr (visible in the terminal running the MCP server) and
persists events under ``<session>/tool_events.jsonl`` / ``tool_errors.log``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

_TRUNCATE_HEAVY_KEYS = (
    "evaluation_progress",
    "structure_artifact_paths",
    "evaluation_note",
    "traceback",
    "property_analysis",
)


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
            or "Check <session>/tool_events.jsonl and tool_errors.log. "
            "If MCP timed out, the session latches to CLI-only — do not call MCP again; "
            "use bash CLI per .opencode/MCP_CLI_FALLBACK.md.",
        )
    return out


def truncate_mcp_json(payload: Dict[str, Any], max_bytes: int = 65536) -> Dict[str, Any]:
    """Strip heavy fields when MCP JSON would exceed stdio-friendly size."""
    encoded = json.dumps(payload, default=str).encode("utf-8")
    if len(encoded) <= max_bytes:
        return payload
    trimmed = dict(payload)
    for key in _TRUNCATE_HEAVY_KEYS:
        trimmed.pop(key, None)
    if len(json.dumps(trimmed, default=str).encode("utf-8")) > max_bytes:
        trimmed = {
            "ok": trimmed.get("ok", True),
            "truncated": True,
            "hint": "Full detail in <session>/tool_events.jsonl",
            "candidate_outcomes": trimmed.get("candidate_outcomes"),
            "error": trimmed.get("error"),
        }
    else:
        trimmed["truncated"] = True
        trimmed.setdefault(
            "hint",
            "Response truncated for MCP stdio; see <session>/tool_events.jsonl for full detail.",
        )
    return trimmed


def instant_mcp_timeout_s() -> float:
    """Wall-clock cap for session/audit/catalog MCP tools (default 30 s)."""
    raw = os.environ.get("BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 30.0
    return value if value > 0 else 30.0


def _invoke_with_timeout(fn: Callable[[], Dict[str, Any]], timeout_s: Optional[float]) -> Dict[str, Any]:
    if timeout_s is None or timeout_s <= 0:
        result = fn()
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        return result
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            result = future.result(timeout=timeout_s)
        except FuturesTimeoutError:
            return {
                "ok": False,
                "error": f"MCP tool exceeded instant timeout ({timeout_s}s)",
                "stage": "timeout",
            }
    if not isinstance(result, dict):
        return {"ok": True, "result": result}
    return result


def run_instant_mcp_tool(
    tool: str,
    session_dir: Optional[Union[str, Path]],
    fn: Callable[[], Dict[str, Any]],
    *,
    stage: str = "execute",
    artifact_key: str = "",
    failure_hint: str = "",
) -> Dict[str, Any]:
    """Run a fast session/audit MCP tool with ``BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S``."""
    cap = instant_mcp_timeout_s()
    hint = failure_hint or (
        f"Instant MCP tool exceeded {cap}s — stdio may be blocked by a long-running call. "
        "After any MCP timeout the session latches to CLI-only per .opencode/MCP_CLI_FALLBACK.md."
    )
    return run_guarded_tool(
        tool,
        session_dir,
        fn,
        stage=stage,
        artifact_key=artifact_key,
        failure_hint=hint,
        timeout_s=cap,
    )


def log_tool_budget(
    session_dir: Optional[Union[str, Path]],
    *,
    tool: str,
    candidate_timeout_s: Optional[float] = None,
    mcp_timeout_ms: Optional[int] = None,
) -> None:
    """Log expected wall-clock budget at MCP tool start."""
    budgets = []
    if candidate_timeout_s is not None and candidate_timeout_s > 0:
        budgets.append(float(candidate_timeout_s))
    if mcp_timeout_ms is not None and mcp_timeout_ms > 0:
        budgets.append(mcp_timeout_ms / 1000.0)
    expected = min(budgets) if budgets else None
    extra: Dict[str, Any] = {}
    if candidate_timeout_s is not None:
        extra["candidate_timeout_s"] = candidate_timeout_s
    if mcp_timeout_ms is not None:
        extra["mcp_timeout_ms"] = mcp_timeout_ms
    if expected is not None:
        extra["expected_budget_s"] = round(expected, 1)
    log_tool_event(
        Path(session_dir).resolve() if session_dir else None,
        tool=tool,
        status="budget",
        stage="plan",
        message=f"expected_budget_s={expected}" if expected is not None else "",
        extra=extra or None,
    )


class McpProgressReporter:
    """Emit MCP ``notifications/progress`` plus stderr / tool_events mirrors."""

    def __init__(
        self,
        ctx: Optional["Context"],
        *,
        tool: str = "",
        session_dir: Optional[Union[str, Path]] = None,
        interval_s: float = 15.0,
    ) -> None:
        self._ctx = ctx
        self._tool = tool
        self._session = Path(session_dir).resolve() if session_dir else None
        self._interval_s = max(0.0, float(interval_s))
        self._last_emit = 0.0
        self._counter = 0.0

    def heartbeat(
        self,
        message: str,
        *,
        stage: str = "",
        progress: Optional[float] = None,
        total: Optional[float] = None,
        force: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.perf_counter()
        if not force and self._interval_s > 0 and (now - self._last_emit) < self._interval_s:
            return
        self._last_emit = now
        if progress is None:
            self._counter += 1.0
            progress = self._counter
        if self._ctx is not None:
            try:
                self._ctx.report_progress(progress, total, message)
            except Exception:
                pass
        log_tool_event(
            self._session,
            tool=self._tool or "mcp_tool",
            status="progress",
            stage=stage or "heartbeat",
            message=message,
            extra=extra,
        )


def run_guarded_tool(
    tool: str,
    session_dir: Optional[Union[str, Path]],
    fn: Callable[[], Dict[str, Any]],
    *,
    stage: str = "execute",
    artifact_key: str = "",
    failure_hint: str = "",
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run *fn* with start/complete/failed logging and structured error capture.

    *fn* must return a dict (typically with ``ok`` bool). When *timeout_s* is set, exceed
    raises a structured ``stage=timeout`` failure (used for instant session/audit tools).
    """
    sess = Path(session_dir).resolve() if session_dir else None
    t0 = time.perf_counter()
    log_tool_event(sess, tool=tool, status="started", stage=stage)

    try:
        result = _invoke_with_timeout(fn, timeout_s)
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
            or "Unexpected tool exception; inspect tool_errors.log. "
            "If MCP timed out, session latches to CLI-only — no further MCP; "
            "use bash CLI per .opencode/MCP_CLI_FALLBACK.md.",
        )
