"""Structural metrics for a protein/polymer complex.

The property extractor and the composite screening score consume
insulin_rmsd_to_initial_nm and insulin_polymer_contacts, but nothing produced
them, so the composite score was always None and candidates could not be ranked.
"""

from typing import Optional

import numpy as np


def kabsch_rmsd_nm(initial: np.ndarray, final: np.ndarray) -> Optional[float]:
    """RMSD in nm between two coordinate sets after optimal superposition.

    Removes rigid-body translation and rotation so the value reflects genuine
    conformational change rather than drift across the box.
    """
    a = np.asarray(initial, dtype=float)
    b = np.asarray(final, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[0] < 3:
        return None
    p = a - a.mean(axis=0)
    q = b - b.mean(axis=0)
    covariance = p.T @ q
    try:
        v, _s, wt = np.linalg.svd(covariance)
    except np.linalg.LinAlgError:
        return None
    sign = np.sign(np.linalg.det(v @ wt))
    correction = np.diag([1.0, 1.0, sign if sign != 0 else 1.0])
    rotation = v @ correction @ wt
    aligned = p @ rotation
    return float(np.sqrt(np.mean(np.sum((aligned - q) ** 2, axis=1))))


def minimum_image(delta: np.ndarray, box_nm: Optional[float]) -> np.ndarray:
    """Wrap displacement vectors into the periodic box."""
    if not box_nm or box_nm <= 0:
        return delta
    return delta - box_nm * np.round(delta / box_nm)


def contact_count(
    protein: np.ndarray,
    polymer: np.ndarray,
    cutoff_nm: float = 0.4,
    box_nm: Optional[float] = None,
) -> int:
    """Number of protein/polymer heavy-atom pairs within cutoff_nm.

    Counted under the minimum-image convention so contacts across a periodic
    boundary are not missed.
    """
    a = np.asarray(protein, dtype=float)
    b = np.asarray(polymer, dtype=float)
    if a.ndim != 2 or b.ndim != 2 or a.size == 0 or b.size == 0:
        return 0
    cutoff_sq = float(cutoff_nm) ** 2
    total = 0
    # Chunked so a large matrix (thousands of atoms each side) stays bounded.
    chunk = max(1, int(2_000_000 // max(1, b.shape[0])))
    for start in range(0, a.shape[0], chunk):
        block = a[start : start + chunk]
        delta = block[:, None, :] - b[None, :, :]
        delta = minimum_image(delta, box_nm)
        total += int(np.count_nonzero(np.sum(delta * delta, axis=-1) <= cutoff_sq))
    return total
