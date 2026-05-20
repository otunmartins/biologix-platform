#!/usr/bin/env python3
"""
Quantitative packing metrics for insulin + polymer matrix PDBs.

OpenMM writes **all** protein atoms first (``n_insulin_atoms`` records), then polymer atoms.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np


def _parse_atom_line(line: str) -> Tuple[bool, np.ndarray, str]:
    """Return (is_heavy, xyz_angstrom, element)."""
    if len(line) < 54:
        return False, np.zeros(3), ""
    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
    except ValueError:
        return False, np.zeros(3), ""
    el = line[76:78].strip() if len(line) >= 78 else ""
    if not el:
        name = line[12:16].strip()
        el = re.sub(r"\d", "", name)[:1] or "?"
    elu = el.upper()[:1]
    heavy = elu not in ("H", "D", "")
    return heavy, np.array([x, y, z], dtype=float), elu


def _split_protein_polymer_heavy(
    path: Path, n_protein_atoms_total: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Heavy-atom coordinates: first ``n_protein_atoms_total`` ATOM/HETATM lines = protein region.
    """
    prot: list[np.ndarray] = []
    poly: list[np.ndarray] = []
    idx = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            heavy, xyz, _ = _parse_atom_line(line)
            if heavy:
                if idx < n_protein_atoms_total:
                    prot.append(xyz)
                else:
                    poly.append(xyz)
            idx += 1
    return (
        np.array(prot, dtype=float) if prot else np.zeros((0, 3)),
        np.array(poly, dtype=float) if poly else np.zeros((0, 3)),
    )


def compute_matrix_packing_metrics(
    pdb_path: str,
    n_protein_atoms_total: int,
) -> Dict[str, Any]:
    """
    Nearest protein atom for each polymer heavy atom; distances in **nm**.

    Args:
        pdb_path: Minimized complex PDB.
        n_protein_atoms_total: OpenMM ``n_insulin_atoms`` (includes hydrogens in count).
    """
    path = Path(pdb_path)
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}"}
    if n_protein_atoms_total <= 0:
        return {"ok": False, "error": "n_protein_atoms_total must be positive"}

    prot, poly = _split_protein_polymer_heavy(path, n_protein_atoms_total)
    if prot.shape[0] == 0 or poly.shape[0] == 0:
        return {
            "ok": False,
            "error": "no protein or polymer heavy atoms after split",
            "n_protein_heavy": int(prot.shape[0]),
            "n_polymer_heavy": int(poly.shape[0]),
        }

    diff = poly[:, None, :] - prot[None, :, :]
    d2 = np.einsum("ijk,ijk->ij", diff, diff)
    d_min_a = np.sqrt(np.min(d2, axis=1))
    d_nm = d_min_a * 0.1

    return {
        "ok": True,
        "n_protein_heavy": int(prot.shape[0]),
        "n_polymer_heavy": int(poly.shape[0]),
        "min_polymer_protein_distance_nm": float(np.min(d_nm)),
        "median_polymer_protein_distance_nm": float(np.median(d_nm)),
        "mean_polymer_protein_distance_nm": float(np.mean(d_nm)),
        "fraction_polymer_within_0.50_nm": float(np.mean(d_nm <= 0.50)),
        "fraction_polymer_within_0.80_nm": float(np.mean(d_nm <= 0.80)),
        "fraction_polymer_within_1.20_nm": float(np.mean(d_nm <= 1.20)),
    }
