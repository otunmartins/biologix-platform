#!/usr/bin/env python3
"""
Feedback-guided mutation: bias toward high performers, avoid problematic features.

Uses psmiles similarity (Ramprasad) to favor structures near successful candidates
and diversify away from problematic ones. Combines 70% feedback-guided, 30% random.
"""

from typing import Dict, List, Any, Optional

from .blocks import get_random_blocks, get_functional_groups
from .generator import MaterialMutator


def _similarity(psmiles1: str, psmiles2: str) -> float:
    """Compute similarity between two PSMILES. Returns 0 on error."""
    try:
        from psmiles import PolymerSmiles
        ps1 = PolymerSmiles(psmiles1)
        ps2 = PolymerSmiles(psmiles2)
        return float(ps1.similarity(ps2))
    except Exception:
        return 0.0


def feedback_guided_mutation(
    feedback_state: Dict[str, Any],
    library_size: int = 10,
    feedback_fraction: float = 0.7,
    random_seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Generate candidates informed by MD feedback.
    
    Args:
        feedback_state: Dict with high_performer_psmiles, problematic_psmiles,
                        high_performers (names), limitations.
        library_size: Number of candidates to generate.
        feedback_fraction: Fraction from feedback-guided vs random (0-1).
        random_seed: For reproducibility.
        
    Returns:
        List of candidate dicts compatible with MDSimulator.evaluate_candidates.
    """
    mutator = MaterialMutator(random_seed=random_seed)
    high_psmiles: List[str] = feedback_state.get("high_performer_psmiles") or []
    problematic: List[str] = feedback_state.get("problematic_psmiles") or []
    blocks = get_random_blocks()
    functional = list(get_functional_groups().values())
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    n_feedback = int(library_size * feedback_fraction)
    n_random = library_size - n_feedback

    def _score_block(block: str) -> float:
        """Higher = better. Favor similarity to high performers."""
        if not high_psmiles:
            return 1.0
        sims = [_similarity(block, h) for h in high_psmiles]
        return max(sims) if sims else 0.0

    def _avoid_problematic(block: str) -> bool:
        """True if block is not in problematic set."""
        if not problematic:
            return True
        for p in problematic:
            if _similarity(block, p) > 0.8:
                return False
        return True

    for i in range(n_feedback):
        if high_psmiles:
            weighted = [(b, _score_block(b)) for b in blocks if _avoid_problematic(b)]
            if not weighted:
                weighted = [(b, 1.0) for b in blocks]
            total = sum(w for _, w in weighted)
            if total <= 0:
                chosen = mutator.rng.choice(blocks)
            else:
                r = mutator.rng.uniform(0, total)
                for b, w in weighted:
                    r -= w
                    if r <= 0:
                        chosen = b
                        break
                else:
                    chosen = weighted[-1][0]
        else:
            chosen = mutator.rng.choice(blocks)

        if chosen in seen:
            chosen = mutator.rng.choice(blocks)
        seen.add(chosen)

        cand = {
            "material_name": f"FB_{i:03d}",
            "chemical_structure": chosen,
            "base_psmiles": chosen,
            "functional_psmiles": mutator.rng.choice(functional),
            "generation_method": "feedback_guided",
            "candidate_id": f"FB_{i:03d}",
        }
        out.append(cand)

    for i in range(n_random):
        cands = mutator.generate_library(library_size=1)
        c = cands[0]
        psm = c["chemical_structure"]
        if psm not in seen:
            seen.add(psm)
            c["material_name"] = f"RND_{i:03d}"
            c["candidate_id"] = f"RND_{i:03d}"
            out.append(c)

    return out[:library_size]
