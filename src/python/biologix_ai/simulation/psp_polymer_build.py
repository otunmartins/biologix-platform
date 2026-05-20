#!/usr/bin/env python3
"""
Polymer PDB for Packmol: RDKit oligomer (default) or optional PSP MoleculeBuilder.

PSP (Polymer Structure Predictor) expects CSV input with SMILES containing [*].
When PSP is on PYTHONPATH and dependencies exist, optional PSP path builds
longer oligomers; otherwise RDKit + psmiles matches CI and minimal deps.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def rdkit_polymer_pdb(psmiles: str, n_repeats: int, out_pdb: str, random_seed: int = 42) -> bool:
    """
    Build capped oligomer 3D PDB via RDKit (same chain as OpenMM / polymer_build path).

    Args:
        psmiles: Repeat unit with two [*] ports.
        n_repeats: Number of repeat units (chain length).
        out_pdb: Output PDB path (Angstrom).

    Returns:
        True if written.
    """
    from rdkit import Chem

    from .polymer_build import psmiles_to_mol_3d

    mol = psmiles_to_mol_3d(psmiles, n_repeats, random_seed=random_seed)
    if mol is None:
        return False
    Path(out_pdb).parent.mkdir(parents=True, exist_ok=True)
    Chem.MolToPDBFile(mol, out_pdb)
    return os.path.getsize(out_pdb) > 0


def try_psp_molecule_builder_pdb(
    psmiles: str,
    n_repeats: int,
    out_pdb: str,
    *,
    left_cap: str = "C(Cl)(Cl)(Cl)[*]",
    right_cap: str = "C(F)(F)(F)[*]",
) -> bool:
    """
    Run PSP MoleculeBuilder in a temp dir; copy first PDB to out_pdb.

    Requires: psp package importable (e.g. repo PSP on PYTHONPATH), OpenBabel,
    and typically many PSP deps. Returns False on any failure.
    """
    if "[*]" not in psmiles:
        return False
    try:
        import pandas as pd
        from psp.MoleculeBuilder import Builder
    except Exception as e:
        logger.debug("PSP import failed: %s", e)
        return False

    out_pdb = str(Path(out_pdb).resolve())
    Path(out_pdb).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="psp_mb_") as td:
        td = os.path.abspath(td)
        cwd = os.getcwd()
        os.chdir(td)
        try:
            df = pd.DataFrame(
                [
                    {
                        "ID": "PACK",
                        "smiles": psmiles,
                        "LeftCap": left_cap,
                        "RightCap": right_cap,
                    }
                ]
            )
            mol = Builder(
                df,
                ID_col="ID",
                SMILES_col="smiles",
                LeftCap="LeftCap",
                RightCap="RightCap",
                OutDir=os.path.join(td, "models"),
                Inter_Mol_Dis=6,
                Length=[int(n_repeats)],
                NumConf=1,
                Loop=False,
                NCores=1,
                IrrStruc=False,
                OPLS=False,
                GAFF2=False,
                Subscript=True,
            )
            mol.Build()
            # PSP names: PACK_N{n}_C1.pdb or similar
            models = Path(td) / "models"
            pdbs = sorted(models.glob("PACK*.pdb"))
            if not pdbs:
                pdbs = sorted(models.glob("*.pdb"))
            if not pdbs:
                return False
            import shutil

            shutil.copy2(pdbs[0], out_pdb)
            return os.path.getsize(out_pdb) > 0
        except Exception as e:
            logger.debug("PSP Build failed: %s", e)
            return False
        finally:
            os.chdir(cwd)


def build_polymer_pdb_for_packmol(
    psmiles: str,
    n_repeats: int,
    out_pdb: str,
    *,
    prefer_psp: bool = False,
    random_seed: int = 42,
) -> bool:
    """
    Single entry: polymer PDB for Packmol (PSP first if prefer_psp else RDKit).

    Returns True when out_pdb exists.
    """
    if prefer_psp and try_psp_molecule_builder_pdb(psmiles, n_repeats, out_pdb):
        return True
    return rdkit_polymer_pdb(psmiles, n_repeats, out_pdb, random_seed=random_seed)
