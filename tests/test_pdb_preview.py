"""Tests for PDB → PNG preview (matplotlib)."""

import pytest


MINI_PDB = """\
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
END
"""


def test_write_complex_preview_png_writes_file(tmp_path):
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        pytest.skip("matplotlib not installed")

    from insulin_ai.simulation.pdb_preview import write_complex_preview_png

    pdb = tmp_path / "t.pdb"
    pdb.write_text(MINI_PDB, encoding="utf-8")
    png = tmp_path / "out.png"
    r = write_complex_preview_png(str(pdb), str(png))
    assert r.get("ok") is True
    assert png.is_file()
    assert r.get("n_atoms") == 3


def test_write_complex_preview_png_missing_pdb(tmp_path):
    from insulin_ai.simulation.pdb_preview import write_complex_preview_png

    r = write_complex_preview_png(str(tmp_path / "nope.pdb"), str(tmp_path / "x.png"))
    assert r.get("ok") is False
