#!/usr/bin/env python3
"""
Lightweight PDB → PNG preview (3D scatter) for reports. Uses matplotlib (optional extra).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np


def _pdb_atom_coords_angstrom(pdb_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read ATOM/HETATM x,y,z (Angstrom) from a PDB file."""
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    with open(pdb_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            if len(line) < 54:
                continue
            try:
                xs.append(float(line[30:38]))
                ys.append(float(line[38:46]))
                zs.append(float(line[46:54]))
            except ValueError:
                continue
    return np.array(xs), np.array(ys), np.array(zs)


def write_complex_preview_png(
    pdb_path: str,
    output_path: str,
    *,
    max_points: int = 5000,
    figsize_inches: Tuple[float, float] = (6.0, 5.0),
    dpi: int = 120,
) -> Dict[str, Any]:
    """
    Render a static 3D scatter preview of PDB heavy-atom coordinates to PNG.

    Args:
        pdb_path: Path to PDB (e.g. minimized insulin + polymer complex).
        output_path: Destination ``.png`` path (parent dirs created).
        max_points: Subsample if the structure has more atoms (plot performance).
        figsize_inches: Matplotlib figure size.
        dpi: Raster resolution.

    Returns:
        ``{"ok": bool, "path": str, "n_atoms": int, "error": optional str}``
    """
    path = Path(output_path)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    pdb = Path(pdb_path)
    if not pdb.is_file():
        return {"ok": False, "error": f"PDB not found: {pdb}", "n_atoms": 0}

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        return {"ok": False, "error": f"matplotlib required for PDB preview: {e}", "n_atoms": 0}

    xs, ys, zs = _pdb_atom_coords_angstrom(str(pdb))
    n = len(xs)
    if n == 0:
        return {"ok": False, "error": "no ATOM/HETATM coordinates in PDB", "n_atoms": 0}

    if n > max_points:
        idx = np.linspace(0, n - 1, max_points, dtype=int)
        xs, ys, zs = xs[idx], ys[idx], zs[idx]

    px, py, pz = float(np.ptp(xs)), float(np.ptp(ys)), float(np.ptp(zs))
    use_2d = px < 1e-6 or py < 1e-6 or pz < 1e-6 or max(px, py, pz) / (min(px, py, pz) + 1e-9) > 1e6

    path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=figsize_inches, dpi=dpi)
    if use_2d:
        ax2 = fig.add_subplot(111)
        ax2.scatter(xs, ys, s=4.0, c="#1a3a5c", alpha=0.65, linewidths=0)
        ax2.set_aspect("equal", adjustable="box")
        ax2.set_axis_off()
        plt.tight_layout()
        fig.savefig(str(path), bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
    else:
        try:
            ax = fig.add_subplot(111, projection="3d")
            ax.scatter(xs, ys, zs, s=1.2, c="#1a3a5c", alpha=0.65, linewidths=0)
            try:
                m = max(px, py, pz, 1e-6)
                ax.set_box_aspect([px / m, py / m, pz / m])
            except Exception:
                pass
            ax.set_axis_off()
            ax.view_init(elev=20, azim=45)
            plt.tight_layout()
            fig.savefig(str(path), bbox_inches="tight", pad_inches=0.05)
        except Exception:
            plt.close(fig)
            fig = plt.figure(figsize=figsize_inches, dpi=dpi)
            ax2 = fig.add_subplot(111)
            ax2.scatter(xs, ys, s=4.0, c="#1a3a5c", alpha=0.65, linewidths=0)
            ax2.set_aspect("equal", adjustable="box")
            ax2.set_axis_off()
            plt.tight_layout()
            fig.savefig(str(path), bbox_inches="tight", pad_inches=0.05)
        finally:
            plt.close(fig)

    if not path.is_file():
        return {"ok": False, "error": f"PNG not written: {path}", "n_atoms": n}
    return {"ok": True, "path": str(path.resolve()), "n_atoms": n}
