#!/usr/bin/env python3
"""
Rough melt packing: how many identical chains fill a box at a target polymer density.

Entanglement is NOT set by Packmol—only by subsequent NVT/NPT MD (many ns).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, Optional, Tuple

PackingMode = Literal["shell", "bulk"]

AVOGADRO = 6.02214076e23


def shell_volume_cm3(box_edge_nm: float, shell_inner_angstrom: float) -> float:
    """
    Volume of cubic box minus central sphere (shell for polymer packing).

    Polymers are placed inside box and outside sphere. V = box³ - (4/3)π R³.

    Args:
        box_edge_nm: Cubic box edge length in nm.
        shell_inner_angstrom: Inner sphere radius in Angstrom (polymers excluded).

    Returns:
        Accessible volume in cm³.
    """
    box_cm = box_edge_nm * 1e-7
    r_cm = shell_inner_angstrom * 1e-8
    v_box = box_cm**3
    v_sphere = (4.0 / 3.0) * math.pi * r_cm**3
    return max(v_box - v_sphere, v_box * 0.1)


def compute_shell_inner_from_pdb(pdb_path: str) -> float:
    """
    Max distance of protein atoms from center of geometry (nm); return as Angstrom.

    Used as shell inner radius so polymers are excluded from insulin region.
    Fallback 1.5 nm (15 Angstrom) if PDB unreadable.
    """
    try:
        from .polymer_build import pdb_atom_coords_angstrom

        _, coords = pdb_atom_coords_angstrom(pdb_path, include_hetatm=False)
        if not coords:
            return 15.0
        import numpy as np

        arr = np.array(coords)
        com = arr.mean(axis=0)
        dists = np.linalg.norm(arr - com, axis=1)
        r_nm = float(np.max(dists)) * 0.1  # Angstrom to nm
        return r_nm * 10.0  # nm to Angstrom for Packmol
    except Exception:
        return 15.0


def suggest_n_polymers_from_density(
    target_density_g_cm3: float,
    psmiles: str,
    n_repeats: int,
    box_size_nm: float,
    shell_inner_angstrom: Optional[float] = None,
    insulin_pdb_path: Optional[str] = None,
    n_min: int = 4,
    n_max: int = 100,
    packing_mode: PackingMode = "bulk",
    volume_fraction_polymer: float = 0.92,
) -> Tuple[int, Optional[float]]:
    """
    Derive n_polymers from target polymer density.

    **shell** mode: n = density × V_shell × N_A / MW where V_shell is the box minus
    the inner sphere (encapsulation).

    **bulk** mode: n = density × (V_box × volume_fraction_polymer) × N_A / MW —
    polymer mass per **total cell** (approximate; insulin mass is secondary).

    Args:
        target_density_g_cm3: Target polymer density in g/cm³.
        psmiles: Repeat unit SMILES with [*].
        n_repeats: Repeat units per chain.
        box_size_nm: Cubic box edge in nm.
        shell_inner_angstrom: Inner sphere radius (Å), **shell** mode only.
        insulin_pdb_path: PDB path for radius; used when shell_inner_angstrom is None.
        n_min, n_max: Clamp n_polymers for Packmol tractability (default *n_max* **100**;
        a low cap with a large box makes the cell look sparse vs target density).
        packing_mode: ``bulk`` (default) or ``shell``.
        volume_fraction_polymer: Effective polymer volume fraction of the cell (**bulk**).

    Returns:
        (n_polymers, shell_inner_angstrom). Second value is ``None`` in **bulk** mode.
    """
    mw = estimate_chain_mw_g_mol(psmiles, n_repeats)
    if mw <= 0:
        if packing_mode == "bulk":
            return max(n_min, min(n_max, 12)), None
        if shell_inner_angstrom is None and insulin_pdb_path:
            shell_inner_angstrom = compute_shell_inner_from_pdb(insulin_pdb_path)
        if shell_inner_angstrom is None:
            shell_inner_angstrom = 15.0
        return max(n_min, min(n_max, 12)), shell_inner_angstrom

    if packing_mode == "bulk":
        v = box_volume_cm3(box_size_nm) * volume_fraction_polymer
        n_mol = target_density_g_cm3 * v / mw
        n = int(round(n_mol * AVOGADRO))
        return max(n_min, min(n_max, n)), None

    if shell_inner_angstrom is None and insulin_pdb_path:
        shell_inner_angstrom = compute_shell_inner_from_pdb(insulin_pdb_path)
    if shell_inner_angstrom is None:
        shell_inner_angstrom = 15.0

    v_shell = shell_volume_cm3(box_size_nm, shell_inner_angstrom)
    n_mol = target_density_g_cm3 * v_shell / mw
    n = int(round(n_mol * AVOGADRO))
    return max(n_min, min(n_max, n)), shell_inner_angstrom


def suggest_box_size_from_shell(
    shell_inner_angstrom: float,
    shell_thickness_angstrom: float = 30.0,
    margin_angstrom: float = 5.0,
) -> float:
    """
    Box edge (nm) from shell geometry: box = 2 * (R_inner + thickness + margin).

    Returns box size in nm.
    """
    half_nm = (shell_inner_angstrom + shell_thickness_angstrom + margin_angstrom) * 0.1
    return 2.0 * half_nm


def box_volume_cm3(box_edge_nm: float) -> float:
    """Cubic box edge in nm -> volume in cm^3."""
    cm = box_edge_nm * 1e-7
    return cm**3


def suggest_n_chains_for_density(
    box_edge_nm: float,
    polymer_molar_mass_g_mol: float,
    target_density_g_cm3: float = 0.85,
    volume_fraction_polymer: float = 0.92,
) -> int:
    """
    Approximate number of identical chains to reach ~target_density in the box.

    volume_fraction_polymer: leave headroom for insulin + void (~0.9–0.95).

    Returns at least 1.
    """
    if polymer_molar_mass_g_mol <= 0 or box_edge_nm <= 0:
        return 1
    v = box_volume_cm3(box_edge_nm) * volume_fraction_polymer
    mass_g = target_density_g_cm3 * v
    n_mol = mass_g / polymer_molar_mass_g_mol
    n = int(max(1, round(n_mol * AVOGADRO)))
    return n


def polymer_mw_from_rdkit_mol(mol) -> Optional[float]:
    """Molar mass (g/mol) from RDKit mol."""
    try:
        from rdkit.Chem import Descriptors

        return float(Descriptors.MolWt(mol))
    except Exception:
        return None


# C2H4 repeat (polyethylene-type)
_MW_C2H4 = 12.011 * 2 + 1.008 * 4  # ~28.05 g/mol
_MW_H2 = 1.008 * 2


def estimate_chain_mw_g_mol(psmiles: str, n_repeats: int) -> float:
    """
    Chain MW without building huge oligomers (avoids psmiles segfault / RDKit embed).

    For [*]CC[*] uses linear alkane C_{2n}H_{4n+2}. For n_repeats<=6 may use
    psmiles+RDKit MolWt on capped SMILES only (2D, no embed).
    """
    if n_repeats < 1:
        n_repeats = 1
    s = psmiles.replace(" ", "").strip()
    if s == "[*]CC[*]" or s == "[*]C/C[*]":
        return _MW_C2H4 * n_repeats + _MW_H2
    if n_repeats <= 6:
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors

            from insulin_ai.simulation.polymer_build import build_polymer_oligomer_smiles

            capped, _actual = build_polymer_oligomer_smiles(psmiles, n_repeats)
            if capped:
                m = Chem.MolFromSmiles(capped)
                if m is not None:
                    return float(Descriptors.MolWt(m))
        except Exception:
            pass
    # Generic: one capped repeat * n (overestimate ~ok for suggest-n)
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors

        one = psmiles.replace("[*]", "[H]")
        m = Chem.MolFromSmiles(one)
        if m is not None:
            return float(Descriptors.MolWt(m)) * n_repeats
    except Exception:
        pass
    return 200.0 * n_repeats


def suggest_n_polymer_around_insulin(
    box_edge_nm: float,
    polymer_mw_g_mol: float,
    n_repeats: int,
    insulin_exclusion_radius_nm: float = 1.5,
    fill_fraction: float = 0.45,
) -> int:
    """
    Rough chain count to *surround* insulin in PBC — not a full melt.

    Polymer is placed in the box minus a central sphere (insulin + clearance).
    Uses a modest effective density in that annulus so Packmol stays tractable.
    Result clamped to [10, 48] by default scale; caller can override box/N.
    """
    import math

    if box_edge_nm <= 0 or polymer_mw_g_mol <= 0:
        return 12
    half_nm = box_edge_nm / 2.0
    # Cube edge in cm
    a_cm = box_edge_nm * 1e-7
    v_box_cm3 = a_cm**3
    r_cm = min(insulin_exclusion_radius_nm, half_nm * 0.9) * 1e-7
    v_void_cm3 = (4.0 / 3.0) * math.pi * r_cm**3
    v_poly_cm3 = max(v_box_cm3 - v_void_cm3, v_box_cm3 * 0.25) * fill_fraction
    # ~0.25 g/cm³ effective in annulus = loose packing
    density = 0.28
    mass_g = density * v_poly_cm3
    n = int(round(mass_g / polymer_mw_g_mol * AVOGADRO))
    # Practical Packmol + OpenMM matrix limits (~20 chains usually enough visually)
    return max(10, min(28, n))
