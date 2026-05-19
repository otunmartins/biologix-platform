"""Bridge between PSMILES polymer representation and SMILES for retro engines."""

from __future__ import annotations

from typing import Optional


def psmiles_to_smiles_target(psmiles: str) -> str:
    """Convert a PSMILES repeat unit to a SMILES string suitable for retrosynthesis.

    PSMILES uses [*] to mark connection points in polymer repeat units.
    For retrosynthesis we need a concrete small-molecule SMILES, so we cap
    connection points with hydrogen (remove [*] markers).
    """
    try:
        from psmiles import PolymerSmiles

        ps = PolymerSmiles(psmiles)
        dimer_fn = ps.dimer
        dimer = dimer_fn() if callable(dimer_fn) else dimer_fn
        if dimer and isinstance(dimer, str):
            smiles_out = dimer.replace("[*]", "[H]")
            try:
                from rdkit import Chem
                mol = Chem.MolFromSmiles(smiles_out)
                if mol is not None:
                    return Chem.MolToSmiles(mol)
            except ImportError:
                pass
            return smiles_out
    except (ImportError, Exception):
        pass

    smiles = psmiles.replace("[*]", "[H]")
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return Chem.MolToSmiles(mol)
    except ImportError:
        pass
    return smiles


def name_to_target(name: str) -> Optional[str]:
    """Resolve a polymer common name to a PSMILES or SMILES target.

    Uses the existing material_mappings lookup when available.
    """
    try:
        from insulin_ai.material_mappings import name_to_psmiles

        result = name_to_psmiles(name)
        if result.get("ok"):
            return result["psmiles"]
    except ImportError:
        pass
    return None
