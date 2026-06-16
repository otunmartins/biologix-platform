#!/usr/bin/env python3
"""
Plan retrosynthetic routes for a polymer target (CLI fallback for MCP latch sessions).

Wraps plan_retrosynthesis() with a hard wall-clock cap and stderr heartbeats so
OpenCode/bash sessions do not appear hung during tree construction on Rosetta Docker.

Usage:
  cd /app && python3 scripts/run_plan_retrosynthesis.py Chitosan \
    --biologic-target insulin --run-dir runs/SESSION --max-routes 3 2>&1

Env:
  BIOLOGIX_PLAN_TIMEOUT_S  Total wall clock (default 420)
  BIOLOGIX_TREE_TIMEOUT    Tree subprocess cap (default 300 in Docker entrypoint)
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "python"))

_PLAN_TIMEOUT_S = int(os.environ.get("BIOLOGIX_PLAN_TIMEOUT_S", "420"))


def _heartbeat(stop: threading.Event, label: str) -> None:
    while not stop.wait(30):
        print(f"[retro] {label} still running…", file=sys.stderr, flush=True)


def _plan_worker(request_dict: Dict[str, Any], result_queue: "multiprocessing.Queue[Any]") -> None:
    try:
        os.setsid()
    except OSError:
        pass
    try:
        from biologix_ai.retrosynthesis.models import RetrosynthesisRequest
        from biologix_ai.services.retrosynthesis_service import plan_retrosynthesis as _plan

        request = RetrosynthesisRequest.model_validate(request_dict)
        result = _plan(request)
        result_queue.put({"ok": True, "result": result.model_dump()})
    except Exception as exc:
        result_queue.put({"ok": False, "error": str(exc)})


def _kill_process_tree(proc: multiprocessing.Process) -> None:
    if not proc.is_alive():
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    proc.join(5)
    if proc.is_alive():
        proc.kill()
        proc.join(2)


def _run_with_timeout(
    request_dict: Dict[str, Any],
    timeout_s: int,
) -> Dict[str, Any]:
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue[Any] = ctx.Queue()
    proc = ctx.Process(target=_plan_worker, args=(request_dict, result_queue))
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(stop, "plan_retrosynthesis"),
        daemon=True,
    )
    heartbeat.start()
    try:
        proc.start()
        proc.join(timeout=timeout_s)
        if proc.is_alive():
            _kill_process_tree(proc)
            return {
                "ok": False,
                "error": f"plan_retrosynthesis timed out after {timeout_s}s",
                "hint": (
                    "Increase BIOLOGIX_PLAN_TIMEOUT_S or BIOLOGIX_TREE_TIMEOUT; "
                    "check runs/<session>/tool_events.jsonl for stage progress."
                ),
            }
        if not result_queue.empty():
            payload = result_queue.get_nowait()
            if isinstance(payload, dict):
                return payload
        return {"ok": False, "error": "plan_retrosynthesis worker exited without result"}
    finally:
        stop.set()
        heartbeat.join(timeout=1)


def _persist_plan(
    session: Path,
    target: str,
    biologic_target: str,
    result_dict: Dict[str, Any],
) -> Optional[str]:
    import time as _time

    from biologix_ai.services.biologics_session import (
        patch_world_retrosynthesis,
        write_retrosynthesis_artifact,
    )

    stem = f"plan_{int(_time.time())}"
    path = write_retrosynthesis_artifact(
        session,
        f"{stem}.json",
        {"target": target, "biologic_target": biologic_target, "result": result_dict},
    )
    try:
        rel_art = os.path.relpath(str(path), str(session))
    except ValueError:
        rel_art = str(path)
    patch_world_retrosynthesis(
        session,
        {
            "id": stem,
            "polymer_target": target,
            "biologic_target": biologic_target,
            "n_routes": len(result_dict.get("polymer_routes", [])),
            "artifact": rel_art,
        },
    )
    return str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="Polymer name, PSMILES, or SMILES")
    ap.add_argument("--biologic-target", default="insulin")
    ap.add_argument("--run-dir", metavar="PATH", help="Session directory (runs/SESSION)")
    ap.add_argument("--max-routes", type=int, default=5)
    ap.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help=f"Wall-clock cap (default: BIOLOGIX_PLAN_TIMEOUT_S={_PLAN_TIMEOUT_S})",
    )
    args = ap.parse_args()

    from biologix_ai.retrosynthesis.models import RetrosynthesisConstraints, RetrosynthesisRequest
    from biologix_ai.services.retrosynthesis_service import _is_retrosynthesisagent_available

    if not _is_retrosynthesisagent_available():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "RetroSynthesisAgent not installed. Run ./install (includes git submodules).",
                },
                indent=2,
            )
        )
        sys.exit(1)

    session: Optional[Path] = None
    if args.run_dir:
        session = Path(args.run_dir).expanduser().resolve()

    request = RetrosynthesisRequest(
        target=args.target,
        biologic_target=args.biologic_target,
        session_dir=str(session) if session is not None else None,
        constraints=RetrosynthesisConstraints(max_routes=args.max_routes),
    )
    timeout_s = args.timeout_s if args.timeout_s is not None else _PLAN_TIMEOUT_S

    print(
        f"[retro] plan_retrosynthesis target={args.target!r} timeout={timeout_s}s",
        file=sys.stderr,
        flush=True,
    )
    payload = _run_with_timeout(request.model_dump(), timeout_s)

    if payload.get("ok") and session is not None and "result" in payload:
        try:
            art = _persist_plan(
                session,
                args.target,
                args.biologic_target,
                payload["result"],
            )
            if art:
                payload["session_artifact"] = art
        except Exception as exc:
            payload["session_persist_warning"] = str(exc)

    if payload.get("ok") and "result" in payload:
        print(json.dumps(payload["result"], indent=2, default=str))
        return

    print(json.dumps(payload, indent=2, default=str))
    sys.exit(1)


if __name__ == "__main__":
    main()
