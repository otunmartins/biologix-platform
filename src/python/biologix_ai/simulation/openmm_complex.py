#!/usr/bin/env python3
"""
OpenMM geometry relaxation and interaction energy (no acpype/antechamber).

- Protein target: AMBER14SB, disulfide bonds from SSBOND.
- Ligand: GAFF via openmmforcefields, charges from RDKit Gasteiger.
"""

from __future__ import annotations

import json
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


PROGRESS_FILE_ENV = "BIOLOGIX_AI_PROGRESS_FILE"


def _append_progress_file(stage: str, msg: str) -> None:
    """Append ``stage<TAB>message`` to the file the parent process is tailing.

    A candidate runs in a worker process (a forked pool child on CPU, a fresh
    interpreter on GPU), and ``_STAGE_HEARTBEAT_HOOK`` lives only in the parent,
    so nothing the worker reports reached a check-back on the running job. A file
    is the one channel that both start methods share. Failures are swallowed:
    progress is a courtesy and must never fail a simulation.
    """
    path = os.environ.get(PROGRESS_FILE_ENV, "").strip()
    if not path:
        return
    try:
        clean = " ".join(str(msg).split())
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stage}\t{clean}\n")
    except OSError:
        pass


def _packing_failure_text(pack_result: Dict[str, Any]) -> str:
    """Why Packmol failed, in its own words: stderr, else the last lines of what it printed."""
    text = str(pack_result.get("stderr") or "").strip()
    if not text:
        tail = [ln.strip() for ln in str(pack_result.get("stdout") or "").splitlines() if ln.strip()]
        text = " | ".join(tail[-3:])
    return (text or "unknown reason")[:300]


def _protein_label(protein_pdb_path: Optional[str]) -> str:
    """Name of the simulated protein for progress text: "insulin" only for the bundled default.

    A resolved biologic's ``biologic_target.json`` sits beside its PDB.
    """
    if not protein_pdb_path:
        return "insulin"
    try:
        info = json.loads((Path(protein_pdb_path).parent / "biologic_target.json").read_text())
        name = str(info.get("canonical_name") or info.get("resolved_target") or "").strip()
    except (OSError, ValueError):
        name = ""
    return name or "the target protein"


def _stage_heartbeat(stage: str, msg: str) -> None:
    """Emit stage progress to stderr unless the user opted out via BIOLOGIX_AI_EVAL_QUIET."""
    # Before the quiet check: silencing stderr must not silence the job's progress.
    _append_progress_file(stage, msg)
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


def seeded_langevin(temperature_k: float, friction_per_ps: float, dt_ps: float, seed: int):
    """Langevin integrator whose random stream is fixed by *seed*.

    OpenMM draws a fresh seed when none is set, so an unseeded NPT leg is not
    comparable across runs of the same candidate.

    Parameters
    ----------
    temperature_k : float
        Temperature in kelvin.
    friction_per_ps : float
        Collision frequency in 1/ps.
    dt_ps : float
        Timestep in picoseconds.
    seed : int
        Random seed. The same seed reproduces the same trajectory.
    """
    import openmm  # noqa: PLC0415 — optional dependency, same as the rest of this module
    import openmm.unit as unit  # noqa: PLC0415

    integrator = openmm.LangevinIntegrator(
        float(temperature_k) * unit.kelvin,
        float(friction_per_ps) / unit.picosecond,
        float(dt_ps) * unit.picoseconds,
    )
    integrator.setRandomNumberSeed(int(seed))
    return integrator


def seeded_barostat(pressure_bar: float, temperature_k: float, frequency: int, seed: int):
    """Monte Carlo barostat whose volume moves are fixed by *seed*.

    Parameters
    ----------
    pressure_bar : float
        Pressure in bar.
    temperature_k : float
        Temperature in kelvin.
    frequency : int
        Steps between barostat attempts.
    seed : int
        Random seed, the same value passed to :func:`seeded_langevin`.
    """
    import openmm  # noqa: PLC0415 — optional dependency, same as the rest of this module
    import openmm.unit as unit  # noqa: PLC0415

    barostat = openmm.MonteCarloBarostat(
        float(pressure_bar) * unit.bar,
        float(temperature_k) * unit.kelvin,
        int(frequency),
    )
    barostat.setRandomNumberSeed(int(seed))
    return barostat


def _cpu_platform():
    """Return the OpenMM CPU platform with a fixed thread count.

    Under Rosetta 2 (linux/amd64 Docker on Apple Silicon), sched_getaffinity
    returns inconsistent values across process boundaries.  OpenMM's CPU
    platform probes that syscall to size its FFTW thread pool; when the count
    is wrong the pool deadlocks on initialisation — producing the random hang
    seen at session start or mid-simulation.

    OPENMM_CPU_THREADS=1 (set in docker-compose.yml) is the primary guard.
    This helper applies the same constraint in-process so subprocess invocations
    that may not inherit the env var are also protected.
    """
    import openmm  # noqa: PLC0415 — lazy to avoid import at module level before optional dep check

    n_threads = os.environ.get("OPENMM_CPU_THREADS", "1")
    platform = openmm.Platform.getPlatformByName("CPU")
    platform.setPropertyDefaultValue("Threads", n_threads)
    return platform


# OpenMM Modeller.addHydrogens default. Resolution and the matrix run must pass the
# same value; histidine protonation follows this pH, not a predicted pKa.
PROTONATION_PH = 7.0

PLATFORM_ENV = "BIOLOGIX_AI_OPENMM_PLATFORM"
_GPU_PLATFORMS = ("CUDA", "OpenCL")
_PLATFORM_CACHE: Dict[str, Tuple[Any, Dict[str, str]]] = {}


class OpenMMPlatformError(RuntimeError):
    """An explicitly requested OpenMM platform cannot create a Context."""


def _probe_platform(name: str) -> Tuple[Optional[Any], str]:
    """Return (platform, "") when *name* can create a Context here, else (None, reason).

    Loading the CUDA plugin succeeds on machines without a GPU; only creating a
    Context proves the device and driver work.
    """
    import openmm  # noqa: PLC0415

    try:
        platform = openmm.Platform.getPlatformByName(name)
    except Exception as exc:  # OpenMMException when the plugin is not loaded
        return None, f"{name} plugin not loaded: {exc}"
    if name in _GPU_PLATFORMS:
        platform.setPropertyDefaultValue("Precision", "mixed")
    try:
        system = openmm.System()
        system.addParticle(1.0)
        openmm.Context(system, openmm.VerletIntegrator(0.001), platform)
    except Exception as exc:
        return None, f"{name} cannot create a Context: {exc}"
    return platform, ""


def select_openmm_platform(requested: Optional[str] = None) -> Tuple[Any, Dict[str, str]]:
    """Return the OpenMM platform for this process and a description for results.

    ``BIOLOGIX_AI_OPENMM_PLATFORM`` is ``CPU`` (default), ``CUDA``, ``OpenCL`` or
    ``auto`` (*requested* overrides the variable for one run). ``auto`` tries CUDA,
    then OpenCL, then CPU. An explicit GPU platform
    that cannot create a Context raises :class:`OpenMMPlatformError`; it never
    falls back silently, so a GPU run is never reported as one that ran on CPU.
    """
    requested = (requested or os.environ.get(PLATFORM_ENV, "CPU")).strip() or "CPU"
    key = requested.lower()
    if key in _PLATFORM_CACHE:
        return _PLATFORM_CACHE[key]
    if key == "cpu":
        chosen: Tuple[Any, Dict[str, str]] = (
            _cpu_platform(),
            {"requested": "CPU", "name": "CPU", "precision": "mixed",
             "threads": os.environ.get("OPENMM_CPU_THREADS", "1")},
        )
    elif key == "auto":
        chosen = (
            _cpu_platform(),
            {"requested": "auto", "name": "CPU", "precision": "mixed",
             "threads": os.environ.get("OPENMM_CPU_THREADS", "1")},
        )
        reasons: List[str] = []
        for name in _GPU_PLATFORMS:
            platform, reason = _probe_platform(name)
            if platform is not None:
                chosen = (platform, {"requested": "auto", "name": name, "precision": "mixed"})
                break
            reasons.append(reason)
        if chosen[1]["name"] == "CPU" and reasons:
            chosen[1]["fallback_reason"] = "; ".join(reasons)
    else:
        name = {"cuda": "CUDA", "opencl": "OpenCL"}.get(key)
        if name is None:
            raise OpenMMPlatformError(
                f"{PLATFORM_ENV}={requested!r} is not one of CPU, CUDA, OpenCL, auto"
            )
        platform, reason = _probe_platform(name)
        if platform is None:
            raise OpenMMPlatformError(reason)
        chosen = (platform, {"requested": name, "name": name, "precision": "mixed"})
    _PLATFORM_CACHE[key] = chosen
    return chosen


def _openmm_platform():
    """Platform used for every Context in this module (see :func:`select_openmm_platform`)."""
    return select_openmm_platform()[0]


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

from .openmm_protein import (
    prepare_protein_pdb,
    add_disulfide_bonds_from_ssbond,
    load_protein_modeller,
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


# prepare_protein_pdb imported from openmm_protein

DEFAULT_INSULIN_CHAINS: Tuple[str, ...] = ("A", "B")


def target_protein_chains(
    pdb_path: Optional[str],
    protein_chains: Optional[Tuple[str, ...]] = None,
) -> Optional[Tuple[str, ...]]:
    """Chains of the target protein to keep before building the OpenMM system.

    The bundled insulin file (4F1C, chains A-D) keeps its A+B heterodimer as it
    always has. Any other target was prepared by ``biologic_resolver`` and already
    holds exactly the chains to simulate, so every chain is kept (``None``).
    """
    if protein_chains:
        return tuple(protein_chains)
    if pdb_path is None:
        return DEFAULT_INSULIN_CHAINS
    from .polymer_build import INSULIN_PDB_PATH

    try:
        if Path(pdb_path).resolve() == Path(INSULIN_PDB_PATH).resolve():
            return DEFAULT_INSULIN_CHAINS
    except OSError:
        pass
    return None



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
    platform = _openmm_platform()
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
        ctx = openmm.Context(system, integ, _openmm_platform())
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
    protein_pdb_path: Optional[str] = None,
    random_seed: int = 42,
    ligand_offset_nm: Tuple[float, float, float] = (2.0, 0.0, 0.0),
    max_minimize_steps: int = 5000,
    save_complex_pdb: Optional[str] = None,
    protein_chains: Optional[Tuple[str, ...]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Protein (AMBER14SB, SSBOND) + oligomer (GAFF, RDKit Gasteiger) → minimize → interaction energy.

    If ``save_complex_pdb`` is set, writes minimized protein+oligomer coordinates with
    ``PDBFile.writeFile`` (Angstrom) and returns ``complex_pdb_path`` in the result dict.
    """
    from .polymer_build import ensure_default_target_pdb

    chains = target_protein_chains(protein_pdb_path, protein_chains)
    pdb_path = protein_pdb_path or ensure_default_target_pdb()

    with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False) as f:
        work_pdb = f.name
    try:
        prepare_protein_pdb(pdb_path, work_pdb, chains=chains)
        modeller = load_protein_modeller(work_pdb, add_ssbond=True)
    finally:
        Path(work_pdb).unlink(missing_ok=True)

    protein_ff = app.ForceField("amber14-all.xml")  # vacuum; avoids C-term template issues
    modeller.addHydrogens(protein_ff, pH=PROTONATION_PH)
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
    platform = _openmm_platform()
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
        "n_protein_atoms": n_protein,
        "n_polymer_atoms": n_lig,
        "gromacs_only": False,
        "openmm_platform": dict(select_openmm_platform()[1]),
        "random_seed": int(random_seed),
        "protonation_ph": PROTONATION_PH,
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
    n_extra_atoms: int = 0,
) -> Tuple[List, List]:
    """Read packed PDB; return (protein_positions_nm, matrix_positions_nm).

    *n_extra_atoms* counts single-atom counterions packed after the chains; they
    trail the polymer positions, matching the topology built for the matrix.
    """
    from .polymer_build import pdb_atom_coords_angstrom

    _, coords_ang = pdb_atom_coords_angstrom(packed_pdb_path, include_hetatm=True)
    nm = 0.1  # Angstrom to nm
    all_nm = [[c[0] * nm, c[1] * nm, c[2] * nm] for c in coords_ang]
    n_expected = n_protein + n_lig_per_chain * n_chains + n_extra_atoms
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
    protein_pdb_path: Optional[str] = None,
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
    protein_chains: Optional[Tuple[str, ...]] = None,
    openmm_platform: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Protein + polymer matrix from Packmol, then OpenMM minimize and interaction energy.

    **packing_mode** ``bulk`` (default): polymers throughout the periodic cell (no ``outside sphere``).
    **packing_mode** ``shell``: annulus around the protein (``outside sphere`` in Packmol).

    When target_density_g_cm3 is set, n_polymers (and shell radius in **shell** mode) are
    derived from density; explicit n_polymers / shell_only_angstrom are ignored.

    **box_size_nm:** cubic edge in nm. ``None`` (fixed chain count, no density target) lets
    Packmol auto-size the cell from protein + polymer extent (see ``packmol_packer``).
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
        pack_protein_polymers,
        pack_protein_polymers_progressive,
        _packmol_available,
    )
    from .polymer_build import ensure_default_target_pdb, mol_to_pdb_block

    def _log(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr, flush=True)

    if packing_mode not in ("shell", "bulk"):
        packing_mode = "bulk"
    if restrain_shell is None:
        restrain_shell = packing_mode == "shell"

    def _fail(error: str, stage: str) -> Dict[str, Any]:
        return {"ok": False, "error": error, "stage": stage, "psmiles": psmiles}

    if not _packmol_available():
        return _fail("packmol not found on PATH", "packmol")

    chains = target_protein_chains(protein_pdb_path, protein_chains)
    pdb_path = protein_pdb_path or ensure_default_target_pdb()
    protein_label = _protein_label(protein_pdb_path)
    try:
        platform, platform_info = select_openmm_platform(openmm_platform)
    except OpenMMPlatformError as exc:
        return _fail(f"OpenMM platform unavailable: {exc}", "openmm_platform")

    with tempfile.TemporaryDirectory(prefix="openmm_matrix_") as work:
        work = Path(work)
        prep_pdb = work / "protein_prepared.pdb"
        prepare_protein_pdb(str(pdb_path), str(prep_pdb), chains=chains)
        modeller = load_protein_modeller(str(prep_pdb), add_ssbond=True)
        # The ion parameters live in the water-model file. Load it only for a
        # charged matrix: a neutral run then builds the identical force field it
        # always did, down to the summation order of its energies.
        repeat_charge = repeat_unit_charge(psmiles)
        ff_files = ["amber14-all.xml"] + ([ION_FORCEFIELD] if repeat_charge else [])
        protein_ff = app.ForceField(*ff_files)
        modeller.addHydrogens(protein_ff, pH=PROTONATION_PH)
        protein_top = modeller.topology
        protein_pos = modeller.positions
        n_protein = protein_top.getNumAtoms()

        ins_packmol = work / "protein_packmol.pdb"
        app.PDBFile.writeFile(modeller.topology, modeller.positions, open(ins_packmol, "w"))

        from .packmol_packer import protein_box_floor_nm

        box_floor_nm = protein_box_floor_nm(str(ins_packmol))
        box_requested_nm = box_size_nm
        if box_size_nm is not None and float(box_size_nm) < box_floor_nm:
            _stage_heartbeat(
                "packmol",
                f"box {float(box_size_nm):.2f} nm is smaller than the protein extent; "
                f"using {box_floor_nm:.2f} nm",
            )
            box_size_nm = box_floor_nm
        volume_box_nm = float(box_size_nm) if box_size_nm is not None else max(7.5, box_floor_nm)

        if target_density_g_cm3 is not None:
            from .matrix_density import suggest_n_polymers_from_density

            n_polymers, shell_only_angstrom = suggest_n_polymers_from_density(
                target_density_g_cm3,
                psmiles,
                n_repeats,
                volume_box_nm,
                shell_inner_angstrom=None,
                protein_pdb_path=str(ins_packmol),
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

        chain_charge = Chem.GetFormalCharge(lig_mol)
        ion_resname, ion_element, n_ions = counterions_for_matrix(chain_charge, n_polymers)
        if n_ions and not repeat_charge:
            return _fail(
                f"The repeat unit reads as neutral but the built chain carries {chain_charge:+d}; "
                "the ion parameters were not loaded. Rewrite the repeat unit so its charge is "
                "explicit.",
                "oligomer_build",
            )
        if n_ions and not neutralization_enabled():
            return _fail(
                f"Each chain carries a net charge of {chain_charge:+d} and "
                f"{NEUTRALIZE_ENV} disables counterions, so the PME cell would carry a "
                "neutralising background. Enable neutralisation or use a neutral repeat unit.",
                "prescreen",
            )
        extra_species: List[Tuple[str, int]] = []
        if n_ions:
            if progressive_pack:
                # The ion count is fixed to the chain count, so growing the chain
                # count mid-pack would leave the matrix charged.
                _log("[matrix] charged matrix: progressive packing disabled")
                progressive_pack = False
            ion_pdb = work / "counterion.pdb"
            write_ion_pdb(str(ion_pdb), ion_resname, ion_element)
            extra_species = [(str(ion_pdb), n_ions)]
            _stage_heartbeat(
                "packmol",
                f"neutralising {n_polymers} chain(s) of charge {chain_charge:+d} "
                f"with {n_ions} {ion_resname} ion(s)",
            )

        packed_pdb = work / "packed.pdb"
        pack_box_nm: Optional[float] = (
            volume_box_nm if target_density_g_cm3 is not None else box_size_nm
        )
        if shell_only_angstrom is not None:
            _log(f"[matrix] Packmol: {protein_label} + {n_polymers} chains, shell R={shell_only_angstrom} Å")
        else:
            _log(f"[matrix] Packmol: {protein_label} + {n_polymers} chains, bulk (full cell)")
        packmol_retry: Optional[Dict[str, Any]] = None
        pack_common_kw = dict(
            box_size_nm=pack_box_nm,
            tolerance_angstrom=2.0,
            seed=random_seed,
            shell_only_angstrom=shell_only_angstrom,
            packing_mode=packing_mode,
            extra_species=extra_species,
        )
        _stage_heartbeat("packmol", f"packing {protein_label} + {n_polymers} polymer chain(s)")
        if progressive_pack:
            _log(
                f"[matrix] Progressive pack: start={n_polymers}, "
                f"per-attempt timeout={progressive_per_attempt_timeout_s}s, "
                f"max_total_s={progressive_max_total_s}, n_max={progressive_n_max}"
            )
            pack_result = pack_protein_polymers_progressive(
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
            def _packing_wait(seconds: float) -> None:
                _stage_heartbeat(
                    "packmol",
                    f"packing {protein_label} + {n_polymers} polymer chain(s), {int(seconds)} s so far",
                )

            # No wall-clock limit: Packmol runs until it finishes or reports a failure.
            pack_result = pack_protein_polymers(
                str(ins_packmol),
                str(poly_pdb),
                n_polymers,
                str(packed_pdb),
                on_wait=_packing_wait,
                **pack_common_kw,
            )
            first_edge_nm = pack_result.get("box_edge_nm") or pack_box_nm
            if not pack_result.get("success") and first_edge_nm:
                retry_box_nm = round(float(first_edge_nm) * 1.15, 3)
                _stage_heartbeat(
                    "packmol",
                    f"Packmol reported it did not converge; retrying once with a 15% larger box ({retry_box_nm} nm)",
                )
                packmol_retry = {
                    "first_box_nm": float(first_edge_nm),
                    "retry_box_nm": retry_box_nm,
                    "first_error": str(pack_result.get("stderr", ""))[:300],
                }
                pack_result = pack_protein_polymers(
                    str(ins_packmol),
                    str(poly_pdb),
                    n_polymers,
                    str(packed_pdb),
                    on_wait=_packing_wait,
                    **{**pack_common_kw, "box_size_nm": retry_box_nm},
                )
        if not pack_result.get("success"):
            return _fail(f"Packmol packing failed: {_packing_failure_text(pack_result)}", "packmol")
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
            str(packed_pdb), n_protein, n_lig, n_polymers, n_extra_atoms=n_ions
        )
        # Packmol output: coordinates in [0, L] nm (protein centered at L/2); OpenMM PBC matches
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
        add_ions_to_topology(combined_top, ion_resname, ion_element, n_ions)
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
        # The counterions belong to the matrix subsystem, so it is neutral and
        # its PME energy carries no neutralising-background artefact.
        ligands_only_top = _merge_topology_protein_n_ligands(
            app.Topology(), lig_top, n_polymers
        )
        add_ions_to_topology(ligands_only_top, ion_resname, ion_element, n_ions)
        ligands_only_top.setPeriodicBoxVectors(box_vec_omm)
        ligands_ff = app.ForceField(ION_FORCEFIELD) if n_ions else app.ForceField()
        gaff2 = GAFFTemplateGenerator(molecules=mol_off)
        ligands_ff.registerTemplateGenerator(gaff2.generator)
        ligands_sys = ligands_ff.createSystem(
            ligands_only_top,
            nonbondedMethod=app.PME,
            nonbondedCutoff=1.0 * unit.nanometers,
            constraints=app.HBonds,
        )

        integ = openmm.LangevinIntegrator(300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds)
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
                seeded_barostat(pressure_bar, temperature_k, barostat_freq, random_seed)
            )
            npt_steps = int(npt_duration_ps / dt_ps)
            integ_npt = seeded_langevin(temperature_k, 1.0, dt_ps, random_seed)
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
            "n_protein_atoms": n_protein,
            "n_polymer_chains": n_polymers,
            "n_polymer_atoms_per_chain": n_lig,
            "shell_angstrom": shell_only_angstrom,
            "box_nm": effective_box_nm,
            "gromacs_only": False,
            "openmm_platform": dict(platform_info),
            "n_protein_chains": len(list(protein_top.chains())),
            "random_seed": int(random_seed),
            "protonation_ph": PROTONATION_PH,
            "polymer_chain_charge": int(chain_charge),
        }
        if n_ions:
            out["counterions"] = {
                "residue": ion_resname,
                "count": n_ions,
                "neutralises": "polymer matrix",
                "note": (
                    "The matrix subsystem is neutral. The protein keeps its own net charge, "
                    "as in every run."
                ),
            }
        if box_requested_nm is not None and float(box_requested_nm) < box_floor_nm:
            out["box_enlarged"] = {
                "requested_nm": float(box_requested_nm),
                "used_nm": float(box_floor_nm),
                "reason": "protein extent plus padding exceeds the requested box edge",
            }
        if packmol_retry is not None:
            out["packmol_retry"] = packmol_retry
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


# Counterions for a polyelectrolyte matrix. AMBER14 monovalent ion parameters
# ship with the water model file, not with amber14-all.xml.
ION_FORCEFIELD = "amber14/tip3p.xml"
NEUTRALIZE_ENV = "BIOLOGIX_AI_OPENMM_NEUTRALIZE"
_ION_FOR_NEGATIVE = ("NA", "Na", 1)   # a negative matrix needs cations
_ION_FOR_POSITIVE = ("CL", "Cl", -1)  # a positive matrix needs anions


def repeat_unit_charge(psmiles: str) -> int:
    """Net formal charge of one repeat unit, read before the oligomer is built."""
    mol = Chem.MolFromSmiles(str(psmiles).strip().replace("[*]", "[H]"))
    return 0 if mol is None else Chem.GetFormalCharge(mol)


def neutralization_enabled() -> bool:
    """Whether a charged polymer matrix gets counterions (default: yes)."""
    return os.environ.get(NEUTRALIZE_ENV, "yes").strip().lower() not in ("0", "no", "false")


def counterions_for_matrix(polymer_charge: int, n_polymers: int) -> Tuple[str, str, int]:
    """``(residue name, element, count)`` neutralising *n_polymers* chains.

    The ions neutralise the polymer matrix only. The protein keeps its own net
    charge, exactly as in every neutral-polymer run, so existing results stay
    comparable and nothing silently changes underneath them.
    """
    total = int(polymer_charge) * int(n_polymers)
    if total == 0:
        return "", "", 0
    resname, element, _sign = _ION_FOR_NEGATIVE if total < 0 else _ION_FOR_POSITIVE
    return resname, element, abs(total)


def write_ion_pdb(path: str, resname: str, element: str) -> str:
    """Write a one-atom PDB for Packmol, named as the AMBER14 template expects."""
    from openmm import Vec3

    topology = app.Topology()
    chain = topology.addChain("I")
    residue = topology.addResidue(resname, chain)
    topology.addAtom(resname, app.Element.getBySymbol(element), residue)
    with open(path, "w", encoding="utf-8") as handle:
        app.PDBFile.writeFile(topology, [Vec3(0, 0, 0)] * 1, handle)
    return path


def add_ions_to_topology(topology: app.Topology, resname: str, element: str, count: int) -> None:
    """Append *count* single-atom ion residues as their own chain."""
    if count <= 0:
        return
    chain = topology.addChain("I")
    ion_element = app.Element.getBySymbol(element)
    for _ in range(count):
        residue = topology.addResidue(resname, chain)
        topology.addAtom(resname, ion_element, residue)


def polymer_md_preflight(
    psmiles: str,
    n_repeats: Optional[int] = None,
    random_seed: int = 42,
) -> Dict[str, Any]:
    """Build and parameterize the capped oligomer exactly as the matrix run does.

    Runs only :func:`build_polymer_oligomer_smiles`, 3D embedding and the GAFF
    template (:func:`create_ligand_system`): no Packmol, no protein, no MD. A
    chemistry GAFF/Gasteiger cannot type fails here in seconds instead of inside
    a 20-minute OpenMM job. Returns ``{"md_ready": bool, "stage", "error", ...}``.
    """
    if n_repeats is None:
        n_repeats = int(os.environ.get("BIOLOGIX_AI_OPENMM_N_REPEATS", "") or
                        os.environ.get("BIOLOGIX_AI_GMX_N_REPEATS", "") or 4)
    capped, actual = build_polymer_oligomer_smiles(psmiles, n_repeats)
    if not capped:
        return {"md_ready": False, "stage": "oligomer_build",
                "error": "build_polymer_oligomer_smiles returned empty (bad PSMILES or n_repeats)"}
    mol = Chem.MolFromSmiles(capped)
    if mol is None:
        return {"md_ready": False, "stage": "oligomer_build",
                "error": f"RDKit MolFromSmiles failed for capped SMILES: {capped[:120]}"}
    mol = Chem.AddHs(mol)
    ok, err = embed_mol_3d(mol, random_seed)
    if not ok:
        return {"md_ready": False, "stage": "embed", "error": f"3D embedding failed: {err}"}
    try:
        _top, system = create_ligand_system(mol, box_vectors=None)
    except Exception as exc:
        return {"md_ready": False, "stage": "gaff_parameterization",
                "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    return {
        "md_ready": True,
        "stage": "gaff_parameterization",
        "error": "",
        "n_repeats": actual,
        "n_atoms_per_chain": system.getNumParticles(),
        "net_charge": Chem.GetFormalCharge(mol),
    }
