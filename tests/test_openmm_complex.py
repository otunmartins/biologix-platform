"""TDD tests for OpenMM geometry relaxation + interaction energy (no fallbacks)."""

import pytest


def test_parse_ssbond_from_pdb():
    """Parse SSBOND lines from 4F1C; expect 6 total, 3 for chains A+B."""
    from insulin_ai.simulation.openmm_complex import parse_ssbond_pairs

    pairs = parse_ssbond_pairs("SSBOND   1 CYS A    6    CYS A   11\n")
    assert pairs == [("A", 6, "A", 11)]

    pairs = parse_ssbond_pairs(
        "SSBOND   1 CYS A    6    CYS A   11\n"
        "SSBOND   2 CYS A    7    CYS B    7\n"
        "SSBOND   3 CYS A   20    CYS B   19\n"
    )
    assert len(pairs) == 3
    assert ("A", 6, "A", 11) in pairs
    assert ("A", 7, "B", 7) in pairs
    assert ("A", 20, "B", 19) in pairs


def test_parse_ssbond_from_4f1c():
    """Parse SSBOND from actual 4F1C PDB."""
    from pathlib import Path

    from insulin_ai.simulation.openmm_complex import parse_ssbond_pairs
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb

    pdb_path = ensure_insulin_pdb()
    text = Path(pdb_path).read_text()
    pairs = parse_ssbond_pairs(text)
    # 4F1C has 6 SSBOND; we filter to A,B only (3 pairs)
    ab_pairs = [(c1, r1, c2, r2) for c1, r1, c2, r2 in pairs if c1 in "AB" and c2 in "AB"]
    assert len(ab_pairs) == 3
    assert ("A", 6, "A", 11) in ab_pairs
    assert ("A", 7, "B", 7) in ab_pairs
    assert ("A", 20, "B", 19) in ab_pairs


def test_prepare_insulin_ab_pdb_creates_file(tmp_path):
    """prepare_insulin_ab_pdb writes PDB with only chains A+B and SSBOND."""
    from insulin_ai.simulation.openmm_complex import prepare_insulin_ab_pdb
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb

    src = ensure_insulin_pdb()
    out = tmp_path / "insulin_AB.pdb"
    prepare_insulin_ab_pdb(src, str(out))
    assert out.exists()
    text = out.read_text()
    assert "ATOM" in text
    assert "SSBOND" in text
    # No chain C or D in ATOM lines
    lines = [l for l in text.splitlines() if l.startswith("ATOM") or l.startswith("HETATM")]
    chains = {l[21] for l in lines}
    assert chains <= {"A", "B"}


def test_openmm_load_protein_with_disulfides():
    """Load insulin_AB PDB, ensure 3 disulfide bonds in topology."""
    from pathlib import Path

    from insulin_ai.simulation.openmm_complex import (
        prepare_insulin_ab_pdb,
        ensure_disulfide_bonds,
    )
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb
    from openmm.app import PDBFile, Modeller

    src = ensure_insulin_pdb()
    with Path(src).parent.joinpath("insulin_AB.pdb").open("w") as _:
        pass  # placeholder
    prepare_insulin_ab_pdb(src, str(Path(src).parent / "insulin_AB.pdb"))
    ab_path = Path(src).parent / "insulin_AB.pdb"

    pdb = PDBFile(str(ab_path))
    modeller = Modeller(pdb.topology, pdb.positions)
    ensure_disulfide_bonds(modeller, ab_path)
    n_ss = sum(
        1
        for b in modeller.topology.bonds()
        if b.atom1.name == "SG" and b.atom2.name == "SG"
    )
    assert n_ss == 3


def test_openmm_protein_minimization():
    """Load insulin A+B (PDBFixer + disulfides), add H, minimize; potential energy finite."""
    from pathlib import Path

    from insulin_ai.simulation.openmm_complex import (
        prepare_insulin_ab_pdb,
        run_protein_minimization,
    )
    from insulin_ai.simulation.openmm_insulin import load_insulin_modeller
    from insulin_ai.simulation.polymer_build import ensure_insulin_pdb
    from openmm.app import ForceField

    src = ensure_insulin_pdb()
    ab_path = Path(src).parent / "insulin_AB.pdb"
    prepare_insulin_ab_pdb(src, str(ab_path))

    modeller = load_insulin_modeller(str(ab_path), add_ssbond=True, use_pdbfixer=True)
    ff = ForceField("amber14-all.xml")  # vacuum; implicit/gbn2 has stricter C-term matching
    modeller.addHydrogens(ff)

    epot, _ = run_protein_minimization(modeller.topology, modeller.positions, ff)
    assert epot is not None
    assert abs(epot) < 1e7  # Sanity: not exploding


def test_openmm_npt_api_dry_run():
    """Dry-run: validate OpenMM NPT API (integrator.step, getState, state.getPeriodicBoxVectors)."""
    import openmm
    import openmm.app as app
    import openmm.unit as unit
    from io import StringIO

    # 1 water with tip3p - residue name HOH per tip3p.xml
    pdb_str = (
        "HETATM    1  O   HOH A   1       0.000   0.000   0.000  1.00  0.00           O\n"
        "HETATM    2  H1  HOH A   1       0.076   0.058   0.000  1.00  0.00           H\n"
        "HETATM    3  H2  HOH A   1      -0.058  -0.076   0.000  1.00  0.00           H\n"
        "END\n"
    )
    pdb = app.PDBFile(StringIO(pdb_str))
    box_nm = 2.0
    box_vec = [
        unit.Quantity((box_nm, 0, 0), unit.nanometers),
        unit.Quantity((0, box_nm, 0), unit.nanometers),
        unit.Quantity((0, 0, box_nm), unit.nanometers),
    ]
    pdb.topology.setPeriodicBoxVectors(box_vec)
    ff = app.ForceField("tip3p.xml")
    sys = ff.createSystem(
        pdb.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=0.9 * unit.nanometers,
    )
    sys.addForce(
        openmm.MonteCarloBarostat(1.0 * unit.bar, 300 * unit.kelvin, 5)
    )
    integ = openmm.LangevinIntegrator(
        300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds
    )
    ctx = openmm.Context(sys, integ, openmm.Platform.getPlatformByName("CPU"))
    box_nm = 2.0
    ctx.setPeriodicBoxVectors(
        unit.Quantity((box_nm, 0, 0), unit.nanometers),
        unit.Quantity((0, box_nm, 0), unit.nanometers),
        unit.Quantity((0, 0, box_nm), unit.nanometers),
    )
    ctx.setPositions(
        unit.Quantity([[0.0, 0.0, 0.0], [0.076, 0.058, 0.0], [-0.058, -0.076, 0.0]], unit.nanometers)
    )
    integ.step(10)  # integrator.step, not context.step
    state = ctx.getState(getEnergy=True, getPositions=True)
    _ = state.getPositions(asNumpy=True)
    box = state.getPeriodicBoxVectors()
    assert len(box) == 3


def test_ligand_gasteiger_charges():
    """RDKit Gasteiger + OpenFF Molecule; charges assigned."""
    from insulin_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        pytest.skip("openmm + openmmforcefields + openff.toolkit required")

    from insulin_ai.simulation.openmm_complex import rdkit_mol_to_openff_with_gasteiger

    from rdkit import Chem

    mol = Chem.MolFromSmiles("CCO")
    mol = Chem.AddHs(mol)
    from rdkit.Chem import AllChem

    AllChem.EmbedMolecule(mol, randomSeed=42)
    off_mol = rdkit_mol_to_openff_with_gasteiger(mol)
    assert off_mol is not None
    assert off_mol.n_atoms == mol.GetNumAtoms()
    # Partial charges set
    pc = off_mol.partial_charges
    assert pc is not None
    assert len(pc) == off_mol.n_atoms


def test_openmm_complex_minimization_and_interaction():
    """Insulin + small ligand; minimize; interaction energy computed."""
    from insulin_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        pytest.skip("openmm + openmmforcefields + openff.toolkit required")

    from insulin_ai.simulation.openmm_complex import run_openmm_relax_and_energy

    res = run_openmm_relax_and_energy(
        "[*]CC[*]",
        n_repeats=1,
        ligand_offset_nm=(2.0, 0.0, 0.0),
        max_minimize_steps=500,
    )
    assert res is not None
    assert "potential_energy_complex_kj_mol" in res
    assert "interaction_energy_kj_mol" in res
    assert res["potential_energy_complex_kj_mol"] is not None
    # Interaction can be + or -; just check it's finite
    assert abs(res["interaction_energy_kj_mol"]) < 1e6


def test_openmm_complex_writes_minimized_pdb(tmp_path):
    """Optional save_complex_pdb writes a PDB with ATOM records."""
    from insulin_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        pytest.skip("openmm + openmmforcefields + openff.toolkit required")

    from insulin_ai.simulation.openmm_complex import run_openmm_relax_and_energy

    out_pdb = tmp_path / "complex_min.pdb"
    res = run_openmm_relax_and_energy(
        "[*]CC[*]",
        n_repeats=1,
        ligand_offset_nm=(2.0, 0.0, 0.0),
        max_minimize_steps=300,
        save_complex_pdb=str(out_pdb),
    )
    assert res is not None
    assert res.get("complex_pdb_path") == str(out_pdb.resolve())
    text = out_pdb.read_text(encoding="utf-8")
    assert "ATOM" in text or "HETATM" in text


def test_openmm_matrix_density_driven():
    """Density-driven matrix encapsulation: Packmol + minimize + interaction energy."""
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.openmm_complex import run_openmm_matrix_relax_and_energy
    from insulin_ai.simulation.packmol_packer import _packmol_available

    if not openmm_available():
        pytest.skip("openmm + openmmforcefields + openff.toolkit required")
    if not _packmol_available():
        pytest.skip("packmol not found")

    res = run_openmm_matrix_relax_and_energy(
        "[*]CC[*]",
        n_repeats=2,
        box_size_nm=9.0,
        target_density_g_cm3=0.5,
        max_minimize_steps=500,
    )
    assert res is not None
    assert "interaction_energy_kj_mol" in res
    assert "n_polymer_chains" in res
    assert res.get("packing_mode") == "bulk"
    assert res["n_polymer_chains"] >= 4
    assert abs(res["interaction_energy_kj_mol"]) < 1e6


