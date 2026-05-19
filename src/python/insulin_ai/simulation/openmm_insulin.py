#!/usr/bin/env python3
"""Prepare insulin PDB for OpenMM: parse SSBOND, restrict chains A+B, clean."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# OpenMM imported lazily or at use; load_insulin_modeller needs it

SSBondPair = Tuple[str, int, str, int]  # (chain1, resseq1, chain2, resseq2)


def parse_ssbond_from_pdb(pdb_path: str) -> List[SSBondPair]:
    """
    Parse SSBOND lines from PDB. Format:
    SSBOND n CYS chain resseq   CYS chain resseq ...
    Returns list of (chain1, resseq1, chain2, resseq2).
    """
    pairs: List[SSBondPair] = []
    with open(pdb_path) as f:
        for line in f:
            if not line.startswith("SSBOND"):
                continue
            if len(line) < 29:
                continue
            # SSBOND   1 CYS A    6    CYS A   11
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


def filter_ssbond_for_chains(
    pairs: List[SSBondPair],
    keep_chains: Set[str],
) -> List[SSBondPair]:
    """Return SSBOND pairs where both residues are in keep_chains."""
    return [
        p for p in pairs
        if p[0] in keep_chains and p[2] in keep_chains
    ]


def prepare_insulin_ab_pdb(
    pdb_in: str,
    pdb_out: str,
    chains: Tuple[str, ...] = ("A", "B"),
) -> str:
    """
    Write a cleaned PDB with only ATOM lines for specified chains.
    Keeps SSBOND lines that reference only those chains.
    Drops HETATM, ANISOU, other records. Resolves altloc by taking first.
    """
    keep = set(chains)
    ssbonds = filter_ssbond_for_chains(
        parse_ssbond_from_pdb(pdb_in),
        keep,
    )
    # Write SSBOND lines explicitly (PDB cols 16, 18-21, 27, 29-32)
    lines_out: List[str] = []
    for i, (ch1, r1, ch2, r2) in enumerate(ssbonds, 1):
        lines_out.append(f"SSBOND {i:3d} CYS {ch1} {r1:4d}    CYS {ch2} {r2:4d}\n")
    seen_atoms: Set[Tuple[str, str, int, str]] = set()
    header_done = False
    with open(pdb_in) as f:
        for line in f:
            if line.startswith("SSBOND"):
                continue  # Already written from ssbonds
            if line.startswith("ATOM") or line.startswith("HETATM"):
                chain = line[21:22].strip()
                if chain not in keep:
                    continue
                resseq_s = line[22:26].strip()
                try:
                    resseq = int(resseq_s)
                except ValueError:
                    continue
                name = line[12:16].strip()
                alt = line[16:17].strip() or " "
                key = (chain, resseq_s, resseq, name)
                if alt and alt != "A":
                    if key in seen_atoms:
                        continue
                seen_atoms.add(key)
                atom_line = line[:16] + " " + line[17:66] + "\n"
                if line.startswith("HETATM"):
                    resname = line[17:20].strip()
                    if resname in ("HOH", "WAT", "ZN", "CL", "NA", "CA"):
                        continue
                lines_out.append(atom_line)
            elif line.startswith("TER"):
                chain_before = ""
                for prev in reversed(lines_out):
                    if prev.startswith("ATOM") or prev.startswith("HETATM"):
                        chain_before = prev[21:22].strip()
                        break
                if chain_before in keep:
                    lines_out.append(line)
            elif line.startswith("MODEL") or line.startswith("ENDMDL"):
                continue
            elif line.startswith("HEADER") or line.startswith("TITLE") or line.startswith("COMPND"):
                if not lines_out:
                    lines_out.append(line)
    Path(pdb_out).parent.mkdir(parents=True, exist_ok=True)
    with open(pdb_out, "w") as f:
        f.writelines(lines_out)
    return pdb_out


def add_disulfide_bonds_from_ssbond(modeller, pdb_path: str) -> None:
    """
    Add disulfide bonds to modeller.topology from SSBOND lines in PDB.
    OpenMM requires bonds before addHydrogens to choose CYS vs CYX.
    """
    import openmm.app as app

    pairs = parse_ssbond_from_pdb(pdb_path)
    if not pairs:
        return

    # Build map (chain_id, resseq, atom_name) -> atom for SG only
    sg_map: dict[tuple[str, int, str], object] = {}
    for atom in modeller.topology.atoms():
        res = atom.residue
        ch_id = res.chain.id if hasattr(res.chain, "id") else str(res.chain.id)
        res_id = res.id
        try:
            resseq = int(res_id) if isinstance(res_id, str) else res_id
        except (ValueError, TypeError):
            continue
        if atom.name == "SG":
            sg_map[(ch_id, resseq, "SG")] = atom

    def _has_bond(atom1, atom2):
        for bond in modeller.topology.bonds():
            if (bond.atom1, bond.atom2) == (atom1, atom2) or (bond.atom1, bond.atom2) == (atom2, atom1):
                return True
        return False

    for ch1, r1, ch2, r2 in pairs:
        a1 = sg_map.get((ch1, r1, "SG"))
        a2 = sg_map.get((ch2, r2, "SG"))
        if a1 is None or a2 is None:
            continue
        if _has_bond(a1, a2):
            continue
        modeller.topology.addBond(a1, a2)


def _add_missing_oxt_to_modeller(modeller) -> None:
    """
    Add OXT to C-terminal residues that lack it. Required when PDBFixer is unavailable.
    Modifies modeller.topology and modeller.positions in place.
    """
    import openmm.app as app
    import openmm.unit as unit
    import numpy as np

    oxygen = app.element.oxygen
    pos_list = list(modeller.positions.value_in_unit(unit.nanometers))
    topology = modeller.topology
    n_inserted = 0

    for chain in topology.chains():
        residues = list(chain.residues())
        if not residues:
            continue
        last_res = residues[-1]
        has_oxt = any(a.name == "OXT" for a in last_res.atoms())
        if has_oxt:
            continue
        atom_names = {a.name for a in last_res.atoms()}
        if not ({"C", "O", "CA"} <= atom_names):
            continue
        c_atom = next(a for a in last_res.atoms() if a.name == "C")
        o_atom = next(a for a in last_res.atoms() if a.name == "O")
        ca_atom = next(a for a in last_res.atoms() if a.name == "CA")
        atoms_list = list(topology.atoms())
        c_idx = atoms_list.index(c_atom)
        o_idx = atoms_list.index(o_atom)
        c_pos = np.array(pos_list[c_idx])
        o_pos = np.array(pos_list[o_idx])
        ca_pos = np.array(pos_list[atoms_list.index(ca_atom)])
        co_vec = o_pos - c_pos
        ca_vec = ca_pos - c_pos
        co_len = np.linalg.norm(co_vec)
        if co_len < 0.01:
            continue
        co_unit = co_vec / co_len
        normal = np.cross(ca_vec, co_vec)
        if np.linalg.norm(normal) < 0.01:
            normal = np.array([1, 0, 0]) if abs(co_unit[0]) < 0.9 else np.array([0, 1, 0])
        normal = normal / np.linalg.norm(normal)
        oxt_dir = np.cos(np.radians(117)) * co_unit + np.sin(np.radians(117)) * np.cross(normal, co_unit)
        oxt_dir_norm = oxt_dir / np.linalg.norm(oxt_dir)
        oxt_pos = c_pos + 0.126 * oxt_dir_norm
        insert_idx = max(atoms_list.index(a) for a in last_res.atoms()) + 1 + n_inserted
        oxt_atom = topology.addAtom("OXT", oxygen, last_res)
        topology.addBond(c_atom, oxt_atom)
        pos_list.insert(insert_idx, oxt_pos.tolist())
        n_inserted += 1

    if n_inserted > 0:
        modeller.positions = unit.Quantity(np.array(pos_list), unit.nanometers)


def load_insulin_modeller(pdb_path: str, add_ssbond: bool = True, use_pdbfixer: bool = True):
    """
    Load PDB into OpenMM Modeller. Optionally add disulfide bonds from SSBOND.
    Uses PDBFixer to add missing atoms (e.g. OXT) when use_pdbfixer=True.
    When PDBFixer is unavailable, adds OXT manually via _add_missing_oxt_to_modeller.
    Returns Modeller (topology + positions).
    """
    import openmm.app as app

    if use_pdbfixer:
        try:
            from pdbfixer import PDBFixer

            fixer = PDBFixer(filename=pdb_path)
            fixer.findMissingResidues()
            fixer.findMissingAtoms()
            fixer.addMissingAtoms()
            modeller = app.Modeller(fixer.topology, fixer.positions)
        except ImportError:
            pdb = app.PDBFile(pdb_path)
            modeller = app.Modeller(pdb.topology, pdb.positions)
            _add_missing_oxt_to_modeller(modeller)
    else:
        pdb = app.PDBFile(pdb_path)
        modeller = app.Modeller(pdb.topology, pdb.positions)
        _add_missing_oxt_to_modeller(modeller)
    if add_ssbond:
        add_disulfide_bonds_from_ssbond(modeller, pdb_path)
    return modeller
