"""Run one OpenMM matrix evaluation in its own interpreter (GPU platforms).

Usage: ``python -m biologix_ai.simulation.matrix_subprocess REQUEST.json RESPONSE.json``,
where the request is ``{"psmiles": str, "kwargs": {...}}`` for
:func:`openmm_complex.run_openmm_matrix_relax_and_energy`. See
``md_simulator._run_matrix_in_subprocess`` for why this is not a forked worker.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def main(request_path: str, response_path: str) -> int:
    request = json.loads(Path(request_path).read_text())
    kwargs = dict(request.get("kwargs") or {})
    if kwargs.get("protein_chains") is not None:
        kwargs["protein_chains"] = tuple(kwargs["protein_chains"])
    try:
        from biologix_ai.simulation.openmm_complex import run_openmm_matrix_relax_and_energy

        result = run_openmm_matrix_relax_and_energy(request["psmiles"], **kwargs)
        if result is None:
            result = {"ok": False, "error": "unknown failure", "stage": "openmm"}
    except Exception as exc:
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "stage": "openmm",
            "traceback": traceback.format_exc()[-2000:],
        }
    Path(response_path).write_text(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
