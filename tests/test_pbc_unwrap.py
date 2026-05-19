"""Bond-consistent PBC unwrapping for visualization PDBs (no PyMOL / Packmol)."""

import numpy as np
import pytest

pytest.importorskip("openmm", reason="pbc_unwrap requires OpenMM")

import openmm.app as app


def _make_two_atom_bonded_topology():
    top = app.Topology()
    ch = top.addChain("A")
    r = top.addResidue("UNK", ch)
    e = app.element.carbon
    a0 = top.addAtom("C1", e, r)
    a1 = top.addAtom("C2", e, r)
    top.addBond(a0, a1)
    return top


def test_min_image_displacement():
    from insulin_ai.simulation.pbc_unwrap import min_image_displacement

    L = 2.0
    dr = np.array([1.8, 0.0, 0.0])
    out = min_image_displacement(dr, L)
    assert np.allclose(out, np.array([-0.2, 0.0, 0.0]))


def test_unwrap_restores_short_bond_across_periodic_image():
    from insulin_ai.simulation.pbc_unwrap import unwrap_bond_consistent_pbc

    top = _make_two_atom_bonded_topology()
    L = 2.0
    # Bond appears ~1.8 nm apart in raw Cartesian coords but is ~0.2 nm through PBC
    pos = np.array(
        [
            [0.1, 0.5, 0.5],
            [1.9, 0.5, 0.5],
        ],
        dtype=float,
    )
    out = unwrap_bond_consistent_pbc(pos, top, L)
    dist = float(np.linalg.norm(out[1] - out[0]))
    assert dist < 0.25, f"unwrapped bond length {dist} nm should be chemical"


def test_center_protein_com_at_cubic_cell_center():
    from insulin_ai.simulation.pbc_unwrap import center_protein_com_at_cubic_cell_center

    L = 4.0
    pos = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=float)
    out = center_protein_com_at_cubic_cell_center(pos, n_protein=1, box_edge_nm=L)
    assert np.allclose(out[0], [L / 2, L / 2, L / 2])
    assert np.allclose(out[1] - out[0], pos[1] - pos[0])


def test_prepare_matrix_complex_matches_unwrap_then_center():
    from insulin_ai.simulation.pbc_unwrap import (
        center_protein_com_at_cubic_cell_center,
        prepare_matrix_complex_pdb_positions_nm,
        unwrap_bond_consistent_pbc,
    )

    top = _make_two_atom_bonded_topology()
    L = 2.0
    pos = np.array([[0.1, 0.5, 0.5], [1.9, 0.5, 0.5]], dtype=float)
    ref = center_protein_com_at_cubic_cell_center(
        unwrap_bond_consistent_pbc(pos, top, L), n_protein=1, box_edge_nm=L
    )
    got = prepare_matrix_complex_pdb_positions_nm(pos, top, n_protein=1, box_edge_nm=L)
    assert np.allclose(got, ref)


def test_cubic_box_edge_from_vectors():
    from insulin_ai.simulation.pbc_unwrap import cubic_box_edge_nm_from_vectors
    import openmm.unit as u

    L = 5.0
    a = u.Quantity((L, 0, 0), u.nanometers)
    b = u.Quantity((0, L, 0), u.nanometers)
    c = u.Quantity((0, 0, L), u.nanometers)
    assert cubic_box_edge_nm_from_vectors((a, b, c)) == pytest.approx(L)
