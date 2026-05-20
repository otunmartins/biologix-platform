"""DesignerService: facade over existing PSMILES generation and mutation code."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def generate_candidates(
    prompt: str,
    biologic_target: str = "insulin",
    library_size: int = 10,
) -> Dict[str, Any]:
    """Generate polymer candidates using existing PSMILES generation and mutation."""
    result: Dict[str, Any] = {
        "candidates": [],
        "biologic_target": biologic_target,
        "errors": [],
    }

    try:
        from biologix_ai.psmiles_generator import PSMILESGenerator

        gen = PSMILESGenerator()
        gen_result = gen.generate_psmiles(
            f"{prompt} for {biologic_target} stabilization"
        )
        if gen_result.get("success"):
            result["candidates"] = [gen_result]
            result["method"] = "llm_generation"
    except ImportError:
        result["errors"].append("psmiles_generator not available")

    if len(result["candidates"]) < library_size:
        try:
            from biologix_ai.mutation import MaterialMutator

            mutator = MaterialMutator(random_seed=42)
            library = mutator.generate_library(library_size=library_size)
            result["candidates"].extend(library)
            result["method"] = result.get("method", "mutation")
        except ImportError:
            result["errors"].append("mutation module not available")

    return result
