"""
Unit tests for cheminformatics mutation module.

Tests MaterialMutator, blocks, and feedback_guided_mutation.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))
sys.path.insert(0, ROOT)


def test_blocks_import():
    """Blocks module exports expected functions."""
    from insulin_ai.mutation.blocks import get_random_blocks, get_functional_groups, get_all_blocks

    blocks = get_random_blocks()
    assert len(blocks) >= 10
    assert "[*]OCC[*]" in blocks or "[*]CC[*]" in blocks
    assert all("[*]" in b for b in blocks)

    fg = get_functional_groups()
    assert "hydroxyl" in fg
    assert "aromatic" in fg
    assert "[*]" in fg["hydroxyl"]

    all_b = get_all_blocks()
    assert len(all_b) >= len(blocks) + len(fg)


def test_material_mutator_generate_library():
    """MaterialMutator.generate_library returns valid candidates."""
    from insulin_ai.mutation import MaterialMutator

    mutator = MaterialMutator(random_seed=42)
    cands = mutator.generate_library(library_size=5)

    assert len(cands) == 5
    for c in cands:
        assert "material_name" in c
        assert "chemical_structure" in c
        assert "[*]" in c["chemical_structure"]
        assert "base_psmiles" in c
        assert "generation_method" in c
        assert c["generation_method"] == "systematic_exploration"


def test_material_mutator_reproducible():
    """Same seed yields same library."""
    from insulin_ai.mutation import MaterialMutator

    m1 = MaterialMutator(random_seed=123)
    m2 = MaterialMutator(random_seed=123)
    c1 = m1.generate_library(library_size=3)
    c2 = m2.generate_library(library_size=3)

    assert [x["chemical_structure"] for x in c1] == [x["chemical_structure"] for x in c2]


def test_feedback_guided_mutation_random_only():
    """feedback_guided_mutation with empty feedback returns random-like candidates."""
    from insulin_ai.mutation import feedback_guided_mutation

    feedback = {}
    cands = feedback_guided_mutation(feedback, library_size=5, random_seed=42)

    assert len(cands) >= 1
    for c in cands[:5]:
        assert "chemical_structure" in c
        assert "material_name" in c
        assert "[*]" in c["chemical_structure"]


def test_feedback_guided_mutation_with_high_performers():
    """feedback_guided_mutation with high_performer_psmiles runs without error."""
    from insulin_ai.mutation import feedback_guided_mutation

    feedback = {"high_performer_psmiles": ["[*]OCC[*]", "[*]CC[*]"]}
    try:
        cands = feedback_guided_mutation(
            feedback, library_size=5, feedback_fraction=0.7, random_seed=42
        )
        assert len(cands) >= 1
    except ImportError:
        import pytest
        pytest.skip("psmiles not installed")


def test_mutation_candidates_compatible_with_mdsimulator():
    """Mutation output format compatible with MDSimulator.evaluate_candidates."""
    from insulin_ai.mutation import MaterialMutator
    from insulin_ai.simulation import MDSimulator

    mutator = MaterialMutator(random_seed=42)
    cands = mutator.generate_library(library_size=2)
    assert all("chemical_structure" in c and "material_name" in c for c in cands)

    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        import pytest
        pytest.skip("OpenMM stack required for evaluate_candidates")
    if not _packmol_available():
        import pytest
        pytest.skip("packmol binary required for evaluate_candidates (matrix encapsulation)")
    sim = MDSimulator(n_steps=500)
    result = sim.evaluate_candidates(cands, max_candidates=2)

    assert "high_performers" in result
    assert "effective_mechanisms" in result
    assert "problematic_features" in result
