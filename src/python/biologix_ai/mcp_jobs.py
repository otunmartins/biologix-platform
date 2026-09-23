#!/usr/bin/env python3
"""
Run MCP tools off the event loop and hand long calls back as jobs.

FastMCP calls synchronous tools directly on the event loop, so one OpenMM call
used to freeze the whole HTTP server. :func:`install_job_runner` turns every
synchronous tool into an async handler that runs the tool in a worker thread
(with the caller's context variables, so the protocol gate and the per-client
lock still see the right client) and reports MCP progress while it waits.

Remote clients time out long tool calls at different points. When a call takes
longer than ``BIOLOGIX_TOOL_WAIT_S`` it keeps running, and the client gets
``status: "running"`` plus a ``job_id``; ``await_biologix_job`` waits again and
returns the finished result exactly as the tool would have. Over stdio the
default wait is unlimited, so local agents see no change.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from biologix_ai.mcp_client import client_key

JOB_RUNNING = "JOB_RUNNING"
AWAIT_TOOL = "await_biologix_job"
WAIT_ENV = "BIOLOGIX_TOOL_WAIT_S"
_PROGRESS_INTERVAL_S = 15.0
_FINISHED_TTL_S = 6 * 3600

# The server's event loop, visible to tool threads so they can send MCP progress
# notifications (``McpProgressReporter``) without blocking.
EVENT_LOOP: contextvars.ContextVar[Optional[asyncio.AbstractEventLoop]] = contextvars.ContextVar(
    "biologix_event_loop", default=None
)

# Tools that run while a job is pending: they report on it or never touch state.
PASS_THROUGH_TOOLS = {AWAIT_TOOL, "biologix_runtime_status"}


def tool_wait_s() -> float:
    """Seconds a tool call may block before it becomes a job (0 = wait until done)."""
    raw = os.environ.get(WAIT_ENV, "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    transport = os.environ.get("BIOLOGIX_MCP_TRANSPORT", "stdio").strip().lower()
    return 240.0 if transport in ("http", "streamable-http") else 0.0


@dataclass
class Job:
    job_id: str
    client: str
    tool: str
    future: Future
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None


_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.environ.get("BIOLOGIX_MCP_JOB_THREADS", "16") or 16),
    thread_name_prefix="biologix-tool",
)
_JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _prune() -> None:
    now = time.time()
    with _JOBS_LOCK:
        for job_id in [j for j, job in _JOBS.items() if job.finished and now - job.finished > _FINISHED_TTL_S]:
            _JOBS.pop(job_id, None)


def pending_job(client: str) -> Optional[Job]:
    """The unfinished job of *client*, if any."""
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.client == client and not job.future.done():
                return job
    return None


def _register(client: str, tool: str, future: Future) -> Job:
    job = Job(job_id=uuid.uuid4().hex[:16], client=client, tool=tool, future=future)

    def _mark(_f: Future) -> None:
        job.finished = time.time()

    future.add_done_callback(_mark)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
    _prune()
    return job


def running_json(job: Job) -> str:
    """Result returned while *job* is still running."""
    elapsed = round(time.time() - job.started, 1)
    return json.dumps(
        {
            "ok": True,
            "status": "running",
            "job_id": job.job_id,
            "tool": job.tool,
            "elapsed_s": elapsed,
            "protocol": {
                "stage": "job",
                "next_required_tool": AWAIT_TOOL,
                "next_arguments": {"job_id": job.job_id},
                "user_stop_allowed": False,
                "rule": (
                    f"{job.tool} is still running on the server ({elapsed} s so far). Call "
                    f"{AWAIT_TOOL} now with this job_id. Do not stop, summarize, estimate the "
                    "result, or call another Biologix tool first."
                ),
            },
        },
        indent=2,
    )


def job_running_refusal(job: Job, tool: str) -> str:
    """Refusal for a tool called while the client's job is still running."""
    payload = json.loads(running_json(job))
    payload.update(
        {
            "ok": False,
            "error": JOB_RUNNING,
            "not_a_failure": True,
            "refused_tool": tool,
            "reason": f"{job.tool} (job {job.job_id}) has not finished. Wait for it first.",
        }
    )
    return json.dumps(payload, indent=2)


async def _report(ctx: Any, progress: float, message: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress, None, message)
    except Exception:
        pass


async def _wait(future: Future, wait_s: float, ctx: Any, label: str) -> bool:
    """Wait for *future* up to *wait_s* (0 = forever), reporting progress. True when done."""
    wrapped = asyncio.wrap_future(future)
    started = time.monotonic()
    while True:
        remaining = None if wait_s <= 0 else wait_s - (time.monotonic() - started)
        if remaining is not None and remaining <= 0:
            return future.done()
        step = _PROGRESS_INTERVAL_S if remaining is None else min(_PROGRESS_INTERVAL_S, remaining)
        done, _ = await asyncio.wait({wrapped}, timeout=step)
        if done:
            return True
        elapsed = time.monotonic() - started
        await _report(ctx, elapsed, f"{label} running ({elapsed:.0f} s)")


def _result_text(future: Future) -> str:
    try:
        result = future.result()
    except Exception as exc:  # the tool raised instead of returning JSON
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "abort": True}, indent=2)
    return result if isinstance(result, str) else json.dumps(result, default=str)


def _current_context(server: Any) -> Any:
    try:
        return server.get_context()
    except Exception:
        return None


def _wrap(name: str, fn: Callable[..., Any], server: Any) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> str:
        client = client_key()
        if name not in PASS_THROUGH_TOOLS:
            job = pending_job(client)
            if job is not None:
                return job_running_refusal(job, name)
        call = functools.partial(fn, *args, **kwargs)
        EVENT_LOOP.set(asyncio.get_running_loop())
        future = _EXECUTOR.submit(contextvars.copy_context().run, call)
        ctx = _current_context(server)
        if await _wait(future, tool_wait_s(), ctx, name):
            return _result_text(future)
        return running_json(_register(client, name, future))

    setattr(wrapped, "_biologix_job_runner", True)
    # FastMCP validates arguments from the original signature.
    wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapped


def install_job_runner(server: Any) -> None:
    """Run every synchronous FastMCP tool in a worker thread (see module docstring).

    Install after ``install_protocol_gate`` and ``install_stdio_guards`` so the
    gate and the per-client lock run inside the worker thread with the tool.
    """
    tools = getattr(getattr(server, "_tool_manager", None), "_tools", None)
    if not isinstance(tools, dict):
        return
    for name, tool in tools.items():
        fn = getattr(tool, "fn", None)
        if not callable(fn) or getattr(fn, "_biologix_job_runner", False):
            continue
        if getattr(tool, "is_async", False) or inspect.iscoroutinefunction(fn):
            continue
        tool.fn = _wrap(str(name), fn, server)
        tool.is_async = True


async def await_job(job_id: str, wait_s: float = 0.0, ctx: Any = None) -> str:
    """Wait for a job of the calling client; return its result or a new ``running`` payload."""
    job_id = (job_id or "").strip()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return json.dumps(
            {
                "ok": False,
                "error": "JOB_NOT_FOUND",
                "job_id": job_id,
                "abort": True,
                "hint": (
                    "The server has no job with this id (it may have restarted). Call the "
                    "tool that started it again with the same arguments."
                ),
            },
            indent=2,
        )
    if job.client != client_key():
        return json.dumps({"ok": False, "error": "JOB_NOT_FOUND", "job_id": job_id, "abort": True})
    limit = wait_s if wait_s and wait_s > 0 else tool_wait_s()
    if await _wait(job.future, limit, ctx, job.tool):
        return _result_text(job.future)
    return running_json(job)


def job_summary(job_id: str) -> Tuple[str, str]:
    """(tool, state) of a job, for status tools."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return "", "unknown"
    return job.tool, "done" if job.future.done() else "running"
