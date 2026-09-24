"""
Tell a tool that failed from a tool that was cut off by its container shutting down.

A deploy, a scale-down, or a crash stops the web container while a job thread is still
running. The thread then dies with an error from whatever it was doing at that instant:
Modal's ``ClientClosed`` while polling a worker, ``CancelledError``, or a refusal to start
new work at interpreter shutdown. None of that says anything about the job. Recording it as
the job's result, or as a failed attempt at the step, turned a healthy simulation into a
reported failure and made the agent stop. Callers that record outcomes skip these.
"""

from __future__ import annotations

from typing import Any

_MARKERS = (
    "ClientClosed",
    "CancelledError",
    "cannot schedule new futures after interpreter shutdown",
    "cannot schedule new futures after shutdown",
)


def is_shutdown_interruption(value: Any) -> bool:
    """True for an exception, or a tool result's text, produced by container shutdown."""
    if isinstance(value, BaseException):
        text = f"{type(value).__name__}: {value}"
    else:
        text = str(value or "")
    return any(marker in text for marker in _MARKERS)
