"""Simulation stack: RDKit + OpenMM (optional import check)."""

import pytest


def test_mdsimulator_requires_openmm_stack():
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation import MDSimulator

    if not openmm_available():
        pytest.skip("OpenMM + openmmforcefields + openff.toolkit not installed")
    MDSimulator(n_steps=100)


def test_openmm_available_is_bool():
    from insulin_ai.simulation.openmm_compat import openmm_available

    assert isinstance(openmm_available(), bool)


def test_merge_gro_roundtrip():
    from insulin_ai.simulation.gro_pdb_io import write_gro, read_gro
    import tempfile
    import os

    atoms = [(1, "MOL", "C1", 0.0, 0.0, 0.0), (1, "MOL", "C2", 0.1, 0.0, 0.0)]
    box = (2.0, 2.0, 2.0)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".gro", delete=False) as f:
        p = f.name
    try:
        write_gro(p, "t", atoms, box)
        title, read = read_gro(p)
        assert len(read) == 2
        assert read[0][3] == pytest.approx(0.0)
    finally:
        os.unlink(p)
