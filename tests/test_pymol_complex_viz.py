"""PyMOL complex visualization (optional binary on PATH)."""

import pytest


def test_build_pymol_complex_script_index_selection(tmp_path):
    from insulin_ai.simulation.pymol_complex_viz import build_pymol_complex_script

    pdb = tmp_path / "a.pdb"
    png = tmp_path / "o.png"
    pdb.write_text("ATOM\n", encoding="utf-8")
    s = build_pymol_complex_script(pdb, png, n_protein_atoms=1200)
    assert "index 1-1200" in s
    assert "dss prot" in s
    assert "show cartoon, prot" in s
    assert "show sticks, poly" in s
    assert "stick_ball, 1" in s


def test_build_pymol_complex_script_chain_fallback(tmp_path):
    from insulin_ai.simulation.pymol_complex_viz import build_pymol_complex_script

    pdb = tmp_path / "a.pdb"
    png = tmp_path / "o.png"
    pdb.write_text("ATOM\n", encoding="utf-8")
    s = build_pymol_complex_script(
        pdb, png, n_protein_atoms=None, protein_chains=("A", "B")
    )
    assert "chain A" in s and "chain B" in s
    assert "dss prot" in s


def test_write_complex_viz_png_auto_is_pymol_only(tmp_path):
    from insulin_ai.simulation.pymol_complex_viz import write_complex_viz_png_auto

    pdb = tmp_path / "c.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "HETATM    2  C1  UNK C   1      20.000   0.000   0.000  1.00  0.00           C\n"
        "END\n",
        encoding="utf-8",
    )
    png = tmp_path / "out.png"
    r, backend = write_complex_viz_png_auto(
        str(pdb),
        str(png),
        n_protein_atoms=1,
        protein_chains=("A",),
    )
    assert backend == "pymol"
    # ``which(pymol)`` can be true while the binary fails at runtime (e.g. missing GLEW).
    if r.get("ok"):
        assert png.is_file()
    else:
        assert r.get("error")
        assert "pymol" in (r.get("error") or "").lower()


def test_write_complex_pymol_png_integration(tmp_path):
    from insulin_ai.simulation.pymol_complex_viz import write_complex_pymol_png

    pdb = tmp_path / "c.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  CA  ALA A   2       3.800   0.000   0.000  1.00  0.00           C\n"
        "HETATM    3  C1  UNK C   1      20.000   0.000   0.000  1.00  0.00           C\n"
        "END\n",
        encoding="utf-8",
    )
    png = tmp_path / "out.png"
    r = write_complex_pymol_png(str(pdb), str(png), n_protein_atoms=2)
    if not r.get("ok"):
        pytest.skip(f"pymol not runnable: {r.get('error', '')[:300]}")
    assert png.is_file()
