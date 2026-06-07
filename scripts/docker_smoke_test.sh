#!/usr/bin/env bash
# Lightweight in-container smoke test — run before publishing Docker images.
set -euo pipefail

source /opt/conda/etc/profile.d/conda.sh
conda activate biologix-ai-sim
cd /app

echo "=== Smoke: baked-in data ==="
if [[ ! -f /app/data/retrosynthesis/precursors.json ]]; then
  echo "ERROR: precursors.json missing from image" >&2
  exit 1
fi
if [[ ! -f /app/data/aizynthfinder/config.yml ]]; then
  echo "WARN: AiZynth config missing (SLIM image or first-run volume)"
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

echo "=== Smoke: OpenMM matrix (tiny settings) ==="
export BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S="${BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S:-180}"
export BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1
export BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS=2
export BIOLOGIX_AI_OPENMM_N_REPEATS=1
export BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS=50
export BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1
export BIOLOGIX_AI_OPENMM_MATRIX_NPT=0

python - <<'PY'
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

echo "=== Docker smoke test passed ==="
