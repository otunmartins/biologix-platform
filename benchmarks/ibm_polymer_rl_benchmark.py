#!/usr/bin/env python3
"""
Smoke check for IBM logical-agent polymer RL clone (non-BO; MCP-independent).

Runs `python scripts/main.py test -h` when the repo is present. See docs/THIRD_PARTY_BENCHMARKS.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))

from benchmarks._external_paths import IBM_POLYMER_RL_ROOT  # noqa: E402


def run_check() -> dict:
    root = IBM_POLYMER_RL_ROOT
    if not root.is_dir():
        return {
            "ok": False,
            "benchmark": "ibm_polymer_rl",
            "error": "clone_missing",
            "expected_path": str(root),
            "hint": "bash scripts/clone_external_benchmarks.sh",
        }
    main_py = root / "scripts" / "main.py"
    if not main_py.is_file():
        return {
            "ok": False,
            "benchmark": "ibm_polymer_rl",
            "error": "scripts/main.py missing",
            "path": str(root),
        }
    try:
        r = subprocess.run(
            [sys.executable, str(main_py), "test", "-h"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {
            "ok": False,
            "benchmark": "ibm_polymer_rl",
            "error": str(e),
            "path": str(root),
        }
    return {
        "ok": r.returncode == 0,
        "benchmark": "ibm_polymer_rl",
        "path": str(root),
        "cli_help_returncode": r.returncode,
        "paper": "IBM RL4RealLife ICML 2021 (logical action-aware features)",
    }


def main() -> None:
    out = run_check()
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 2)


if __name__ == "__main__":
    main()
