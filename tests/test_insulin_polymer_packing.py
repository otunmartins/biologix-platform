"""Packmol import; no OpenMM."""

import pytest


def test_packmol_packer_import():
    from insulin_ai.simulation.packmol_packer import pack_insulin_polymers, _packmol_available

    assert callable(pack_insulin_polymers)
    assert isinstance(_packmol_available(), bool)


def test_packmol_inp_contains_shell_when_requested():
    from insulin_ai.simulation.packmol_packer import build_packmol_inp_content

    # 10 nm cube → 100 Å edge; insulin fixed at (50, 50, 50)
    s = build_packmol_inp_content(
        "/a/ins.pdb",
        "/b/poly.pdb",
        3,
        "/c/out.pdb",
        box_edge_angstrom=100.0,
        tolerance_angstrom=2.0,
        seed=1,
        shell_only_angstrom=12.0,
        packing_mode="shell",
    )
    assert "outside sphere" in s
    assert "12.00" in s or "12.0" in s
    assert "fixed 50.00 50.00 50.00" in s
    assert "maxit 20" in s
    assert "nloop 200" in s
    assert "movebadrandom" in s


def test_packmol_inp_bulk_has_no_outside_sphere():
    """Bulk mode fills the cell; shell radius is ignored for Packmol constraints."""
    from insulin_ai.simulation.packmol_packer import build_packmol_inp_content

    s = build_packmol_inp_content(
        "/a/ins.pdb",
        "/b/poly.pdb",
        3,
        "/c/out.pdb",
        box_edge_angstrom=100.0,
        tolerance_angstrom=2.0,
        seed=1,
        shell_only_angstrom=12.0,
        packing_mode="bulk",
    )
    assert "outside sphere" not in s
    assert "inside box" in s
    assert "1.00 1.00 1.00" in s  # lo = tol/2
    assert "99.00 99.00 99.00" in s  # hi = L - lo


def test_parse_pdb_extents(tmp_path):
    from insulin_ai.simulation.packmol_packer import _parse_pdb_extents

    p = tmp_path / "t.pdb"
    p.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1      10.000   0.000   0.000  1.00  0.00           C\n",
        encoding="utf-8",
    )
    n, spans = _parse_pdb_extents(str(p))
    assert n == 2
    assert spans == (10.0, 0.0, 0.0)


def test_estimate_box_edge_angstrom(tmp_path):
    from insulin_ai.simulation.packmol_packer import estimate_box_edge_angstrom

    ins = tmp_path / "ins.pdb"
    poly = tmp_path / "poly.pdb"
    ins.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n",
        encoding="utf-8",
    )
    poly.write_text(
        "ATOM      1  C   ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  O   ALA A   1       5.000   0.000   0.000  1.00  0.00           O\n",
        encoding="utf-8",
    )
    edge = estimate_box_edge_angstrom(
        str(ins),
        str(poly),
        n_polymers=2,
        tolerance_angstrom=2.0,
        padding_angstrom=6.0,
        volume_per_atom_A3=20.0,
        packing_fraction=0.40,
    )
    assert edge > 0
    assert edge >= 2.0 + 12.0  # tol + 2*padding from insulin extent branch


def test_pack_insulin_polymers_progressive_stops_when_increment_fails(monkeypatch):
    import insulin_ai.simulation.packmol_packer as pp

    def fake_pack(insulin_pdb_path, polymer_pdb_path, n_polymers, output_path, **kw):
        if n_polymers <= 4:
            return {
                "success": True,
                "box_edge_angstrom": 90.0,
                "box_edge_nm": 9.0,
                "stdout": "",
                "stderr": "",
            }
        return {
            "success": False,
            "box_edge_angstrom": 90.0,
            "box_edge_nm": 9.0,
            "stdout": "",
            "stderr": "fail",
        }

    monkeypatch.setattr(pp, "pack_insulin_polymers", fake_pack)
    r = pp.pack_insulin_polymers_progressive(
        "/tmp/a.pdb",
        "/tmp/b.pdb",
        2,
        "/tmp/out.pdb",
        per_attempt_timeout_s=30.0,
    )
    assert r["success"] is True
    assert r["n_polymers"] == 4
    assert r["stopped_reason"] == "increment_failed_or_timeout"
    assert r["attempts"] == 4


def test_pack_insulin_polymers_progressive_n_cap(monkeypatch):
    import insulin_ai.simulation.packmol_packer as pp

    def fake_pack(insulin_pdb_path, polymer_pdb_path, n_polymers, output_path, **kw):
        return {
            "success": True,
            "box_edge_angstrom": 100.0,
            "box_edge_nm": 10.0,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(pp, "pack_insulin_polymers", fake_pack)
    r = pp.pack_insulin_polymers_progressive(
        "/tmp/a.pdb",
        "/tmp/b.pdb",
        3,
        "/tmp/out.pdb",
        n_polymers_cap=5,
    )
    assert r["success"] is True
    assert r["n_polymers"] == 5
    assert r["stopped_reason"] == "n_cap"


def test_pack_insulin_polymers_returns_dict():
    from insulin_ai.simulation.packmol_packer import pack_insulin_polymers

    r = pack_insulin_polymers(
        "/nonexistent/ins.pdb",
        "/nonexistent/poly.pdb",
        1,
        "/tmp/out.pdb",
        box_size_nm=5.0,
    )
    assert isinstance(r, dict)
    assert "success" in r
    assert "box_edge_nm" in r
    assert r["success"] is False


def test_polymer_build_ensure_pdb_or_skip():
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb

    try:
        p = ensure_insulin_pdb()
        assert p.endswith(".pdb")
    except FileNotFoundError:
        pytest.skip("4F1C.pdb not present")
