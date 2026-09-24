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
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from biologix_ai.interruption import is_shutdown_interruption
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
    # Bracketed by measurement against ChatGPT's MCP proxy on this server: a
    # 144.5 s call completed and its result reached the model, while a 240 s wait
    # was cut off with HTTP 504 {"detail":"MCP request timed out"}. Modal holds
    # the request for the function timeout (3600 s), so the ceiling is the
    # client's and lies between those two. Wait 90 s, which leaves room for a
    # cold start on top and still hands off well before the ceiling. A client
    # that tolerates more can raise BIOLOGIX_TOOL_WAIT_S.
    return 90.0 if transport in ("http", "streamable-http") else 0.0


@dataclass
class Job:
    job_id: str
    client: str
    tool: str
    future: Optional[Future] = None
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    progress: str = ""
    stage: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    # The Modal call this job spawned, and the call to re-attach to when the server
    # restarted while it ran (see ``resume_job``).
    worker_call_id: str = ""
    worker_key: str = ""
    resume_worker: Optional[Tuple[str, str]] = None

    def done(self) -> bool:
        return self.future is not None and self.future.done()


# The job the current tool thread belongs to, so its progress reports can be
# read back by whoever checks on it.
CURRENT_JOB: contextvars.ContextVar[Optional[Job]] = contextvars.ContextVar(
    "biologix_current_job", default=None
)


def note_progress(message: str, stage: str = "") -> None:
    """Record the latest progress line for the running job (safe to call anywhere)."""
    job = CURRENT_JOB.get()
    if job is None:
        return
    if message:
        job.progress = str(message)[:300]
    if stage:
        job.stage = str(stage)[:80]


_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.environ.get("BIOLOGIX_MCP_JOB_THREADS", "16") or 16),
    thread_name_prefix="biologix-tool",
)
_JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


# --- Durable job records -----------------------------------------------------------
#
# The job table above lives in one container's memory, and a deploy, crash, or
# scale-down replaces that container while a simulation is still running. A ChatGPT
# run was lost that way: the check-back got JOB_NOT_FOUND and the agent stopped.
# Each job therefore also leaves a small record on the runs volume: what was asked,
# which Modal call it spawned, and the result once it finished. A fresh container
# can then hand back a finished result, re-attach to the still-running worker, or
# re-run the call, instead of losing the job.

_MAX_STORED_RESULT = 8_000_000
_TOOL_FNS: Dict[str, Callable[..., Any]] = {}


def jobs_dir() -> Optional[Path]:
    """Where job records live, or None when jobs need no durability (local stdio)."""
    explicit = os.environ.get("BIOLOGIX_JOBS_DIR", "").strip()
    if explicit:
        return Path(explicit)
    transport = os.environ.get("BIOLOGIX_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in ("http", "streamable-http"):
        return None
    from biologix_ai.run_paths import repo_root_from_package

    return repo_root_from_package() / "runs" / ".jobs"


def _commit_volume() -> None:
    """Make the record visible to the next container (best effort, off the caller's thread)."""
    if not os.environ.get("MODAL_TASK_ID"):
        return

    def _commit() -> None:
        try:
            import modal

            modal.Volume.from_name("biologix-mcp-runs").commit()
        except Exception:
            pass

    threading.Thread(target=_commit, daemon=True).start()


def _json_safe(arguments: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in arguments.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue  # a Context or other live object; the tool re-creates it
        safe[key] = value
    return safe


def _record_path(job_id: str) -> Optional[Path]:
    root = jobs_dir()
    if root is None or not job_id.isalnum():
        return None
    return root / f"{job_id}.json"


def _persist(job: Job, status: str = "running", result: Optional[str] = None) -> None:
    path = _record_path(job.job_id)
    if path is None:
        return
    record: Dict[str, Any] = {
        "job_id": job.job_id,
        "client": job.client,
        "tool": job.tool,
        "arguments": job.arguments,
        "started": job.started,
        "status": status,
        "worker_call_id": job.worker_call_id,
        "worker_key": job.worker_key,
    }
    if result is not None and len(result) <= _MAX_STORED_RESULT:
        record["result"] = result
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)
        _commit_volume()
    except OSError:
        pass


def _load_record(job_id: str) -> Optional[Dict[str, Any]]:
    path = _record_path(job_id)
    if path is None or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def note_worker_call(call_id: str, key: str) -> None:
    """Record the Modal call the current job spawned, so a restarted server can re-attach."""
    job = CURRENT_JOB.get()
    if job is None:
        return
    job.worker_call_id, job.worker_key = str(call_id), str(key)
    _persist(job)


def resumable_worker() -> Optional[Tuple[str, str]]:
    """``(call_id, progress_key)`` of a worker the current job should re-attach to, if any."""
    job = CURRENT_JOB.get()
    return job.resume_worker if job is not None else None


def _prune() -> None:
    now = time.time()
    with _JOBS_LOCK:
        for job_id in [j for j, job in _JOBS.items() if job.finished and now - job.finished > _FINISHED_TTL_S]:
            _JOBS.pop(job_id, None)


def pending_job(client: str) -> Optional[Job]:
    """The unfinished job of *client*, if any."""
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.client == client and not job.done():
                return job
    return None


def _register(
    client: str, tool: str, arguments: Optional[Dict[str, Any]] = None, job_id: str = ""
) -> Job:
    """Record a job before its work starts; the future is attached next."""
    job = Job(
        job_id=job_id or uuid.uuid4().hex[:16],
        client=client,
        tool=tool,
        arguments=_json_safe(arguments or {}),
    )
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
    _persist(job)
    _prune()
    return job


def _attach_future(job: Job, future: Future) -> None:
    def _mark(_f: Future) -> None:
        job.finished = time.time()
        text = _result_text(_f)
        if is_shutdown_interruption(text):
            # This container is going away mid-job. Leave the record "running" so the next
            # container resumes the job; a stored "done" would replay this error forever.
            return
        _persist(job, status="done", result=text)

    job.future = future
    future.add_done_callback(_mark)


def running_json(job: Job) -> str:
    """Result returned while *job* is still running."""
    elapsed = round(time.time() - job.started, 1)
    payload: Dict[str, Any] = {
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
                f"{job.tool} is still running on the server ({elapsed} s so far"
                + (f", {job.progress}" if job.progress else "")
                + f"). Nothing has failed. Call {AWAIT_TOOL} again with this job_id, as many "
                "times as it takes. Do not stop, summarize, estimate the result, restart the "
                "session, or call another Biologix tool first."
            ),
        },
    }
    if job.progress:
        payload["progress"] = job.progress
    if job.stage:
        payload["stage"] = job.stage
    return json.dumps(payload, indent=2)


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
    signature = inspect.signature(fn)
    _TOOL_FNS[name] = fn

    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> str:
        client = client_key()
        if name not in PASS_THROUGH_TOOLS:
            job = pending_job(client)
            if job is not None:
                return job_running_refusal(job, name)
        call = functools.partial(fn, *args, **kwargs)
        EVENT_LOOP.set(asyncio.get_running_loop())
        # Register before the work starts: if the connection drops at any point,
        # even before the wait window elapses, the run is still there to check on.
        try:
            asked = dict(signature.bind_partial(*args, **kwargs).arguments)
        except TypeError:
            asked = dict(kwargs)
        job = _register(client, name, asked)
        CURRENT_JOB.set(job)
        future = _EXECUTOR.submit(contextvars.copy_context().run, call)
        _attach_future(job, future)
        ctx = _current_context(server)
        if await _wait(future, tool_wait_s(), ctx, name):
            return _result_text(future)
        return running_json(job)

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


def _not_found(job_id: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "JOB_NOT_FOUND",
            "job_id": job_id,
            "not_a_failure": True,
            "hint": (
                "The server has no record of this job id. Nothing was lost: call the tool that "
                "started it again with the same arguments."
            ),
        },
        indent=2,
    )


def resume_job(record: Dict[str, Any], ctx: Any = None) -> Optional[Job]:
    """Bring a job back after a restart: re-run its tool, re-attaching to a live worker.

    The Modal worker of a simulation keeps running when the web container that spawned
    it is replaced, so the new container re-attaches to that call (``resume_worker``)
    instead of simulating again. Every other tool simply runs again with its saved
    arguments.
    """
    fn = _TOOL_FNS.get(str(record.get("tool", "")))
    if fn is None:
        return None
    job_id = str(record["job_id"])
    with _JOBS_LOCK:
        existing = _JOBS.get(job_id)
        if existing is not None:  # another check-back already resumed it
            return existing
        job = Job(
            job_id=job_id,
            client=str(record.get("client", "")),
            tool=str(record["tool"]),
            started=float(record.get("started") or time.time()),
            arguments=dict(record.get("arguments") or {}),
        )
        if record.get("worker_call_id"):
            job.resume_worker = (str(record["worker_call_id"]), str(record.get("worker_key", "")))
        _JOBS[job_id] = job
    _persist(job)
    kwargs = dict(job.arguments)
    # The MCP context belongs to the request that started the job and is not saved. Tools
    # accept None: progress then goes to the job's own record instead of a live stream.
    if "ctx" in inspect.signature(fn).parameters:
        kwargs.setdefault("ctx", None)
    call = functools.partial(fn, **kwargs)
    EVENT_LOOP.set(asyncio.get_running_loop())
    CURRENT_JOB.set(job)
    _attach_future(job, _EXECUTOR.submit(contextvars.copy_context().run, call))
    return job


async def await_job(job_id: str, wait_s: float = 0.0, ctx: Any = None) -> str:
    """Wait for a job of the calling client; return its result or a new ``running`` payload."""
    job_id = (job_id or "").strip()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        record = _load_record(job_id)
        if record is None or record.get("client") != client_key():
            return _not_found(job_id)
        if record.get("status") == "done" and isinstance(record.get("result"), str):
            return record["result"]  # finished before the restart
        job = resume_job(record, ctx)
        if job is None:
            return _not_found(job_id)
        payload = json.loads(running_json(job))
        payload["resumed_after_restart"] = True
        payload["protocol"]["rule"] = (
            "The server restarted while this ran; it has been resumed"
            + (" and re-attached to the running simulation" if job.resume_worker else "")
            + ". " + payload["protocol"]["rule"]
        )
        return json.dumps(payload, indent=2)
    if job.client != client_key():
        return _not_found(job_id)
    if job.future is None:  # registered but not yet started
        return running_json(job)
    default = tool_wait_s()
    limit = wait_s if wait_s and wait_s > 0 else default
    if default > 0:
        limit = min(limit, default)  # a client-requested wait may not outlast its own timeout
    if await _wait(job.future, limit, ctx, job.tool):
        return _result_text(job.future)
    return running_json(job)


def job_summary(job_id: str) -> Tuple[str, str]:
    """(tool, state) of a job, for status tools."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return "", "unknown"
    return job.tool, "done" if job.done() else "running"
