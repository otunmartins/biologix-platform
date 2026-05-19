"""Tests for discovery_score and composite_screening_score."""

import pytest

from insulin_ai.simulation.scoring import composite_screening_score, discovery_score


def test_discovery_score_empty():
    assert discovery_score({}) == 0.0
    assert discovery_score({"high_performers": [], "effective_mechanisms": [], "problematic_features": []}) == 0.0


def test_discovery_score_positive():
    fb = {
        "high_performers": ["a", "b"],
        "effective_mechanisms": ["hydrogen_bonding"],
        "problematic_features": [],
    }
    # 2*2 + 1*0.5 - 0 = 4.5
    assert discovery_score(fb) == pytest.approx(4.5)


def test_discovery_score_penalty():
    fb = {
        "high_performers": ["x"],
        "effective_mechanisms": [],
        "problematic_features": ["bad1", "bad2"],
    }
    # 1*2 - 2*1 = 0
    assert discovery_score(fb) == pytest.approx(0.0)


def test_discovery_score_custom_weights():
    fb = {"high_performers": [1], "effective_mechanisms": [], "problematic_features": []}
    assert discovery_score(fb, high_performer_weight=10.0) == 10.0


def test_discovery_score_interaction_bonus():
    fb = {
        "high_performers": [],
        "effective_mechanisms": [],
        "problematic_features": [],
        "property_analysis": {
            "a": {"interaction_energy_kj_mol": -100.0},
        },
    }
    # no rmsd -> fallback interaction bonus
    assert discovery_score(fb, use_composite=False) == pytest.approx(2.0)


def test_composite_screening_score_balance():
    # Stable host (negative E_int) + low distortion (low RMSD) -> higher than unstable or floppy
    good = composite_screening_score(-150.0, 0.05)
    bad_e = composite_screening_score(100.0, 0.05)
    bad_r = composite_screening_score(-150.0, 0.6)
    assert good > bad_e and good > bad_r


def test_discovery_score_composite_branch():
    fb = {
        "high_performers": [],
        "effective_mechanisms": [],
        "problematic_features": [],
        "property_analysis": {
            "a": {
                "interaction_energy_kj_mol": -150.0,
                "insulin_rmsd_to_initial_nm": 0.06,
            },
        },
    }
    s = discovery_score(fb, use_composite=True, composite_scale=1.0)
    assert s == pytest.approx(composite_screening_score(-150.0, 0.06))
