"""Tests for simulating arbitrary targets without changing the insulin baseline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

BUNDLED = ROOT / "src" / "python" / "biologix_ai" / "simulation" / "data" / "4F1C.pdb"


def test_bundled_insulin_prep_is_byte_identical_to_the_original_default(tmp_path) -> None:
    pytest.importorskip("openmm")
    from biologix_ai.simulation.openmm_complex import target_protein_chains
    from biologix_ai.simulation.openmm_insulin import prepare_insulin_ab_pdb

    assert target_protein_chains(None) == ("A", "B")
    assert target_protein_chains(str(BUNDLED)) == ("A", "B")
    old = tmp_path / "old.pdb"
    new = tmp_path / "new.pdb"
    prepare_insulin_ab_pdb(str(BUNDLED), str(old))  # the pre-change call
    prepare_insulin_ab_pdb(str(BUNDLED), str(new), chains=target_protein_chains(str(BUNDLED)))
    assert old.read_bytes() == new.read_bytes()


def test_prepared_targets_keep_every_chain(tmp_path) -> None:
    pytest.importorskip("openmm")
    from biologix_ai.simulation.openmm_complex import target_protein_chains
    from biologix_ai.simulation.openmm_insulin import chains_in_pdb, prepare_insulin_ab_pdb

    other = tmp_path / "fab.pdb"
    other.write_text(BUNDLED.read_text())
    assert target_protein_chains(str(other)) is None
    assert target_protein_chains(str(other), ("H", "L")) == ("H", "L")
    out = tmp_path / "all.pdb"
    prepare_insulin_ab_pdb(str(other), str(out), chains=None)
    assert chains_in_pdb(str(out)) == {"A", "B", "C", "D"}


def test_packmol_box_floor_holds_the_protein(tmp_path) -> None:
    from biologix_ai.simulation.packmol_packer import protein_box_floor_nm

    pdb = tmp_path / "long.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  CA  ALA A   2     100.000   0.000   0.000  1.00  0.00           C\n"
    )
    # 100 A span + 2 x 6 A padding + 2 A tolerance = 11.4 nm
    assert protein_box_floor_nm(str(pdb)) == pytest.approx(11.4)
    assert protein_box_floor_nm(str(BUNDLED)) < 7.5  # insulin keeps the 7.5 nm default box


def test_packmol_path_override(monkeypatch, tmp_path) -> None:
    from biologix_ai.simulation import packmol_packer

    fake = tmp_path / "packmol"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("BIOLOGIX_AI_PACKMOL_BIN", str(fake))
    assert packmol_packer.packmol_executable() == str(fake)
    monkeypatch.setenv("BIOLOGIX_AI_PACKMOL_BIN", str(tmp_path / "missing"))
    assert packmol_packer.packmol_executable() is None


def test_platform_selection_never_falls_back_silently(monkeypatch) -> None:
    pytest.importorskip("openmm")
    from biologix_ai.simulation import openmm_complex as oc

    monkeypatch.setattr(oc, "_PLATFORM_CACHE", {})
    monkeypatch.setattr(oc, "_probe_platform", lambda name: (None, f"{name} unavailable"))
    _platform, info = oc.select_openmm_platform("CPU")
    assert info["name"] == "CPU"
    _platform, info = oc.select_openmm_platform("auto")
    assert info["name"] == "CPU" and "CUDA unavailable" in info["fallback_reason"]
    with pytest.raises(oc.OpenMMPlatformError, match="CUDA unavailable"):
        oc.select_openmm_platform("CUDA")
    with pytest.raises(oc.OpenMMPlatformError):
        oc.select_openmm_platform("TPU")


def test_gpu_candidates_run_in_a_fresh_interpreter(monkeypatch) -> None:
    from biologix_ai.simulation import md_simulator

    calls = []
    monkeypatch.setattr(
        md_simulator, "_run_matrix_in_subprocess", lambda p, kw, t: calls.append(kw) or {"ok": True}
    )
    assert md_simulator._run_matrix_eval_with_timeout("[*]CC[*]", {"openmm_platform": "CUDA"}, 5) == {"ok": True}
    assert calls and calls[0]["openmm_platform"] == "CUDA"
    assert md_simulator._gpu_platform_requested({}) is False


def test_matrix_subprocess_reports_errors_as_json(tmp_path) -> None:
    import json

    from biologix_ai.simulation import matrix_subprocess

    request = tmp_path / "req.json"
    response = tmp_path / "resp.json"
    request.write_text(json.dumps({"psmiles": "[*]CC[*]", "kwargs": {"not_a_parameter": 1}}))
    assert matrix_subprocess.main(str(request), str(response)) == 0
    payload = json.loads(response.read_text())
    assert payload["ok"] is False and "not_a_parameter" in payload["error"]


def test_polymer_preflight_flags_unparameterizable_chemistry() -> None:
    pytest.importorskip("openmmforcefields")
    from biologix_ai.simulation.openmm_complex import polymer_md_preflight

    ok = polymer_md_preflight("[*]OCC[*]", n_repeats=2)
    assert ok["md_ready"] is True and ok["n_atoms_per_chain"] > 10
    bad = polymer_md_preflight("not-a-psmiles", n_repeats=2)
    assert bad["md_ready"] is False and bad["stage"] == "oligomer_build"


def test_charged_repeat_units_reach_the_simulation() -> None:
    """Zwitterions and polyelectrolytes are both supported chemistry now.

    The original rule rejected every formal charge, which excluded sulfobetaines,
    phosphorylcholines, polyacrylates and quaternary ammoniums from the platform.
    """
    pytest.importorskip("rdkit")
    from biologix_ai.material_mappings import prescreen_psmiles_for_md

    for psmiles in (
        "[*]CC([*])c1ccc(C[N+](C)(C)CCCS(=O)(=O)[O-])cc1",  # sulfobetaine, net 0
        "[*]CC([*])C(=O)[O-]",                               # polyacrylate, net -1
        "[*]CC[N+](C)(C)C[*]",                               # quaternary ammonium, net +1
        "[*]CC([*])O",                                       # neutral
    ):
        assert prescreen_psmiles_for_md(psmiles) == {"ok": True}, psmiles


def test_preflight_and_prescreen_agree_on_a_zwitterion() -> None:
    """md_ready in screening must not promise what the simulation then refuses."""
    pytest.importorskip("openmmforcefields")
    from biologix_ai.material_mappings import prescreen_psmiles_for_md
    from biologix_ai.simulation.openmm_complex import polymer_md_preflight

    sulfobetaine = "[*]CC([*])c1ccc(C[N+](C)(C)CCCS(=O)(=O)[O-])cc1"
    preflight = polymer_md_preflight(sulfobetaine, n_repeats=2)
    assert preflight["md_ready"] is True and preflight["net_charge"] == 0
    assert prescreen_psmiles_for_md(sulfobetaine)["ok"] is True


# -- counterions for polyelectrolyte matrices ---------------------------------


def test_counterion_arithmetic_neutralises_the_matrix() -> None:
    from biologix_ai.simulation.openmm_complex import counterions_for_matrix

    assert counterions_for_matrix(-1, 8) == ("NA", "Na", 8)   # polyacrylate
    assert counterions_for_matrix(-8, 8) == ("NA", "Na", 64)
    assert counterions_for_matrix(2, 3) == ("CL", "Cl", 6)    # quaternary ammonium
    assert counterions_for_matrix(0, 8) == ("", "", 0)        # zwitterion or neutral


def test_repeat_unit_charge_is_read_before_the_oligomer_is_built() -> None:
    pytest.importorskip("rdkit")
    from biologix_ai.simulation.openmm_complex import repeat_unit_charge

    assert repeat_unit_charge("[*]CC([*])O") == 0
    assert repeat_unit_charge("[*]CC([*])C(=O)[O-]") == -1
    assert repeat_unit_charge("[*]CC([*])c1ccc(C[N+](C)(C)CCCS(=O)(=O)[O-])cc1") == 0
    assert repeat_unit_charge("not a psmiles") == 0


def test_a_neutral_polymer_builds_the_force_field_it_always_did() -> None:
    """Loading the ion file changes energy summation order, so gate it on charge."""
    import inspect

    from biologix_ai.simulation.openmm_complex import (
        ION_FORCEFIELD,
        run_openmm_matrix_relax_and_energy,
    )

    source = inspect.getsource(run_openmm_matrix_relax_and_energy)
    assert 'ff_files = ["amber14-all.xml"] + ([ION_FORCEFIELD] if repeat_charge else [])' in source
    assert ION_FORCEFIELD == "amber14/tip3p.xml"


def test_ion_pdb_and_topology_match_the_amber_templates(tmp_path) -> None:
    pytest.importorskip("openmm")
    import openmm.app as app

    from biologix_ai.simulation.openmm_complex import add_ions_to_topology, write_ion_pdb

    path = tmp_path / "na.pdb"
    write_ion_pdb(str(path), "NA", "Na")
    parsed = app.PDBFile(str(path))
    atoms = list(parsed.topology.atoms())
    assert len(atoms) == 1
    assert atoms[0].name == "NA" and atoms[0].residue.name == "NA"

    topology = app.Topology()
    add_ions_to_topology(topology, "CL", "Cl", 3)
    assert topology.getNumAtoms() == 3
    assert {a.residue.name for a in topology.atoms()} == {"CL"}
    add_ions_to_topology(topology, "CL", "Cl", 0)
    assert topology.getNumAtoms() == 3


def test_packmol_input_packs_the_counterions_after_the_chains() -> None:
    from biologix_ai.simulation.packmol_packer import build_packmol_inp_content

    text = build_packmol_inp_content(
        "prot.pdb", "poly.pdb", 4, "out.pdb", 75.0, 2.0, 42,
        extra_species=[("na.pdb", 12)],
    )
    assert text.index("structure poly.pdb") < text.index("structure na.pdb")
    assert "  number 12\n" in text
    assert "structure na.pdb" not in build_packmol_inp_content(
        "prot.pdb", "poly.pdb", 4, "out.pdb", 75.0, 2.0, 42, extra_species=[("na.pdb", 0)]
    )


def test_packed_positions_account_for_the_extra_ion_atoms(tmp_path) -> None:
    from biologix_ai.simulation.openmm_complex import _read_packed_pdb_positions_nm

    pdb = tmp_path / "packed.pdb"
    lines = [
        f"ATOM  {i:5d}  C   UNK A   1    {i:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
        for i in range(1, 10)
    ]
    pdb.write_text("\n".join(lines) + "\nEND\n")
    protein, matrix = _read_packed_pdb_positions_nm(str(pdb), 2, 2, 2, n_extra_atoms=3)
    assert len(protein) == 2 and len(matrix) == 7  # 2 chains x 2 atoms + 3 ions
    with pytest.raises(ValueError, match="expected"):
        _read_packed_pdb_positions_nm(str(pdb), 2, 2, 2, n_extra_atoms=9)


def test_disabling_neutralisation_refuses_a_charged_matrix(monkeypatch) -> None:
    from biologix_ai.simulation import openmm_complex as oc

    monkeypatch.setenv(oc.NEUTRALIZE_ENV, "no")
    assert oc.neutralization_enabled() is False
    monkeypatch.setenv(oc.NEUTRALIZE_ENV, "yes")
    assert oc.neutralization_enabled() is True
    monkeypatch.delenv(oc.NEUTRALIZE_ENV, raising=False)
    assert oc.neutralization_enabled() is True
