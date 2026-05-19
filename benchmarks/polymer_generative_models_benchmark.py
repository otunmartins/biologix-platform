#!/usr/bin/env python3
"""
Smoke check for Wisconsin polymer generative benchmark clone (non-BO; MCP-independent).

See docs/THIRD_PARTY_BENCHMARKS.md and extern/benchmarks/polymer-generative-models/README.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src" / "python") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src" / "python"))

from benchmarks._external_paths import POLYMER_GENERATIVE_MODELS_ROOT  # noqa: E402


def run_check() -> dict:
    root = POLYMER_GENERATIVE_MODELS_ROOT
    if not root.is_dir():
        return {
            "ok": False,
            "benchmark": "polymer_generative_models",
            "error": "clone_missing",
            "expected_path": str(root),
            "hint": "bash scripts/clone_external_benchmarks.sh",
        }
    readme = root / "README.md"
    moses = root / "MOSES"
    return {
        "ok": True,
        "benchmark": "polymer_generative_models",
        "path": str(root),
        "readme_present": readme.is_file(),
        "moses_dir_present": moses.is_dir(),
        "paper": "10.1039/D4DD00395K",
    }


def main() -> None:
    out = run_check()
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 2)


if __name__ == "__main__":
    main()
