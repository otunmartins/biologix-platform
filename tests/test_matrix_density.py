"""Unit tests for density-driven matrix packing (PSP-inspired)."""

import math
import pytest


def test_shell_volume_cm3():
    """Cube minus sphere: V_shell = box³ - (4/3)π R³."""
    from insulin_ai.simulation.matrix_density import shell_volume_cm3

    box_nm = 9.0
    r_ang = 14.0
    v = shell_volume_cm3(box_nm, r_ang)
    box_cm = box_nm * 1e-7
    r_cm = r_ang * 1e-8
    expected = box_cm**3 - (4.0 / 3.0) * math.pi * r_cm**3
    assert v > 0
    assert abs(v - expected) < 1e-15

    v_small = shell_volume_cm3(6.0, 20.0)
    assert v_small > 0
    assert v_small < v


def test_suggest_n_polymers_from_density():
    """For known psmiles, box, density: n_polymers in expected range and scales with density."""
    from insulin_ai.simulation.matrix_density import suggest_n_polymers_from_density

    n1, shell1 = suggest_n_polymers_from_density(
        target_density_g_cm3=0.5,
        psmiles="[*]CC[*]",
        n_repeats=4,
        box_size_nm=9.0,
        shell_inner_angstrom=14.0,
        packing_mode="shell",
    )
    assert 4 <= n1 <= 100
    assert shell1 == 14.0

    n2, _ = suggest_n_polymers_from_density(
        target_density_g_cm3=0.8,
        psmiles="[*]CC[*]",
        n_repeats=4,
        box_size_nm=9.0,
        shell_inner_angstrom=14.0,
        packing_mode="shell",
    )
    assert n2 >= n1


def test_suggest_n_polymers_from_density_bulk():
    """Bulk uses full-box volume; shell_inner is None; n scales above shell for same rho."""
    from insulin_ai.simulation.matrix_density import suggest_n_polymers_from_density

    n_bulk, inner_bulk = suggest_n_polymers_from_density(
        target_density_g_cm3=0.5,
        psmiles="[*]CC[*]",
        n_repeats=4,
        box_size_nm=9.0,
        packing_mode="bulk",
        volume_fraction_polymer=0.92,
    )
    assert inner_bulk is None
    assert 4 <= n_bulk <= 100

    n_shell, inner_shell = suggest_n_polymers_from_density(
        target_density_g_cm3=0.5,
        psmiles="[*]CC[*]",
        n_repeats=4,
        box_size_nm=9.0,
        shell_inner_angstrom=14.0,
        packing_mode="shell",
    )
    assert inner_shell == 14.0
    assert n_bulk >= n_shell


def test_compute_shell_inner_from_pdb():
    """Shell inner from 4F1C PDB returns reasonable radius in Angstrom."""
    from insulin_ai.simulation.matrix_density import compute_shell_inner_from_pdb
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb

    pdb = ensure_insulin_pdb()
    r = compute_shell_inner_from_pdb(pdb)
    assert 10 <= r <= 25


def test_suggest_box_size_from_shell():
    """Box = 2 * (R_inner + thickness + margin)."""
    from insulin_ai.simulation.matrix_density import suggest_box_size_from_shell

    box = suggest_box_size_from_shell(
        shell_inner_angstrom=14.0,
        shell_thickness_angstrom=30.0,
        margin_angstrom=5.0,
    )
    expected_nm = 2.0 * (14 + 30 + 5) * 0.1
    assert abs(box - expected_nm) < 1e-6
