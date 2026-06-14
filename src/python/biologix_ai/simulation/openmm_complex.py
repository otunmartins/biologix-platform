#!/usr/bin/env python3
"""
OpenMM geometry relaxation and interaction energy (no acpype/antechamber).

- Insulin: AMBER14SB, disulfide bonds from SSBOND.
- Ligand: GAFF via openmmforcefields, charges from RDKit Gasteiger.
"""

from __future__ import annotations

import math
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _stage_heartbeat(stage: str, msg: str) -> None:
    """Emit stage progress to stderr unless the user opted out via BIOLOGIX_AI_EVAL_QUIET."""
    if os.environ.get("BIOLOGIX_AI_EVAL_QUIET", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    print(f"[biologix-ai] stage={stage} {msg}", file=sys.stderr, flush=True)
    hook = _STAGE_HEARTBEAT_HOOK
    if hook is not None:
        try:
            hook(stage, msg)
        except Exception:
            pass


_STAGE_HEARTBEAT_HOOK: Optional[Any] = None


def register_stage_heartbeat_hook(hook: Optional[Any]) -> None:
    """Register optional callback ``hook(stage, msg)`` for MCP progress mirroring."""
    global _STAGE_HEARTBEAT_HOOK
    _STAGE_HEARTBEAT_HOOK = hook


def clear_stage_heartbeat_hook() -> None:
    """Remove any registered stage heartbeat hook."""
    register_stage_heartbeat_hook(None)


# OpenMM
import openmm
import openmm.app as app
import openmm.unit as unit

# RDKit for ligand charges
from rdkit import Chem
from rdkit.Chem import rdPartialCharges

from .openmm_insulin import (
    prepare_insulin_ab_pdb,
    add_disulfide_bonds_from_ssbond,
    load_insulin_modeller,
    parse_ssbond_from_pdb,
)
from .pbc_unwrap import prepare_matrix_complex_pdb_positions_nm
from .polymer_build import build_polymer_oligomer_smiles
from .polymer_build import embed_mol_3d


def parse_ssbond_pairs(text_or_path: str) -> List[Tuple[str, int, str, int]]:
    """
    Parse SSBOND lines; return List[(chain1, resseq1, chain2, resseq2)].
    If text_or_path contains newline, treat as PDB text; else if it is an
    existing file path, read it; otherwise use as text.
    """
    if "\n" in text_or_path:
        text = text_or_path
    else:
        p = Path(text_or_path).expanduser().resolve()
        text = p.read_text() if p.is_file() else text_or_path
    pairs: List[Tuple[str, int, str, int]] = []
    for line in text.splitlines():
        if not line.startswith("SSBOND"):
            continue
        if len(line) < 29:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            cys1, ch1, res1 = parts[2], parts[3], int(parts[4])
            cys2, ch2, res2 = parts[5], parts[6], int(parts[7])
        except (IndexError, ValueError):
            continue
        if cys1 == "CYS" and cys2 == "CYS":
            pairs.append((ch1, res1, ch2, res2))
    return pairs


# prepare_insulin_ab_pdb imported from openmm_insulin


def ensure_disulfide_bonds(modeller, pdb_path: str) -> None:
    """Add disulfide bonds from SSBOND in PDB to modeller topology."""
    add_disulfide_bonds_from_ssbond(modeller, pdb_path)


def rdkit_mol_to_openff_with_gasteiger(rdkit_mol: Chem.Mol):
    """Create OpenFF Molecule with RDKit Gasteiger charges (no antechamber)."""
    from openff.toolkit import Molecule
    from openff.units import unit as off_unit

    rdPartialCharges.ComputeGasteigerCharges(rdkit_mol)
    charges = [
        rdkit_mol.GetAtomWithIdx(i).GetDoubleProp("_GasteigerCharge")
        for i in range(rdkit_mol.GetNumAtoms())
    ]
    n_nan = sum(1 for c in charges if math.isnan(c))
    if n_nan > 0:
        logger.warning(
            "Gasteiger produced %d NaN charge(s) out of %d atoms; zeroing them. "
            "Electrostatics for this candidate may be unreliable.",
            n_nan, len(charges),
        )
    charges = [0.0 if math.isnan(c) else c for c in charges]
    mol_off = Molecule.from_rdkit(rdkit_mol, allow_undefined_stereo=True)
    mol_off.partial_charges = off_unit.Quantity(charges, off_unit.elementary_charge)
    return mol_off


def run_protein_minimization(
    topology: app.Topology,
    positions: unit.Quantity,
    forcefield: app.ForceField,
    max_steps: int = 5000,
) -> Tuple[Optional[float], unit.Quantity]:
    """Minimize protein; return (potential_energy_kj_mol, minimized_positions)."""
    system = forcefield.createSystem(
        topology,
        nonbondedMethod=app.NoCutoff,
        constraints=app.HBonds,
    )
    integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
    platform = openmm.Platform.getPlatformByName("CPU")
    ctx = openmm.Context(system, integ, platform)
    ctx.setPositions(positions)
    openmm.LocalEnergyMinimizer.minimize(ctx, maxIterations=max_steps)
    state = ctx.getState(getEnergy=True, getPositions=True)
    energy = state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    pos = state.getPositions(asNumpy=True)
    return float(energy), pos


def _merge_topologies_with_maps(
    protein_top: app.Topology, lig_top: app.Topology
) -> Tuple[app.Topology, Dict, Dict]:
    """Merge protein and ligand topologies; return (combined_top, atom_map_prot, atom_map_lig)."""
    combined = app.Topology()
    chain_map: Dict = {}
    atom_map_prot: Dict = {}
    atom_map_lig: Dict = {}

    for chain in protein_top.chains():
        c = combined.addChain(chain.id)
        chain_map[chain] = c
    for res in protein_top.residues():
        r = combined.addResidue(res.name, chain_map[res.chain])
        for atom in res.atoms():
            new_atom = combined.addAtom(atom.name, atom.element, r)
            atom_map_prot[atom] = new_atom

    lig_chain = combined.addChain("L")
    for res in lig_top.residues():
        r = combined.addResidue(res.name, lig_chain)
        for atom in res.atoms():
            new_atom = combined.addAtom(atom.name, atom.element, r)
            atom_map_lig[atom] = new_atom

    for bond in protein_top.bonds():
        combined.addBond(atom_map_prot[bond.atom1], atom_map_prot[bond.atom2])
    for bond in lig_top.bonds():
        combined.addBond(atom_map_lig[bond.atom1], atom_map_lig[bond.atom2])

    return combined, atom_map_prot, atom_map_lig


def create_ligand_system(
    rdkit_mol: Chem.Mol,
    box_vectors: Optional[openmm.Vec3] = None,
) -> Tuple[app.Topology, openmm.System]:
    """Create OpenMM system for ligand with GAFF + RDKit Gasteiger charges."""
    from openmmforcefields.generators import GAFFTemplateGenerator

    mol_off = rdkit_mol_to_openff_with_gasteiger(rdkit_mol)
    gaff = GAFFTemplateGenerator(molecules=mol_off)
    ff = app.ForceField()
    ff.registerTemplateGenerator(gaff.generator)
    top = mol_off.to_topology()
    top_openmm = top.to_openmm()
    if box_vectors is not None:
        top_openmm.setPeriodicBoxVectors(box_vectors)
    sys = ff.createSystem(
        top_openmm,
        nonbondedMethod=app.PME if box_vectors else app.NoCutoff,
        constraints=app.HBonds,
    )
    return top_openmm, sys


def interaction_energy_three_systems(
    sys_complex: openmm.System,
    sys_protein: openmm.System,
    sys_ligand: openmm.System,
    pos_complex: unit.Quantity,
    n_protein_atoms: int,
) -> float:
    """E_inter = E(complex) - E(protein) - E(ligand)."""
    pos_protein = pos_complex[:n_protein_atoms]
    pos_ligand = pos_complex[n_protein_atoms:]

    def _e_sys(system, positions):
        integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
        ctx = openmm.Context(system, integ, openmm.Platform.getPlatformByName("CPU"))
        ctx.setPositions(positions)
        e = ctx.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
        return e

    return _e_sys(sys_complex, pos_complex) - _e_sys(sys_protein, pos_protein) - _e_sys(sys_ligand, pos_ligand)


def interaction_energy_pbc_frame(
    sys_complex: openmm.System,
    sys_protein: openmm.System,
    sys_ligands: openmm.System,
    positions: unit.Quantity,
    box_vectors: Tuple[unit.Quantity, unit.Quantity, unit.Quantity],
    n_protein_atoms: int,
    platform: openmm.Platform,
) -> float:
    """
    E_inter = E(complex) - E(protein) - E(ligands) for a single frame with PBC.
    Uses per-frame box vectors for NPT trajectories where box size changes.
    """
    pos_protein = positions[:n_protein_atoms]
    pos_ligands = positions[n_protein_atoms:]

    def _e_pbc(system, pos):
        integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
        ctx = openmm.Context(system, integ, platform)
        ctx.setPeriodicBoxVectors(box_vectors[0], box_vectors[1], box_vectors[2])
        ctx.setPositions(pos)
        return ctx.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)

    return float(
        _e_pbc(sys_complex, positions) - _e_pbc(sys_protein, pos_protein) - _e_pbc(sys_ligands, pos_ligands)
    )


def run_openmm_relax_and_energy(
    psmiles: str,
    n_repeats: int = 2,
    insulin_pdb_path: Optional[str] = None,
    random_seed: int = 42,
    ligand_offset_nm: Tuple[float, float, float] = (2.0, 0.0, 0.0),
    max_minimize_steps: int = 5000,
    save_complex_pdb: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Insulin (AMBER14SB, SSBOND) + oligomer (GAFF, RDKit Gasteiger) → minimize → interaction energy.

    If ``save_complex_pdb`` is set, writes minimized insulin+oligomer coordinates with
    ``PDBFile.writeFile`` (Angstrom) and returns ``complex_pdb_path`` in the result dict.
    """
    from .polymer_build import ensure_insulin_pdb

    pdb_path = insulin_pdb_path or ensure_insulin_pdb()

    with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False) as f:
        work_pdb = f.name
    try:
        prepare_insulin_ab_pdb(pdb_path, work_pdb)
        modeller = load_insulin_modeller(work_pdb, add_ssbond=True)
    finally:
        Path(work_pdb).unlink(missing_ok=True)

    protein_ff = app.ForceField("amber14-all.xml")  # vacuum; avoids C-term template issues
    modeller.addHydrogens(protein_ff)
    protein_top = modeller.topology
    protein_pos = modeller.positions
    n_protein = protein_top.getNumAtoms()

    capped, _actual = build_polymer_oligomer_smiles(psmiles, n_repeats)
    if not capped:
        return None
    lig_mol = Chem.MolFromSmiles(capped)
    if lig_mol is None:
        return None
    lig_mol = Chem.AddHs(lig_mol)
    ok, _err = embed_mol_3d(lig_mol, random_seed)
    if not ok:
        return None

    lig_top, lig_sys = create_ligand_system(lig_mol, box_vectors=None)
    lig_pos = lig_mol.GetConformer(0).GetPositions()
    lig_pos_omm = unit.Quantity(
        [(x * 0.1, y * 0.1, z * 0.1) for x, y, z in lig_pos],
        unit.nanometers,
    )
    ox, oy, oz = ligand_offset_nm
    lig_pos_offset = unit.Quantity(
        [
            (
                float(lig_pos_omm[i][0].value_in_unit(unit.nanometers)) + ox,
                float(lig_pos_omm[i][1].value_in_unit(unit.nanometers)) + oy,
                float(lig_pos_omm[i][2].value_in_unit(unit.nanometers)) + oz,
            )
            for i in range(len(lig_pos_omm))
        ],
        unit.nanometers,
    )

    combined_top, _, _ = _merge_topologies_with_maps(protein_top, lig_top)
    mol_off = rdkit_mol_to_openff_with_gasteiger(lig_mol)
    from openmmforcefields.generators import GAFFTemplateGenerator

    gaff = GAFFTemplateGenerator(molecules=mol_off)
    protein_ff.registerTemplateGenerator(gaff.generator)
    combined_sys = protein_ff.createSystem(
        combined_top,
        nonbondedMethod=app.NoCutoff,
        constraints=app.HBonds,
    )
    protein_sys = protein_ff.createSystem(
        protein_top,
        nonbondedMethod=app.NoCutoff,
        constraints=app.HBonds,
    )

    combined_pos = unit.Quantity(
        list(protein_pos.value_in_unit(unit.nanometers)) + list(lig_pos_offset.value_in_unit(unit.nanometers)),
        unit.nanometers,
    )

    integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
    platform = openmm.Platform.getPlatformByName("CPU")
    ctx = openmm.Context(combined_sys, integ, platform)
    ctx.setPositions(combined_pos)
    openmm.LocalEnergyMinimizer.minimize(ctx, maxIterations=max_minimize_steps)
    state = ctx.getState(getEnergy=True, getPositions=True)
    e_complex = state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    pos_min = state.getPositions(asNumpy=True)

    e_int = interaction_energy_three_systems(
        combined_sys, protein_sys, lig_sys, pos_min, n_protein
    )
    n_lig = lig_top.getNumAtoms()
    out: Dict[str, Any] = {
        "psmiles": psmiles,
        "method": "OpenMM_minimize_AMBER14SB_GAFF_Gasteiger",
        "potential_energy_complex_kj_mol": float(e_complex),
        "interaction_energy_kj_mol": float(e_int),
        "n_insulin_atoms": n_protein,
        "n_polymer_atoms": n_lig,
        "gromacs_only": False,
    }
    if save_complex_pdb:
        outp = Path(save_complex_pdb).expanduser().resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "w", encoding="utf-8") as fh:
            app.PDBFile.writeFile(combined_top, state.getPositions(), fh)
        out["complex_pdb_path"] = str(outp)
    return out


def _merge_topology_protein_n_ligands(
    protein_top: app.Topology,
    lig_top: app.Topology,
    n_ligands: int,
) -> app.Topology:
    """Merge protein topology with N copies of ligand topology."""
    combined = app.Topology()
    chain_map: Dict = {}
    for chain in protein_top.chains():
        c = combined.addChain(chain.id)
        chain_map[chain] = c
    atom_map_prot: Dict = {}
    for res in protein_top.residues():
        r = combined.addResidue(res.name, chain_map[res.chain])
        for atom in res.atoms():
            new_atom = combined.addAtom(atom.name, atom.element, r)
            atom_map_prot[atom] = new_atom
    for bond in protein_top.bonds():
        combined.addBond(atom_map_prot[bond.atom1], atom_map_prot[bond.atom2])

    for i in range(n_ligands):
        lig_chain = combined.addChain(f"L{i}")
        atom_map_lig: Dict = {}
        for res in lig_top.residues():
            r = combined.addResidue(res.name, lig_chain)
            for atom in res.atoms():
                new_atom = combined.addAtom(atom.name, atom.element, r)
                atom_map_lig[atom] = new_atom
        for bond in lig_top.bonds():
            combined.addBond(atom_map_lig[bond.atom1], atom_map_lig[bond.atom2])
    return combined


def _read_packed_pdb_positions_nm(
    packed_pdb_path: str,
    n_protein: int,
    n_lig_per_chain: int,
    n_chains: int,
) -> Tuple[List, List]:
    """Read packed PDB; return (protein_positions_nm, all_ligand_positions_nm)."""
    from .polymer_build import pdb_atom_coords_angstrom

    _, coords_ang = pdb_atom_coords_angstrom(packed_pdb_path, include_hetatm=True)
    nm = 0.1  # Angstrom to nm
    all_nm = [[c[0] * nm, c[1] * nm, c[2] * nm] for c in coords_ang]
    n_expected = n_protein + n_lig_per_chain * n_chains
    if len(all_nm) < n_expected:
        raise ValueError(
            f"Packed PDB has {len(all_nm)} atoms, expected {n_expected} "
            f"(protein={n_protein}, n_chains={n_chains}, n_lig={n_lig_per_chain})"
        )
    prot_pos = all_nm[:n_protein]
    lig_pos = all_nm[n_protein:n_expected]
    return prot_pos, lig_pos


def _add_shell_restraint_force(
    system: openmm.System,
    n_protein: int,
    combined_pos: unit.Quantity,
    shell_only_angstrom: float,
    box_size_nm: float,
    k_kj_mol_nm2: float = 1000.0,
) -> None:
    """
    Add flat-bottom spherical shell restraint so polymer atoms stay between
    R_inner and R_outer during minimization. Uses CustomExternalForce with
    periodicdistance for PBC.
    """
    pos_nm = np.array(
        [
            [
                float(p[0].value_in_unit(unit.nanometers)),
                float(p[1].value_in_unit(unit.nanometers)),
                float(p[2].value_in_unit(unit.nanometers)),
            ]
            for p in combined_pos
        ]
    )
    com = np.mean(pos_nm[:n_protein], axis=0)
    R_inner_nm = shell_only_angstrom / 10.0
    R_outer_nm = (box_size_nm / 2.0) * 0.92  # buffer from box edge

    # E = k*(step(R_inner-r)*(R_inner-r)^2 + step(r-R_outer)*(r-R_outer)^2)
    energy_expr = (
        "k*(step(R_inner-r)*((R_inner-r)^2) + step(r-R_outer)*((r-R_outer)^2)); "
        "r=periodicdistance(x,y,z,x0,y0,z0)"
    )
    force = openmm.CustomExternalForce(energy_expr)
    force.addGlobalParameter("x0", com[0])
    force.addGlobalParameter("y0", com[1])
    force.addGlobalParameter("z0", com[2])
    force.addGlobalParameter("R_inner", R_inner_nm)
    force.addGlobalParameter("R_outer", R_outer_nm)
    force.addGlobalParameter("k", k_kj_mol_nm2)
    force.setName("ShellRestraint")
    force.setForceGroup(31)  # Isolate for energy subtraction when computing interaction

    n_atoms = system.getNumParticles()
    for i in range(n_protein, n_atoms):
        force.addParticle(i, [])

    system.addForce(force)


def run_openmm_matrix_relax_and_energy(
    psmiles: str,
    n_repeats: int = 4,
    n_polymers: int = 8,
    box_size_nm: Optional[float] = 7.5,
    shell_only_angstrom: float = 14.0,
    insulin_pdb_path: Optional[str] = None,
    random_seed: int = 42,
    max_minimize_steps: int = 2000,
    save_packed_pdb: Optional[str] = None,
    save_minimized_pdb: Optional[str] = None,
    verbose: bool = False,
    target_density_g_cm3: Optional[float] = None,
    packing_mode: str = "bulk",
    restrain_shell: Optional[bool] = None,
    run_npt: bool = True,
    barostat_interval_fs: float = 10.0,
    npt_duration_ps: float = 1.0,
    wall_clock_limit_s: float = 900.0,
    report_interval_steps: int = 250,
    temperature_k: float = 300.0,
    pressure_bar: float = 1.0,
    progressive_pack: bool = False,
    progressive_per_attempt_timeout_s: float = 120.0,
    progressive_max_total_s: Optional[float] = None,
    progressive_n_max: Optional[int] = None,
    density_polymer_n_min: int = 4,
    density_polymer_n_max: int = 100,
) -> Optional[Dict[str, Any]]:
    """
    Insulin + polymer matrix from Packmol, then OpenMM minimize and interaction energy.

    **packing_mode** ``bulk`` (default): polymers throughout the periodic cell (no ``outside sphere``).
    **packing_mode** ``shell``: annulus around insulin (``outside sphere`` in Packmol).

    When target_density_g_cm3 is set, n_polymers (and shell radius in **shell** mode) are
    derived from density; explicit n_polymers / shell_only_angstrom are ignored.

    **box_size_nm:** cubic edge in nm. ``None`` (fixed chain count, no density target) lets
    Packmol auto-size the cell from insulin + polymer extent (see ``packmol_packer``).
    If ``target_density_g_cm3`` is set and ``box_size_nm`` is ``None``, volume for chain
    count uses **7.5** nm.

    **density_polymer_n_max:** Upper clamp for density-derived *n_polymers* (default **100**).
    Lower values with a large box yield sparse visuals; Packmol/OpenMM cost grows with *n*.

    **progressive_pack:** If True, after choosing the initial *n_polymers* (density or fixed),
    repeatedly try *n*+1 chains until Packmol fails/times out or **progressive_*** effort
    limits apply (per-attempt timeout, optional total wall budget, optional *n* cap).

    If ``restrain_shell`` is None, it defaults to True only for **shell** mode (bulk uses
    no spherical shell restraint during minimization unless explicitly enabled with a radius).
    """
    from .packmol_packer import (
        pack_insulin_polymers,
        pack_insulin_polymers_progressive,
        _packmol_available,
    )
    from .polymer_build import ensure_insulin_pdb, mol_to_pdb_block

    def _log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    if packing_mode not in ("shell", "bulk"):
        packing_mode = "bulk"
    if restrain_shell is None:
        restrain_shell = packing_mode == "shell"

    def _fail(error: str, stage: str) -> Dict[str, Any]:
        return {"ok": False, "error": error, "stage": stage, "psmiles": psmiles}

    if not _packmol_available():
        return _fail("packmol not found on PATH", "packmol")

    pdb_path = insulin_pdb_path or ensure_insulin_pdb()

    with tempfile.TemporaryDirectory(prefix="openmm_matrix_") as work:
        work = Path(work)
        prep_pdb = work / "insulin_ab.pdb"
        prepare_insulin_ab_pdb(str(pdb_path), str(prep_pdb))
        modeller = load_insulin_modeller(str(prep_pdb), add_ssbond=True)
        protein_ff = app.ForceField("amber14-all.xml")
        modeller.addHydrogens(protein_ff)
        protein_top = modeller.topology
        protein_pos = modeller.positions
        n_protein = protein_top.getNumAtoms()

        ins_packmol = work / "insulin_packmol.pdb"
        app.PDBFile.writeFile(modeller.topology, modeller.positions, open(ins_packmol, "w"))

        volume_box_nm = float(box_size_nm) if box_size_nm is not None else 7.5

        if target_density_g_cm3 is not None:
            from .matrix_density import suggest_n_polymers_from_density

            n_polymers, shell_only_angstrom = suggest_n_polymers_from_density(
                target_density_g_cm3,
                psmiles,
                n_repeats,
                volume_box_nm,
                shell_inner_angstrom=None,
                insulin_pdb_path=str(ins_packmol),
                packing_mode=packing_mode,
                n_min=density_polymer_n_min,
                n_max=density_polymer_n_max,
            )
            if shell_only_angstrom is not None:
                _log(
                    f"[matrix] density-driven: n_polymers={n_polymers}, shell={shell_only_angstrom:.1f} Å"
                )
            else:
                _log(f"[matrix] density-driven bulk: n_polymers={n_polymers}")

        if packing_mode == "bulk":
            shell_only_angstrom = None

        _stage_heartbeat("oligomer_build", f"building oligomer for {psmiles[:48]}")
        capped, actual_repeats = build_polymer_oligomer_smiles(psmiles, n_repeats)
        if not capped:
            return _fail("build_polymer_oligomer_smiles returned empty (bad PSMILES or n_repeats)", "oligomer_build")
        lig_mol = Chem.MolFromSmiles(capped)
        if lig_mol is None:
            return _fail(f"RDKit MolFromSmiles failed for capped SMILES: {capped[:120]}", "oligomer_build")
        lig_mol = Chem.AddHs(lig_mol)
        embed_ok, embed_err = embed_mol_3d(lig_mol, random_seed)
        if not embed_ok:
            return _fail(f"3D embedding failed: {embed_err}", "embed")

        poly_pdb = work / "polymer.pdb"
        Path(poly_pdb).write_text(mol_to_pdb_block(lig_mol))

        packed_pdb = work / "packed.pdb"
        pack_box_nm: Optional[float] = (
            volume_box_nm if target_density_g_cm3 is not None else box_size_nm
        )
        if shell_only_angstrom is not None:
            _log(f"[matrix] Packmol: insulin + {n_polymers} chains, shell R={shell_only_angstrom} Å")
        else:
            _log(f"[matrix] Packmol: insulin + {n_polymers} chains, bulk (full cell)")
        pack_common_kw = dict(
            box_size_nm=pack_box_nm,
            tolerance_angstrom=2.0,
            seed=random_seed,
            shell_only_angstrom=shell_only_angstrom,
            packing_mode=packing_mode,
        )
        _stage_heartbeat("packmol", f"packing insulin + {n_polymers} polymer chain(s)")
        if progressive_pack:
            _log(
                f"[matrix] Progressive pack: start={n_polymers}, "
                f"per-attempt timeout={progressive_per_attempt_timeout_s}s, "
                f"max_total_s={progressive_max_total_s}, n_max={progressive_n_max}"
            )
            pack_result = pack_insulin_polymers_progressive(
                str(ins_packmol),
                str(poly_pdb),
                n_polymers,
                str(packed_pdb),
                n_polymers_cap=progressive_n_max,
                per_attempt_timeout_s=progressive_per_attempt_timeout_s,
                max_total_seconds=progressive_max_total_s,
                seed=random_seed,
                **pack_common_kw,
            )
        else:
            pack_result = pack_insulin_polymers(
                str(ins_packmol),
                str(poly_pdb),
                n_polymers,
                str(packed_pdb),
                timeout_s=300,
                **pack_common_kw,
            )
        if not pack_result.get("success"):
            pack_err = pack_result.get("stderr", "unknown reason")
            return _fail(f"Packmol packing failed: {str(pack_err)[:300]}", "packmol")
        if progressive_pack:
            n_polymers = int(pack_result["n_polymers"])
            if verbose:
                _log(
                    f"[matrix] Progressive pack done: n={n_polymers}, "
                    f"reason={pack_result.get('stopped_reason')}, "
                    f"attempts={pack_result.get('attempts')}, "
                    f"pack_wall_s={pack_result.get('total_pack_seconds', 0):.2f}"
                )
        effective_box_nm = float(pack_result["box_edge_nm"])

        if save_packed_pdb:
            import shutil
            Path(save_packed_pdb).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(packed_pdb), save_packed_pdb)
            _log(f"[matrix] Saved packed structure to {save_packed_pdb}")

        n_lig = lig_mol.GetNumAtoms()
        prot_pos_nm, lig_pos_nm = _read_packed_pdb_positions_nm(
            str(packed_pdb), n_protein, n_lig, n_polymers
        )
        # Packmol output: coordinates in [0, L] nm (insulin centered at L/2); OpenMM PBC matches
        combined_pos = unit.Quantity(
            [[p[0], p[1], p[2]] for p in prot_pos_nm + lig_pos_nm],
            unit.nanometers,
        )

        box_vectors = (
            [effective_box_nm, 0, 0],
            [0, effective_box_nm, 0],
            [0, 0, effective_box_nm],
        )
        box_vec_omm = [unit.Quantity(v, unit.nanometers) for v in box_vectors]

        _stage_heartbeat("openmm_system_build", "creating combined protein+polymer OpenMM system")
        lig_top, lig_sys = create_ligand_system(lig_mol, box_vectors=None)
        combined_top = _merge_topology_protein_n_ligands(protein_top, lig_top, n_polymers)
        combined_top.setPeriodicBoxVectors(box_vec_omm)
        mol_off = rdkit_mol_to_openff_with_gasteiger(lig_mol)
        from openmmforcefields.generators import GAFFTemplateGenerator

        gaff = GAFFTemplateGenerator(molecules=mol_off)
        protein_ff.registerTemplateGenerator(gaff.generator)
        combined_sys = protein_ff.createSystem(
            combined_top,
            nonbondedMethod=app.PME,
            nonbondedCutoff=1.0 * unit.nanometers,
            constraints=app.HBonds,
        )

        # Optional: flat-bottom spherical shell restraint on polymer atoms during minimization
        applied_shell_restraint = False
        if restrain_shell and shell_only_angstrom is not None:
            _add_shell_restraint_force(
                combined_sys,
                n_protein,
                combined_pos,
                shell_only_angstrom,
                effective_box_nm,
                k_kj_mol_nm2=1000.0,
            )
            applied_shell_restraint = True
            _log(
                f"[matrix] Shell restraint: R_in={shell_only_angstrom/10:.2f} nm, "
                f"R_out={(effective_box_nm/2)*0.92:.2f} nm"
            )
        elif restrain_shell and shell_only_angstrom is None:
            _log("[matrix] Shell restraint skipped (bulk packing or no shell radius)")

        protein_top.setPeriodicBoxVectors(box_vec_omm)
        protein_sys = protein_ff.createSystem(
            protein_top,
            nonbondedMethod=app.PME,
            nonbondedCutoff=1.0 * unit.nanometers,
            constraints=app.HBonds,
        )
        ligands_only_top = _merge_topology_protein_n_ligands(
            app.Topology(), lig_top, n_polymers
        )
        ligands_only_top.setPeriodicBoxVectors(box_vec_omm)
        ligands_ff = app.ForceField()
        gaff2 = GAFFTemplateGenerator(molecules=mol_off)
        ligands_ff.registerTemplateGenerator(gaff2.generator)
        ligands_sys = ligands_ff.createSystem(
            ligands_only_top,
            nonbondedMethod=app.PME,
            nonbondedCutoff=1.0 * unit.nanometers,
            constraints=app.HBonds,
        )

        integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
        platform = openmm.Platform.getPlatformByName("CPU")
        ctx = openmm.Context(combined_sys, integ, platform)
        ctx.setPeriodicBoxVectors(box_vec_omm[0], box_vec_omm[1], box_vec_omm[2])
        ctx.setPositions(combined_pos)
        _stage_heartbeat("minimize", f"LocalEnergyMinimizer maxIterations={max_minimize_steps}")
        _log("[matrix] Minimizing ...")
        openmm.LocalEnergyMinimizer.minimize(ctx, maxIterations=max_minimize_steps)
        state = ctx.getState(getEnergy=True, getPositions=True)
        e_complex_total = state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
        pos_min = state.getPositions(asNumpy=True)
        # Exclude shell restraint from complex energy for interaction-energy decomposition
        if applied_shell_restraint:
            e_restraint = ctx.getState(getEnergy=True, groups={31}).getPotentialEnergy().value_in_unit(
                unit.kilojoules_per_mole
            )
            e_complex = float(e_complex_total) - float(e_restraint)
        else:
            e_complex = float(e_complex_total)

        if save_minimized_pdb:
            Path(save_minimized_pdb).parent.mkdir(parents=True, exist_ok=True)
            # Bond-aware PBC unwrap + center protein for PyMOL (avoids spurious long sticks)
            pos_nm_viz = prepare_matrix_complex_pdb_positions_nm(
                pos_min, combined_top, n_protein, effective_box_nm
            )
            pos_to_write = unit.Quantity(pos_nm_viz, unit.nanometers)
            # Use plain-float Vec3 for CRYST1 (Quantity causes TypeError in writeHeader)
            L_nm = float(effective_box_nm)
            combined_top.setUnitCellDimensions(openmm.Vec3(L_nm, L_nm, L_nm))
            with open(save_minimized_pdb, "w") as f:
                app.PDBFile.writeFile(combined_top, pos_to_write, f)
            _log(f"[matrix] Saved minimized structure to {save_minimized_pdb}")

        pos_prot = pos_min[:n_protein]
        pos_ligs = pos_min[n_protein:]

        def _e(system, positions, box_vec=None):
            box = box_vec or box_vec_omm
            i = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
            c = openmm.Context(system, i, platform)
            c.setPeriodicBoxVectors(box[0], box[1], box[2])
            c.setPositions(positions)
            return c.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)

        e_int: float
        e_int_std: Optional[float] = None
        n_frames_averaged: Optional[int] = None

        _stage_heartbeat("energy_eval", "computing interaction energy")
        if run_npt:
            # Build NPT system (no shell restraint); barostat every 10 fs
            combined_sys_npt = protein_ff.createSystem(
                combined_top,
                nonbondedMethod=app.PME,
                nonbondedCutoff=1.0 * unit.nanometers,
                constraints=app.HBonds,
            )
            dt_ps = 0.002
            barostat_freq = max(1, int(barostat_interval_fs / 2))
            combined_sys_npt.addForce(
                openmm.MonteCarloBarostat(
                    pressure_bar * unit.bar,
                    temperature_k * unit.kelvin,
                    barostat_freq,
                )
            )
            npt_steps = int(npt_duration_ps / dt_ps)
            integ_npt = openmm.LangevinIntegrator(
                temperature_k * unit.kelvin,
                1 / unit.picosecond,
                dt_ps * unit.picoseconds,
            )
            ctx_npt = openmm.Context(combined_sys_npt, integ_npt, platform)
            ctx_npt.setPeriodicBoxVectors(box_vec_omm[0], box_vec_omm[1], box_vec_omm[2])
            ctx_npt.setPositions(pos_min)
            _log(f"[matrix] NPT: {npt_duration_ps} ps, barostat every {barostat_interval_fs} fs, wall-clock limit {wall_clock_limit_s}s")
            t_start = time.perf_counter()
            e_int_list: List[float] = []
            total_steps = 0
            while total_steps < npt_steps:
                if time.perf_counter() - t_start > wall_clock_limit_s:
                    break
                chunk = min(report_interval_steps, npt_steps - total_steps)
                integ_npt.step(chunk)
                total_steps += chunk
                state = ctx_npt.getState(getEnergy=True, getPositions=True)
                pos_frame = state.getPositions(asNumpy=True)
                # State includes box vectors for periodic systems (no getPeriodicBoxVectors kw in getState)
                box_frame = state.getPeriodicBoxVectors()
                e_int_frame = interaction_energy_pbc_frame(
                    combined_sys_npt,
                    protein_sys,
                    ligands_sys,
                    pos_frame,
                    box_frame,
                    n_protein,
                    platform,
                )
                e_int_list.append(float(e_int_frame))
            if e_int_list:
                e_int = float(np.mean(e_int_list))
                e_int_std = float(np.std(e_int_list)) if len(e_int_list) > 1 else None
                n_frames_averaged = len(e_int_list)
                _log(f"[matrix] NPT complete: {total_steps} steps, {n_frames_averaged} frames, E_int mean={e_int:.3f} kJ/mol")
            else:
                e_prot = _e(protein_sys, pos_prot)
                e_ligs = _e(ligands_sys, pos_ligs)
                e_int = float(e_complex) - float(e_prot) - float(e_ligs)
                _log("[matrix] NPT ran 0 steps; using single-point E_int")
        else:
            e_prot = _e(protein_sys, pos_prot)
            e_ligs = _e(ligands_sys, pos_ligs)
            e_int = float(e_complex) - float(e_prot) - float(e_ligs)

        method_name = (
            "OpenMM_matrix_bulk_AMBER14SB_GAFF_Gasteiger"
            if packing_mode == "bulk"
            else "OpenMM_matrix_encapsulated_AMBER14SB_GAFF_Gasteiger"
        )
        out: Dict[str, Any] = {
            "psmiles": psmiles,
            "method": method_name,
            "packing_mode": packing_mode,
            "potential_energy_complex_kj_mol": float(e_complex),
            "interaction_energy_kj_mol": float(e_int),
            "n_insulin_atoms": n_protein,
            "n_polymer_chains": n_polymers,
            "n_polymer_atoms_per_chain": n_lig,
            "shell_angstrom": shell_only_angstrom,
            "box_nm": effective_box_nm,
            "gromacs_only": False,
        }
        if progressive_pack:
            out["packmol_progressive"] = {
                "enabled": True,
                "n_polymers_start": pack_result.get("n_polymers_start"),
                "stopped_reason": pack_result.get("stopped_reason"),
                "attempts": pack_result.get("attempts"),
                "total_pack_seconds": pack_result.get("total_pack_seconds"),
                "per_attempt_timeout_s": progressive_per_attempt_timeout_s,
                "max_total_s": progressive_max_total_s,
                "n_max": progressive_n_max,
            }
        if save_minimized_pdb:
            out["minimized_pdb"] = save_minimized_pdb
        if e_int_std is not None:
            out["interaction_energy_kj_mol_std"] = e_int_std
        if n_frames_averaged is not None:
            out["n_frames_averaged"] = n_frames_averaged
        return out
