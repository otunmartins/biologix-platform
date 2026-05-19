#!/usr/bin/env python3
"""
CLI entry for autoresearch-style autonomous discovery (subprocess / overnight).

Usage:
  INSULIN_AI_ROOT=/path/to/insulin-ai python scripts/run_autonomous_discovery.py --budget-minutes 480

Or from repo root:
  python scripts/run_autonomous_discovery.py --budget-minutes 60
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("INSULIN_AI_ROOT", ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src", "python"))

if __name__ == "__main__":
    from insulin_ai.autonomous_discovery import main

    main()  # uses --session-dir (required for single-folder output)
