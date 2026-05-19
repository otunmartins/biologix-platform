#!/usr/bin/env python3
"""
Bond-consistent minimum-image unwrapping for cubic periodic boxes.

Used when writing visualization PDBs so PyMOL does not draw spurious long bonds
across periodic images (per-atom wrapping breaks covalent chains).
"""

from __future__ import annotations

from collections import deque
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
import openmm
import openmm.app as app
import openmm.unit as unit


def positions_to_nm_array(positions: Any) -> np.ndarray:
    """Convert OpenMM positions (Quantity, list, or ndarray) to shape (N, 3) in nm."""
    try:
        return np.array(positions.value_in_unit(unit.nanometers), dtype=float)
    except Exception:
        try:
            return np.array(
                [
                    [
                        float(p[0].value_in_unit(unit.nanometers)),
                        float(p[1].value_in_unit(unit.nanometers)),
                        float(p[2].value_in_unit(unit.nanometers)),
                    ]
                    for p in positions
                ],
                dtype=float,
            )
        except Exception:
            arr = np.asarray(positions, dtype=float)
            if arr.ndim != 2 or arr.shape[1] != 3:
                raise ValueError(f"Expected (N, 3) positions, got {arr.shape}") from None
            return arr


def min_image_displacement(dr: np.ndarray, box_edge_nm: float) -> np.ndarray:
    """Minimum-image displacement for a cubic box with edge *box_edge_nm* (nm)."""
    L = float(box_edge_nm)
    return dr - L * np.round(dr / L)


def unwrap_bond_consistent_pbc(
    pos_nm: np.ndarray,
    topology: app.Topology,
    box_edge_nm: float,
) -> np.ndarray:
    """
    Unwrap positions so bonded neighbors use minimum-image separations along a
    spanning tree (BFS). Does not fold into [0, L); coordinates stay continuous.
    """
    n = pos_nm.shape[0]
    if n == 0:
        return pos_nm.copy()
    out = pos_nm.astype(float).copy()
    adj: List[List[int]] = [[] for _ in range(n)]
    for bond in topology.bonds():
        a = bond[0].index
        b = bond[1].index
        if 0 <= a < n and 0 <= b < n:
            adj[a].append(b)
            adj[b].append(a)

    visited = [False] * n
    L = float(box_edge_nm)
    for start in range(n):
        if visited[start]:
            continue
        visited[start] = True
        q: deque[int] = deque([start])
        while q:
            i = q.popleft()
            for j in adj[i]:
                if not visited[j]:
                    visited[j] = True
                    dr = out[j] - out[i]
                    dr = min_image_displacement(dr, L)
                    out[j] = out[i] + dr
                    q.append(j)
    return out


def center_protein_com_at_cubic_cell_center(
    pos_nm: np.ndarray,
    n_protein: int,
    box_edge_nm: float,
) -> np.ndarray:
    """Translate all atoms so the protein COM lies at (L/2, L/2, L/2)."""
    L = float(box_edge_nm)
    out = pos_nm.copy()
    n_p = max(0, min(int(n_protein), len(out)))
    if n_p == 0:
        return out
    com = np.mean(out[:n_p], axis=0)
    target = np.array([L / 2.0, L / 2.0, L / 2.0], dtype=float)
    out += target - com
    return out


def cubic_box_edge_nm_from_vectors(box_vecs: Tuple[Any, Any, Any]) -> Optional[float]:
    """
    Return cubic box edge in nm if *box_vecs* is orthorhombic with (L,0,0),(0,L,0),(0,0,L).
    Otherwise return None.
    """
    try:
        a = np.array(
            [float(box_vecs[0][i].value_in_unit(unit.nanometers)) for i in range(3)],
            dtype=float,
        )
        b = np.array(
            [float(box_vecs[1][i].value_in_unit(unit.nanometers)) for i in range(3)],
            dtype=float,
        )
        c = np.array(
            [float(box_vecs[2][i].value_in_unit(unit.nanometers)) for i in range(3)],
            dtype=float,
        )
    except Exception:
        return None
    tol = 1e-4
    if (
        abs(a[1]) < tol
        and abs(a[2]) < tol
        and abs(b[0]) < tol
        and abs(b[2]) < tol
        and abs(c[0]) < tol
        and abs(c[1]) < tol
    ):
        lx, ly, lz = abs(a[0]), abs(b[1]), abs(c[2])
        if abs(lx - ly) < 1e-3 and abs(ly - lz) < 1e-3:
            return float((lx + ly + lz) / 3.0)
    return None


def prepare_matrix_complex_pdb_positions_nm(
    positions: Any,
    topology: app.Topology,
    n_protein: int,
    box_edge_nm: float,
) -> np.ndarray:
    """
    Bond-aware unwrap + center protein COM at cell center for matrix minimized PDB output.
    """
    pos = positions_to_nm_array(positions)
    pos = unwrap_bond_consistent_pbc(pos, topology, box_edge_nm)
    pos = center_protein_com_at_cubic_cell_center(pos, n_protein, box_edge_nm)
    return pos


def preprocess_pdb_path_for_pymol_viz(
    pdb_path: Path,
    *,
    n_protein_atoms: Optional[int] = None,
) -> Optional[Path]:
    """
    Load a PDB with OpenMM, unwrap bonds using periodic box from topology, optionally
    center on protein COM, write a temporary PDB, and return its path.

    Returns None if preprocessing is skipped (no bonds, no cubic box, or read error).
    Caller must delete the temp file when done.
    """
    try:
        pdb = app.PDBFile(str(pdb_path))
    except Exception:
        return None
    top = pdb.topology
    if top.getNumBonds() == 0:
        return None
    box = top.getPeriodicBoxVectors()
    if box is None:
        return None
    L = cubic_box_edge_nm_from_vectors(box)
    if L is None or L <= 0:
        return None
    pos = pdb.getPositions(asNumpy=True)
    pos_nm = np.asarray(pos, dtype=float)
    pos_nm = unwrap_bond_consistent_pbc(pos_nm, top, L)
    if n_protein_atoms is not None and int(n_protein_atoms) > 0:
        pos_nm = center_protein_com_at_cubic_cell_center(
            pos_nm, int(n_protein_atoms), L
        )
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix="_unwrap.pdb", prefix="insulin_ai_", delete=False, encoding="utf-8"
    )
    out_p = Path(tf.name)
    tf.close()
    top.setUnitCellDimensions(openmm.Vec3(L, L, L))
    with open(out_p, "w", encoding="utf-8") as fh:
        app.PDBFile.writeFile(top, unit.Quantity(pos_nm, unit.nanometers), fh)
    return out_p
