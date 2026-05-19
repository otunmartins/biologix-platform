"""Tests for matrix packing proximity metrics."""

import pytest


MINI_PDB = """\
ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C
ATOM      2  CA  ALA A   2       1.500   0.000   0.000  1.00  0.00           C
HETATM    3  C1  UNK C   1       2.000   0.000   0.000  1.00  0.00           C
HETATM    4  C2  UNK C   1       3.400   0.000   0.000  1.00  0.00           C
END
"""


def test_compute_matrix_packing_metrics_two_protein_heavy(tmp_path):
    from insulin_ai.simulation.matrix_packing_metrics import compute_matrix_packing_metrics

    p = tmp_path / "m.pdb"
    p.write_text(MINI_PDB, encoding="utf-8")
    # First two ATOM lines = 2 protein atoms (both heavy); rest = polymer
    r = compute_matrix_packing_metrics(str(p), n_protein_atoms_total=2)
    assert r.get("ok") is True
    assert r["n_protein_heavy"] == 2
    assert r["n_polymer_heavy"] == 2
    assert r["min_polymer_protein_distance_nm"] < 1.0
