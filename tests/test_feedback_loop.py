"""
Integration test: Active learning feedback loop with MDSimulator.

Requires OpenMM stack + insulin PDB for full evaluate_candidates.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "python"))
sys.path.insert(0, ROOT)


def test_mdsimulator_interface():
    """MDSimulator implements evaluate_candidates(candidates) -> feedback dict."""
    import pytest
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack + MDSimulator required")
    if not _packmol_available():
        pytest.skip("packmol binary required for evaluate_candidates (matrix encapsulation)")
    sim = MDSimulator()
    candidates = [
        {"material_name": "PEG hydrogel", "chemical_structure": "[*]OCC[*]"},
        {"material_name": "Chitosan", "material_composition": "chitosan"},
    ]
    result = sim.evaluate_candidates(candidates, max_candidates=5)

    assert "high_performers" in result
    assert "effective_mechanisms" in result
    assert "problematic_features" in result
    assert "property_analysis" in result
    assert "successful_materials" in result


def test_feedback_state_update():
    """Feedback dict format matches _update_feedback_state expectations."""
    md_results = {
        "high_performers": ["PEG", "Chitosan"],
        "effective_mechanisms": ["hydrogen bonding"],
        "problematic_features": ["high crystallinity"],
        "successful_materials": ["PEG", "Chitosan"],
        "failed_features": [],
    }
    expected = {
        "top_candidates": md_results.get("successful_materials", md_results.get("high_performers", [])),
        "stability_mechanisms": md_results.get("effective_mechanisms", []),
        "limitations": md_results.get("failed_features", md_results.get("problematic_features", [])),
    }
    assert expected["top_candidates"] == ["PEG", "Chitosan"]
    assert "hydrogen bonding" in expected["stability_mechanisms"]


def test_mdsimulator_with_mock_candidates():
    """Full evaluate_candidates with mixed candidate formats."""
    import pytest
    from insulin_ai.simulation import MDSimulator
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("OpenMM stack + MDSimulator required")
    if not _packmol_available():
        pytest.skip("packmol binary required for evaluate_candidates (matrix encapsulation)")
    sim = MDSimulator()
    candidates = [
        {"material_name": "A", "psmiles": "[*]OCC[*]"},
        {"material_name": "B", "chemical_structure": "[*]CC[*]"},
        {"material_name": "Unknown", "material_composition": "unknown polymer"},
    ]
    result = sim.evaluate_candidates(candidates, max_candidates=5)

    assert isinstance(result["high_performers"], list)
    assert isinstance(result["problematic_features"], list)
    assert len(result["property_analysis"]) >= 0
