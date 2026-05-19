#!/usr/bin/env python3
"""Scripted biologics retrosynthesis loop (retro + ADMET + compile; optional OpenMM)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from insulin_ai.run_paths import ENV_SESSION


def main() -> int:
    ap = argparse.ArgumentParser(description="Biologics discovery loop")
    ap.add_argument("--biologic-target", required=True)
    ap.add_argument("--polymer-target", default="")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--budget-minutes", type=float, default=60.0)
    ap.add_argument("--max-routes", type=int, default=5)
    ap.add_argument("--no-admet", action="store_true")
    ap.add_argument("--openmm", action="store_true")
    ap.add_argument("--root", default="")
    args = ap.parse_args()

    root = args.root or os.environ.get("INSULIN_AI_ROOT", os.getcwd())
    session_dir = Path(args.session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)

    from insulin_ai.autonomous_biologics import run_biologics_discovery_loop

    summary = run_biologics_discovery_loop(
        biologic_target=args.biologic_target,
        polymer_target=args.polymer_target,
        session_dir=session_dir,
        root=root,
        budget_minutes=args.budget_minutes,
        max_routes=args.max_routes,
        run_admet=not args.no_admet,
        run_openmm=args.openmm,
      )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
