"""Server-Sent Events helper for live pipeline progress streaming.

Tails the session's append-only pipeline_audit.jsonl and pushes each new
record as an SSE event. The frontend opens an EventSource and renders live
agent status cards as candidates move through validation, ADMET, retro,
compliance, and scoring stages.

Usage
-----
Mount the router in app.py:

    from insulin_ai.http_api.sse import router as sse_router
    app.include_router(sse_router)

Then connect from JavaScript:

    const source = new EventSource('/api/experiments/<id>/stream');
    source.onmessage = (e) => {
        const record = JSON.parse(e.data);
        // record: { audit_id, timestamp, candidate_psmiles, stage, disposition, detail }
    };
    source.addEventListener('done', () => source.close());
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["Streaming"])

_ROOT = Path(os.environ.get("INSULIN_AI_ROOT", Path(__file__).parents[6]))
_RUNS = _ROOT / "runs"

_POLL_INTERVAL = 0.5   # seconds between file-tail polls
_KEEPALIVE_INTERVAL = 15  # seconds between keepalive comments


def _audit_path(experiment_id: str) -> Path:
    return _RUNS / experiment_id / "audit" / "pipeline_audit.jsonl"


async def _tail_audit(
    experiment_id: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings by tailing the JSONL audit file.

    - Sends all existing records first (catch-up), then polls for new lines.
    - Sends a keepalive comment every _KEEPALIVE_INTERVAL seconds so proxies
      don't close the connection on idle campaigns.
    - Sends a 'done' event when the client disconnects.
    """
    audit_file = _audit_path(experiment_id)
    position = 0
    last_keepalive = asyncio.get_event_loop().time()

    while True:
        if await request.is_disconnected():
            yield "event: done\ndata: {}\n\n"
            return

        now = asyncio.get_event_loop().time()

        if audit_file.is_file():
            with audit_file.open("r", encoding="utf-8") as fh:
                fh.seek(position)
                new_lines = fh.readlines()
                position = fh.tell()

            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)  # validate JSON before sending
                    yield f"data: {line}\n\n"
                    last_keepalive = now
                except json.JSONDecodeError:
                    continue

        # Keepalive comment (ignored by EventSource but keeps connection alive)
        if now - last_keepalive >= _KEEPALIVE_INTERVAL:
            yield ": keepalive\n\n"
            last_keepalive = now

        await asyncio.sleep(_POLL_INTERVAL)


@router.get(
    "/api/experiments/{experiment_id}/stream",
    summary="Stream live pipeline audit events via Server-Sent Events",
    description=(
        "Opens a text/event-stream that tails the session's pipeline_audit.jsonl. "
        "Each event is a PipelineAuditRecord JSON: stage, disposition (pass/fail/warning), "
        "and detail. The frontend renders live agent status cards from these events. "
        "Connect with: new EventSource('/api/experiments/{id}/stream')."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Live stream of PipelineAuditRecord events.",
        }
    },
)
async def stream_pipeline(experiment_id: str, request: Request):
    return StreamingResponse(
        _tail_audit(experiment_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering for SSE
        },
    )
