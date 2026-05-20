#!/usr/bin/env python3
"""
Render polymer repeat units to PNG using the psmiles library (FermiQ / Ramprasad-Group).

See: https://github.com/FermiQ/psmiles — ``PolymerSmiles.savefig`` writes a 2D depiction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Union


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
    return {"ok": True, "path": str(path.resolve())}
