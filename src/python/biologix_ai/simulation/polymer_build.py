#!/usr/bin/env python3
"""RDKit-only PSMILES oligomer build (no OpenMM)."""

import os
import urllib.request
import warnings
from typing import Optional, Tuple, List

from rdkit import Chem
from rdkit.Chem import AllChem

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INSULIN_PDB_PATH = os.path.join(DATA_DIR, "4F1C.pdb")
INSULIN_PDB_URL = "https://files.rcsb.org/download/4F1C.pdb"
INSULIN_PDB_ALT = "https://files.rcsb.org/download/4INS.pdb"


def ensure_default_target_pdb() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.isfile(INSULIN_PDB_PATH) and os.path.getsize(INSULIN_PDB_PATH) > 1000:
        return INSULIN_PDB_PATH
    for url in (INSULIN_PDB_URL, INSULIN_PDB_ALT):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            with open(INSULIN_PDB_PATH, "wb") as pdb_file:
                pdb_file.write(data)
            if os.path.isfile(INSULIN_PDB_PATH) and os.path.getsize(INSULIN_PDB_PATH) > 1000:
                return INSULIN_PDB_PATH
        except Exception as e:
            warnings.warn(f"fetch pdb {url}: {e}")
    raise FileNotFoundError(f"Place 4F1C.pdb in {DATA_DIR}")


def embed_mol_3d(mol: Chem.Mol, random_seed: int = 42) -> Tuple[bool, str]:
    """Embed and MMFF-optimize a molecule. Returns ``(success, error_or_empty)``."""
    try:
        r = AllChem.EmbedMolecule(mol, randomSeed=random_seed)
        if r != 0:
            r2 = AllChem.EmbedMolecule(mol, randomSeed=random_seed, useRandomCoords=True)
            if r2 != 0:
                return False, f"EmbedMolecule failed (code={r}, random-coords code={r2})"
        AllChem.MMFFOptimizeMolecule(mol)
        return True, ""
    except Exception as exc:
        return False, f"embed/MMFF error: {exc}"


def build_polymer_oligomer_smiles(
    psmiles: str, n_repeats: int
) -> Tuple[Optional[str], int]:
    """Build an H-capped, head-to-tail oligomer. Returns ``(smiles_or_None, actual_repeats)``.

    ``n_repeats`` copies of the repeat unit are joined right end to left end, so the chain has
    exactly ``n_repeats`` units and every junction is the bond the PSMILES describes (an amide
    joins ``C(=O)`` to ``N``). Stereocenters are preserved. A single repeat is unchanged: the
    stars become hydrogens.

    An earlier version called ``psmiles.PolymerSmiles.dimer`` in a loop. ``dimer`` doubles the
    chain it is given, so ``n`` repeats built ``2**(n-1)`` units (the default of 4 built 8),
    and it joins head to head, which turned PEG's ``[*]OCC[*]`` into a peroxide and a lysine
    unit into a hydrazine. Results computed before this fix used those chains.
    """
    if "[*]" not in psmiles or n_repeats < 1:
        return None, 0
    if n_repeats == 1:
        return psmiles.replace("[*]", "[H]"), 1
    unit = Chem.MolFromSmiles(psmiles)
    if unit is None:
        return None, 0
    stars = [a.GetIdx() for a in unit.GetAtoms() if a.GetAtomicNum() == 0]
    if len(stars) != 2:
        warnings.warn(
            f"oligomer needs a repeat unit with exactly two [*] ends, found {len(stars)}: {psmiles[:80]}"
        )
        return None, 0
    heavy_per_unit = unit.GetNumAtoms() - 2
    charge_per_unit = Chem.GetFormalCharge(unit)

    combined: Optional[Chem.Mol] = None
    for k in range(n_repeats):
        copy = Chem.Mol(unit)
        # Right end of unit k pairs with left end of unit k+1; the two chain ends stay open.
        copy.GetAtomWithIdx(stars[0]).SetAtomMapNum(k if k > 0 else 0)
        copy.GetAtomWithIdx(stars[1]).SetAtomMapNum(k + 1 if k < n_repeats - 1 else 0)
        combined = copy if combined is None else Chem.CombineMols(combined, copy)
    try:
        chain = Chem.molzip(combined)
    except Exception as exc:
        warnings.warn(f"could not join {n_repeats} repeats of {psmiles[:60]}: {exc}")
        return None, 0
    rw = Chem.RWMol(chain)
    for atom in rw.GetAtoms():
        if atom.GetAtomicNum() == 0:  # a chain end: cap with hydrogen
            atom.SetAtomMapNum(0)
            atom.SetAtomicNum(1)
    capped = rw.GetMol()
    try:
        Chem.SanitizeMol(capped)
    except Exception as exc:
        warnings.warn(f"oligomer of {psmiles[:60]} failed sanitization: {exc}")
        return None, 0
    # The chain must hold exactly n units: right heavy-atom count and right charge.
    heavy = sum(1 for a in capped.GetAtoms() if a.GetAtomicNum() > 1)
    if heavy != n_repeats * heavy_per_unit or Chem.GetFormalCharge(capped) != n_repeats * charge_per_unit:
        warnings.warn(f"oligomer of {psmiles[:60]} does not have {n_repeats} repeat units; refusing to simulate it")
        return None, 0
    return Chem.MolToSmiles(capped), n_repeats


def psmiles_to_mol_3d(psmiles: str, n_repeats: int, random_seed: int = 42) -> Optional[Chem.Mol]:
    capped, _actual = build_polymer_oligomer_smiles(psmiles, n_repeats)
    if not capped:
        return None
    mol = Chem.MolFromSmiles(capped)
    if mol is None:
        return None
    mol = Chem.AddHs(mol)
    ok, _err = embed_mol_3d(mol, random_seed)
    if not ok:
        return None
    return mol


def mol_to_pdb_block(mol: Chem.Mol) -> str:
    return Chem.MolToPDBBlock(mol)


def pdb_atom_coords_angstrom(
    pdb_path: str,
    *,
    include_hetatm: bool = True,
) -> Tuple[List[str], List[Tuple[float, float, float]]]:
    """ATOM (and optionally HETATM) from PDB; skip common waters. Coordinates in Å."""
    symbols: List[str] = []
    coords: List[Tuple[float, float, float]] = []
    skip_res = {"HOH", "WAT", "H2O", "DOD"}
    with open(pdb_path) as f:
        for line in f:
            if line.startswith("ATOM"):
                pass
            elif include_hetatm and line.startswith("HETATM"):
                pass
            else:
                continue
            res = line[17:20].strip()
            if res in skip_res:
                continue
            try:
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
            except ValueError:
                continue
            el = (line[76:78] or line[12:14]).strip()
            if not el:
                el = line[12:14].strip()[0]
            if len(el) > 1:
                el = el[0].upper() + el[1:].lower() if el[1:].isalpha() else el[0]
            else:
                el = el.upper()
            symbols.append(el)
            coords.append((x, y, z))
    return symbols, coords
