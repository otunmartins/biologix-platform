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


def ensure_insulin_pdb() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.isfile(INSULIN_PDB_PATH) and os.path.getsize(INSULIN_PDB_PATH) > 1000:
        return INSULIN_PDB_PATH
    for url in (INSULIN_PDB_URL, INSULIN_PDB_ALT):
        try:
            urllib.request.urlretrieve(url, INSULIN_PDB_PATH)
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
    """Build H-capped oligomer. Returns ``(smiles_or_None, actual_repeats)``."""
    if "[*]" not in psmiles or n_repeats < 1:
        return None, 0
    if n_repeats == 1:
        return psmiles.replace("[*]", "[H]"), 1
    try:
        from psmiles import PolymerSmiles
        chain = psmiles
        for rep in range(n_repeats - 1):
            ps = PolymerSmiles(chain)
            chain = str(ps.dimer(0)) if hasattr(ps, "dimer") else str(ps.dimerize(star_index=0))
        return chain.replace("[*]", "[H]"), n_repeats
    except Exception as exc:
        warnings.warn(
            f"psmiles dimer failed after {rep if 'rep' in dir() else 0} repeats ({exc}); "
            f"falling back to single-repeat H-capped SMILES"
        )
        return psmiles.replace("[*]", "[H]"), 1


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
