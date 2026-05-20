#!/usr/bin/env python3
"""
MaterialMutator - Cheminformatics-based polymer candidate generation.

Generates material libraries via random combination of PSMILES blocks.
Implements logic from FRIDGEFREENET proposal.tex.
"""

import random
from typing import Dict, List, Any, Optional

from .blocks import get_random_blocks, get_functional_groups


class MaterialMutator:
    """
    Generates polymer material candidates for systematic screening.
    
    Uses random block selection and optional functional group combination
    for chemical space exploration.
    """

    def __init__(self, random_seed: Optional[int] = None):
        """
        Args:
            random_seed: For reproducibility. If None, uses system random.
        """
        self.rng = random.Random(random_seed) if random_seed is not None else random
        self._blocks = get_random_blocks()
        self._functional = get_functional_groups()

    def _random_psmiles(self) -> str:
        """Pick a random polymer block for exploration."""
        return self.rng.choice(self._blocks)

    def _functional_group_psmiles(self) -> str:
        """Pick a random functional group variant."""
        return self.rng.choice(list(self._functional.values()))

    def generate_library(
        self,
        base_insights: Optional[Dict[str, Any]] = None,
        library_size: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Generate a library of material candidates.
        
        Args:
            base_insights: Optional feedback state from previous iteration.
                          Unused in random mode; used by feedback_mutation.
            library_size: Number of candidates to generate.
            
        Returns:
            List of candidate dicts with material_name, chemical_structure,
            base_psmiles, functional_psmiles, generation_method.
        """
        out = []
        seen: set[str] = set()

        for i in range(library_size):
            base_block = self._random_psmiles()
            functional_block = self._functional_group_psmiles()
            psmiles = self._combine_blocks(base_block, functional_block)

            if psmiles in seen:
                psmiles = base_block
            seen.add(psmiles)

            cand = {
                "material_name": f"MAT_{i:03d}",
                "chemical_structure": psmiles,
                "base_psmiles": base_block,
                "functional_psmiles": functional_block,
                "generation_method": "systematic_exploration",
                "candidate_id": f"MAT_{i:03d}",
            }
            out.append(cand)

        return out

    def _combine_blocks(self, base: str, functional: str) -> str:
        """
        Combine base and functional blocks into a single PSMILES copolymer unit.

        Tries to dimerize base with functional via the ``psmiles`` package.  If
        that fails, falls back to a base self-dimer; if that also fails, returns
        the raw base block.
        """
        try:
            from psmiles import PolymerSmiles
            ps_base = PolymerSmiles(base)
            ps_func = PolymerSmiles(functional)
            if hasattr(ps_base, "dimer"):
                combined = str(ps_base.dimer(0, other=ps_func))
            else:
                combined = str(ps_base.dimerize(star_index=0, other=ps_func))
            if "[*]" in combined:
                return combined
        except Exception:
            pass
        try:
            from psmiles import PolymerSmiles
            ps = PolymerSmiles(base)
            if hasattr(ps, "dimer"):
                return str(ps.dimer(self.rng.choice([0, 1])))
            return str(ps.dimerize(star_index=self.rng.choice([0, 1])))
        except Exception:
            return base
