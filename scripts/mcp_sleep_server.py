#!/usr/bin/env python3
"""Minimal MCP server for OpenCode host timeout verification."""

from __future__ import annotations

import json
import time

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("sleep-verify")


@mcp.tool()
def sleep_tool(ctx: Context, seconds: float = 140.0) -> str:
    """Sleep up to *seconds*, emitting MCP progress every 15s."""
    total = max(1.0, float(seconds))
    elapsed = 0.0
    while elapsed < total:
        step = min(15.0, total - elapsed)
        time.sleep(step)
        elapsed += step
        try:
            ctx.report_progress(elapsed, total, f"slept {elapsed:.0f}/{total:.0f}s")
        except Exception:
            pass
    return json.dumps({"ok": True, "slept_seconds": elapsed})


if __name__ == "__main__":
    mcp.run()
