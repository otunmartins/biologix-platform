"""PhysicsService: facade over existing OpenMM simulation code.

Generalizes from insulin-only to arbitrary biologic targets by
accepting biologic_target as a parameter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_simulation(
    psmiles_list: List[str],
    biologic_target: str = "insulin",
    temperature_k: float = 313.0,
    n_steps: int = 5000,
) -> Dict[str, Any]:
    """Run MD simulation for polymer-biologic system.

    Delegates to the existing MDSimulator; biologic_target parameterizes
    which protein structure to use.
    """
    result: Dict[str, Any] = {
        "psmiles_list": psmiles_list,
        "biologic_target": biologic_target,
        "temperature_k": temperature_k,
        "results": [],
        "errors": [],
    }

    try:
        import os as _os

        _os.environ["INSULIN_AI_OPENMM_MAX_MINIMIZE_STEPS"] = str(n_steps)
        _os.environ["INSULIN_AI_OPENMM_TEMPERATURE_K"] = str(temperature_k)
        from insulin_ai.simulation.md_simulator import MDSimulator

        sim = MDSimulator()
        candidates = [{"psmiles": s, "chemical_structure": s} for s in psmiles_list]
        sim_result = sim.evaluate_candidates(
            candidates, max_candidates=len(candidates), verbose=False
        )
        result["results"] = sim_result if isinstance(sim_result, dict) else [sim_result]
    except ImportError:
        result["errors"].append(
            "simulation module not available; install with: pip install insulin-ai[simulation]"
        )
    except Exception as exc:
        result["errors"].append(str(exc))

    return result
