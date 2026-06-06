#!/usr/bin/env python3
"""
Render polymer repeat units to PNG using the psmiles library (FermiQ / Ramprasad-Group).

See: https://github.com/FermiQ/psmiles — ``PolymerSmiles.savefig`` writes a 2D depiction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Union


def _psmiles_output_is_svg(path: Path) -> bool:
    head = path.read_bytes()[:256].lstrip()
    return head.startswith(b"<?xml") or head.startswith(b"<svg") or head.startswith(b"<SVG")


def _svg_to_png_via_rdkit(path: Path, ps: Any) -> Optional[str]:
    """When psmiles writes SVG to a .png path, re-render with RDKit."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw
    except ImportError as e:
        return f"psmiles wrote SVG; RDKit unavailable for PNG fallback: {e}"

    smi: Optional[str] = None
    try:
        if hasattr(ps, "dimer"):
            smi = str(ps.dimer(0))
        elif hasattr(ps, "dimerize"):
            smi = str(ps.dimerize(star_index=0))
    except Exception as e:
        return f"psmiles wrote SVG; could not dimerize for PNG fallback: {e}"

    if not smi:
        return "psmiles wrote SVG; could not derive SMILES for PNG fallback"

    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return f"psmiles wrote SVG; invalid dimer SMILES for PNG fallback: {smi[:120]}"

    try:
        img = Draw.MolToImage(mol, size=(480, 480))
        img.save(str(path), format="PNG")
    except Exception as e:
        return f"psmiles wrote SVG; RDKit PNG conversion failed: {e}"
    return None


def safe_filename_basename(name: str, max_len: int = 80) -> str:
    """Filesystem-safe slug for PNG filenames."""
    s = re.sub(r"[^\w.\-]+", "_", name.strip(), flags=re.UNICODE)
    s = s.strip("._") or "structure"
    return s[:max_len]


def save_psmiles_png(
    psmiles: str,
    output_path: Union[str, Path],
    *,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """
    Save a 2D structure image of the PSMILES repeat unit to PNG.

    Args:
        psmiles: Polymer SMILES with two [*] connection points.
        output_path: Destination ``.png`` path (parent dirs created).
        overwrite: If False and file exists, return error without writing.

    Returns:
        ``{"ok": bool, "path": str, "error": optional str}``
    """
    path = Path(output_path)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    if not overwrite and path.is_file():
        return {"ok": False, "error": f"file exists: {path}"}

    try:
        from psmiles import PolymerSmiles
    except ImportError as e:
        return {"ok": False, "error": f"psmiles not installed: {e}"}

    psm = (psmiles or "").strip()
    if "[*]" not in psm and "*" not in psm:
        return {"ok": False, "error": "PSMILES must contain [*] connection points"}

    try:
        ps = PolymerSmiles(psm)
        path.parent.mkdir(parents=True, exist_ok=True)
        # API: savefig(path) on PolymerSmiles (see psmiles docs / FermiQ README)
        if hasattr(ps, "savefig"):
            ps.savefig(str(path))
        else:
            return {"ok": False, "error": "PolymerSmiles has no savefig (incompatible psmiles version)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not path.is_file():
        return {"ok": False, "error": f"expected PNG not written: {path}"}

    if _psmiles_output_is_svg(path):
        conv_err = _svg_to_png_via_rdkit(path, ps)
        if conv_err:
            return {"ok": False, "error": conv_err}

    return {"ok": True, "path": str(path.resolve())}
