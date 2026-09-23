"""Tests for running MCP tools off the event loop and returning long calls as jobs."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai import mcp_jobs  # noqa: E402
from biologix_ai.mcp_jobs import AWAIT_TOOL, JOB_RUNNING, await_job, install_job_runner  # noqa: E402


def _server(release: threading.Event):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("jobs-test")

    @mcp.tool()
    def slow(value: str = "x") -> str:
        release.wait(timeout=10)
        return json.dumps({"ok": True, "value": value, "thread": threading.current_thread().name})

    @mcp.tool()
    def quick() -> str:
        return json.dumps({"ok": True, "quick": True})

    install_job_runner(mcp)
    return mcp


def test_tools_run_in_worker_threads_and_finish_inline_when_fast(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "5")
    release = threading.Event()
    release.set()
    mcp = _server(release)
    tool = mcp._tool_manager._tools["slow"]
    assert tool.is_async is True
    result = json.loads(asyncio.run(tool.fn(value="a")))
    assert result["value"] == "a"
    assert result["thread"].startswith("biologix-tool")


def test_slow_call_becomes_a_job_that_await_returns(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "0.2")
    release = threading.Event()
    mcp = _server(release)

    async def scenario():
        running = json.loads(await mcp._tool_manager._tools["slow"].fn(value="b"))
        assert running["status"] == "running"
        assert running["protocol"]["next_required_tool"] == AWAIT_TOOL
        job_id = running["job_id"]
        refused = json.loads(await mcp._tool_manager._tools["quick"].fn())
        assert refused["error"] == JOB_RUNNING
        still = json.loads(await await_job(job_id, 0.2))
        assert still["status"] == "running"
        release.set()
        done = json.loads(await await_job(job_id, 5))
        assert done["value"] == "b"
        after = json.loads(await mcp._tool_manager._tools["quick"].fn())
        assert after["quick"] is True

    asyncio.run(scenario())


def test_unknown_job_asks_for_a_rerun() -> None:
    missing = json.loads(asyncio.run(await_job("nope")))
    assert missing["error"] == "JOB_NOT_FOUND" and missing["abort"] is True


def test_default_wait_is_unlimited_on_stdio_and_bounded_over_http(monkeypatch) -> None:
    monkeypatch.delenv("BIOLOGIX_TOOL_WAIT_S", raising=False)
    monkeypatch.setenv("BIOLOGIX_MCP_TRANSPORT", "stdio")
    assert mcp_jobs.tool_wait_s() == 0.0
    monkeypatch.setenv("BIOLOGIX_MCP_TRANSPORT", "http")
    assert mcp_jobs.tool_wait_s() == 90.0


def test_progress_from_tool_threads_reaches_the_event_loop() -> None:
    from biologix_ai.mcp_tool_guard import McpProgressReporter

    sent = []

    class FakeContext:
        async def report_progress(self, progress, total, message):
            sent.append((progress, message))

    async def scenario():
        mcp_jobs.EVENT_LOOP.set(asyncio.get_running_loop())
        import contextvars

        reporter = McpProgressReporter(FakeContext(), tool="t", interval_s=0)
        ctx = contextvars.copy_context()
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: ctx.run(reporter.heartbeat, "packing", progress=1.0)
        )
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert sent == [(1.0, "packing")]


def test_full_stack_await_is_not_blocked_by_the_jobs_own_lock(monkeypatch) -> None:
    """Gate-free stack as installed on the server: lock, then job runner."""
    from mcp.server.fastmcp import FastMCP

    from biologix_ai.mcp_stdio_guard import install_stdio_guards

    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "0.2")
    release = threading.Event()
    mcp = FastMCP("jobs-stack")

    @mcp.tool()
    def slow() -> str:
        release.wait(timeout=10)
        return json.dumps({"ok": True, "slow": True})

    @mcp.tool()
    def biologix_runtime_status() -> str:
        return json.dumps({"ok": True, "status": True})

    @mcp.tool()
    async def await_biologix_job(job_id: str, wait_s: int = 0) -> str:
        return await await_job(job_id, float(wait_s or 0))

    install_stdio_guards(mcp)
    install_job_runner(mcp)
    tools = mcp._tool_manager._tools

    async def scenario():
        running = json.loads(await tools["slow"].fn())
        status = json.loads(await tools["biologix_runtime_status"].fn())
        assert status["status"] is True
        pending = json.loads(await tools["await_biologix_job"].fn(job_id=running["job_id"], wait_s=0))
        assert pending["status"] == "running"
        release.set()
        done = json.loads(await tools["await_biologix_job"].fn(job_id=running["job_id"], wait_s=5))
        assert done["slow"] is True

    asyncio.run(scenario())


def test_a_dropped_connection_leaves_the_job_recoverable(monkeypatch) -> None:
    """A client-side 504 must not lose the run: retrying the tool returns its job_id."""
    from mcp.server.fastmcp import FastMCP

    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "0.2")
    release = threading.Event()
    mcp = FastMCP("jobs-504")
    started = {"n": 0}

    @mcp.tool()
    def openmm_evaluate_psmiles(psmiles_list: str = "") -> str:
        started["n"] += 1
        release.wait(timeout=10)
        return json.dumps({"ok": True, "psmiles_list": psmiles_list})

    install_job_runner(mcp)
    tool = mcp._tool_manager._tools["openmm_evaluate_psmiles"]

    async def scenario():
        first = json.loads(await tool.fn(psmiles_list="[*]CC[*]"))
        assert first["status"] == "running"
        # The client never saw that payload: its proxy returned 504 instead.
        # The model retries the identical call, as the protocol now instructs.
        retry = json.loads(await tool.fn(psmiles_list="[*]CC[*]"))
        assert retry["error"] == JOB_RUNNING
        assert retry["job_id"] == first["job_id"]
        assert retry["protocol"]["next_required_tool"] == AWAIT_TOOL
        assert started["n"] == 1  # the work was not started a second time
        release.set()
        done = json.loads(await await_job(first["job_id"], 5))
        assert done["psmiles_list"] == "[*]CC[*]"

    asyncio.run(scenario())


def test_check_back_repeatedly_and_see_what_it_is_doing(monkeypatch) -> None:
    """Submit, wait, check back, keep waiting: the loop a minutes-long run needs."""
    from mcp.server.fastmcp import FastMCP

    from biologix_ai.mcp_jobs import note_progress

    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "0.2")
    release = threading.Event()
    mcp = FastMCP("jobs-checkback")

    @mcp.tool()
    def openmm_evaluate_psmiles(psmiles_list: str = "") -> str:
        note_progress("packing 8 polymer chains", stage="packmol")
        release.wait(timeout=10)
        note_progress("computing interaction energy", stage="energy_eval")
        return json.dumps({"ok": True, "interaction_energy_kj_mol": -376.7})

    install_job_runner(mcp)
    tool = mcp._tool_manager._tools["openmm_evaluate_psmiles"]

    async def scenario():
        first = json.loads(await tool.fn(psmiles_list="[*]CC[*]"))
        assert first["status"] == "running"
        assert first["progress"] == "packing 8 polymer chains"
        assert first["stage"] == "packmol"
        job_id = first["job_id"]

        for _ in range(3):  # check back repeatedly; still running each time
            again = json.loads(await await_job(job_id, 0.2))
            assert again["status"] == "running"
            assert again["job_id"] == job_id
            assert "as many times as it takes" in again["protocol"]["rule"]
        assert again["elapsed_s"] >= first["elapsed_s"]

        release.set()
        done = json.loads(await await_job(job_id, 5))
        assert done["interaction_energy_kj_mol"] == -376.7

    asyncio.run(scenario())


def test_a_job_is_recoverable_even_before_the_wait_window_elapses(monkeypatch) -> None:
    """A client with a very short ceiling still leaves a job to check back on."""
    from mcp.server.fastmcp import FastMCP

    from biologix_ai.mcp_jobs import pending_job

    monkeypatch.setenv("BIOLOGIX_TOOL_WAIT_S", "30")  # far longer than this client waits
    release = threading.Event()
    started = threading.Event()
    mcp = FastMCP("jobs-early-drop")

    @mcp.tool()
    def slow() -> str:
        started.set()
        release.wait(timeout=10)
        return json.dumps({"ok": True})

    install_job_runner(mcp)
    tool = mcp._tool_manager._tools["slow"]

    async def scenario():
        task = asyncio.ensure_future(tool.fn())
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        await asyncio.sleep(0.05)
        # The client gave up here, long before the 30 s window would hand off.
        job = pending_job(mcp_client.client_key())
        assert job is not None and job.tool == "slow"
        release.set()
        assert json.loads(await task)["ok"] is True

    from biologix_ai import mcp_client

    asyncio.run(scenario())
