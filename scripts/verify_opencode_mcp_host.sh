#!/usr/bin/env bash
# Verify OpenCode MCP host capabilities (version pin + optional long-tool smoke).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OPENCODE_BIN="${OPENCODE_BIN:-/root/.opencode/bin/opencode}"
VERSION_FILE="${REPO_ROOT}/.opencode-version"
MIN_VERSION="${OPENCODE_MIN_VERSION:-1.17.4}"
MODE="${1:-fast}"

if [[ ! -x "$OPENCODE_BIN" ]]; then
  OPENCODE_BIN="$(command -v opencode || true)"
fi

if [[ -z "$OPENCODE_BIN" || ! -x "$OPENCODE_BIN" ]]; then
  echo "ERROR: opencode binary not found" >&2
  exit 1
fi

installed="$("$OPENCODE_BIN" --version 2>/dev/null | head -1 | tr -d '[:space:]')"
echo "OpenCode installed: ${installed:-unknown}"

if [[ -f "$VERSION_FILE" ]]; then
  pinned="$(tr -d '[:space:]' < "$VERSION_FILE")"
  echo "Docker pinned version file: $pinned"
fi

python3 - <<PY
import re
import sys

installed = """${installed}"""
min_ver = """${MIN_VERSION}"""

def parse(v):
    m = re.search(r"(\d+\.\d+\.\d+)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0, 0, 0)

if parse(installed) < parse(min_ver):
    print(f"WARN: OpenCode {installed} is below documented minimum {min_ver}", file=sys.stderr)
    sys.exit(2)
print(f"OpenCode version OK (>= {min_ver})")
PY

if [[ "$MODE" == "fast" ]]; then
  echo "verify_opencode_mcp_host: fast checks passed"
  exit 0
fi

echo "=== Full mode: MCP sleep server import ==="
python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("scripts/mcp_sleep_server.py")
spec = importlib.util.spec_from_file_location("mcp_sleep_server", path)
mod = importlib.util.module_from_spec(spec)
from mcp.server.fastmcp import FastMCP

_orig = FastMCP.run
FastMCP.run = lambda self, *a, **kw: None
spec.loader.exec_module(mod)
FastMCP.run = _orig
assert hasattr(mod, "sleep_tool")
print("mcp_sleep_server import OK")
PY

echo "verify_opencode_mcp_host: full checks passed (interactive >130s MCP call still manual)"
