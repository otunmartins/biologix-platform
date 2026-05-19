"""Bridge between PSMILES polymer representation and SMILES for retro engines."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _build_reverse_psmiles_table() -> Dict[str, str]:
    """Canonical PSMILES -> preferred human-readable polymer name."""
    from insulin_ai.material_mappings import _KNOWN_POLYMER_PSMILES, validate_psmiles

    rev: Dict[str, str] = {}

    def _score(name: str) -> tuple:
        return (1 if "poly(" in name else 0, len(name))

    for name, psmiles in _KNOWN_POLYMER_PSMILES.items():
        v = validate_psmiles(psmiles)
        canon = v.get("canonical", psmiles) if v.get("valid") else psmiles
        existing = rev.get(canon)
        if existing is None or _score(name) > _score(existing):
            rev[canon] = name
    return rev


_REVERSE_PSMILES: Dict[str, str] = _build_reverse_psmiles_table()


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


def resolve_retro_target(target: str) -> Dict[str, Any]:
    """Resolve target to {psmiles, material_name, monomer_smiles} for retrosynthesis."""
    target = (target or "").strip()
    if not target:
        return {"psmiles": "", "material_name": "", "monomer_smiles": ""}

    if "[*]" in target:
        from insulin_ai.material_mappings import validate_psmiles

        v = validate_psmiles(target)
        canon = v.get("canonical", target) if v.get("valid") else target
        name = _REVERSE_PSMILES.get(canon)
        mono = psmiles_to_smiles_target(target)
        return {
            "psmiles": target,
            "material_name": name or target,
            "monomer_smiles": mono or "",
        }

    from insulin_ai.material_mappings import name_to_psmiles

    res = name_to_psmiles(target)
    ps = res.get("psmiles", "") if res.get("ok") else ""
    mono = psmiles_to_smiles_target(ps) if ps else ""
    return {
        "psmiles": ps or target,
        "material_name": target,
        "monomer_smiles": mono,
    }
