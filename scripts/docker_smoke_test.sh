#!/usr/bin/env bash
# Lightweight in-container smoke test — run before publishing Docker images.
# No LLM/API keys required. Invoked with --entrypoint bash (not via OpenCode).
set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
conda activate biologix-ai-sim
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
if [[ -d /opt/conda/envs/pymol-viz/lib ]]; then
  export LD_LIBRARY_PATH="/opt/conda/envs/pymol-viz/lib:${LD_LIBRARY_PATH}"
  export PATH="/opt/conda/envs/pymol-viz/bin:${PATH}"
fi
cd /app
export MPLBACKEND=Agg

echo "=== Smoke: conda libstdc++ / libLerc loader ==="
python - <<'PY'
import ctypes
import os
from pathlib import Path

prefix = Path(os.environ["CONDA_PREFIX"])
liblerc = prefix / "lib" / "libLerc.so.4"
assert liblerc.is_file(), f"missing {liblerc}"
ctypes.CDLL(str(liblerc))
ld = os.environ.get("LD_LIBRARY_PATH", "")
assert str(prefix / "lib") in ld, f"LD_LIBRARY_PATH missing conda lib: {ld!r}"
print("libLerc load OK")
PY

echo "=== Smoke: MCP Python package ==="
python -c "import mcp; print('mcp', mcp.__version__)"

echo "=== Smoke: MCP server import ==="
python - <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "biologix_ai_mcp_server",
    Path("/app/biologix_ai_mcp_server.py"),
)
mod = importlib.util.module_from_spec(spec)
sys.modules["biologix_ai_mcp_server"] = mod
try:
    from mcp.server.fastmcp import FastMCP
    _orig = FastMCP.run
    FastMCP.run = lambda self, *a, **kw: None
    spec.loader.exec_module(mod)
    FastMCP.run = _orig
except Exception:
    spec.loader.exec_module(mod)

for name in ("get_retrosynthesis_templates", "get_personas", "plan_retrosynthesis"):
    assert hasattr(mod, name), f"missing MCP tool: {name}"
print("MCP server import OK")
PY

echo "=== Smoke: Docker entrypoint terminal restore ==="
if [[ ! -x /app/docker/restore_terminal.sh ]]; then
  echo "ERROR: restore_terminal.sh missing or not executable" >&2
  exit 1
fi
/app/docker/restore_terminal.sh
grep -q 'restore_host_terminal' /app/docker/entrypoint.sh
grep -q 'BIOLOGIX_AI_OPENMM_AUTO' /app/docker/entrypoint.sh
echo "entrypoint terminal restore OK"

echo "=== Smoke: baked-in data ==="
if [[ ! -f /app/data/retrosynthesis/precursors.json ]]; then
  echo "ERROR: precursors.json missing from image" >&2
  exit 1
fi
if [[ ! -f /app/data/aizynthfinder/config.yml ]]; then
  echo "WARN: AiZynth config missing (SLIM image or first-run volume)"
fi

echo "=== Smoke: PyMOL (pymol-viz env) ==="
if command -v pymol >/dev/null 2>&1; then
  PYMOL_HEADLESS=1 pymol -c -d "quit"
  echo "PyMOL OK"
else
  echo "ERROR: pymol not on PATH (expected /opt/conda/envs/pymol-viz/bin)" >&2
  exit 1
fi

echo "=== Smoke: psmiles PNG (not SVG) ==="
python - <<'PY'
from pathlib import Path
from biologix_ai.psmiles_drawing import save_psmiles_png

out = Path("/tmp/smoke_psmiles.png")
r = save_psmiles_png("[*]OCC[*]", out)
assert r.get("ok"), r
head = out.read_bytes()[:64].lstrip()
assert not (head.startswith(b"<?xml") or head.startswith(b"<svg")), "SVG written as PNG"
print("psmiles PNG OK:", out)
PY

echo "=== Smoke: PDF compile with Markdown tables ==="
python - <<'PY'
from pathlib import Path
from biologix_ai.discovery_report import compile_markdown_to_pdf

sess = Path("/tmp/smoke_pdf_session")
sess.mkdir(exist_ok=True)
(sess / "SUMMARY_REPORT.md").write_text(
    "# Smoke\n\n| Polymer | Route |\n|---|---|\n| PLA | ROP |\n",
    encoding="utf-8",
)
r = compile_markdown_to_pdf(sess)
assert r.get("ok"), r
assert (sess / "SUMMARY_REPORT.pdf").is_file()
print("PDF OK:", r.get("pdf_render_mode"), r.get("warnings"))
PY

echo "=== Smoke: MCP stdio guard + tool guard ==="
PYTHONPATH=src/python python -m pytest tests/test_mcp_stdio_guard.py tests/test_mcp_tool_guard.py -q

echo "=== Smoke: OpenCode version file (when present) ==="
if [[ -f /app/.opencode-version ]]; then
  bash /app/scripts/verify_opencode_mcp_host.sh fast || echo "WARN: OpenCode version below documented minimum"
else
  echo "SKIP: .opencode-version not baked (dev/local image)"
fi

echo "=== Smoke: OpenMM matrix (tiny settings, hard cap 5 min) ==="
export BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S="${BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S:-120}"
export BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1
export BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS=2
export BIOLOGIX_AI_OPENMM_N_REPEATS=1
export BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS=50
export BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1
export BIOLOGIX_AI_OPENMM_MATRIX_NPT=0

if command -v timeout >/dev/null 2>&1; then
  timeout 300 python - <<'PY'
import json
from biologix_ai.simulation.openmm_compat import openmm_available

if not openmm_available():
    print("SKIP: OpenMM stack not importable in this image")
    raise SystemExit(0)

from biologix_ai.simulation import MDSimulator

sim = MDSimulator(n_steps=100, random_seed=1)
result = sim.evaluate_candidates(
    [{"material_name": "smoke", "chemical_structure": "[*]CC[*]"}],
    max_candidates=1,
    verbose=True,
)
progress = result.get("evaluation_progress") or []
print("OpenMM smoke:", json.dumps(progress[0] if progress else {"status": "no_progress"}))
status = progress[0].get("status") if progress else None
if status not in ("completed", "failed", "rejected", "skipped"):
    raise SystemExit(f"unexpected OpenMM smoke status: {status}")
PY
else
  echo "WARN: timeout(1) unavailable; skipping OpenMM smoke"
fi

echo "=== Docker smoke test passed ==="
