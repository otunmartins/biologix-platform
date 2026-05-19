#!/usr/bin/env python3
"""
Packmol integration for insulin + polymer matrix packing.

Packs insulin (1 copy, fixed at box center) and polymer chains (N copies)
into a cubic periodic box.  Two packing modes are supported:

  **bulk** (default): polymers fill the entire cell around insulin.
  Suitable for periodic MD of an insulin–polymer matrix.

  **shell**: polymers are placed inside the box but outside a central
  exclusion sphere, creating an annular shell around insulin.

When *box_size_nm* is None (default), the box is auto-sized to be just
large enough to contain the insulin and the requested number of polymer
chains at a reasonable packing density.

Coordinates in the output PDB span [0, L] in each dimension, with
insulin centered at (L/2, L/2, L/2).
"""

import os
import shutil
import subprocess
import tempfile
import time
import warnings
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

PackingMode = Literal["shell", "bulk"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _packmol_available() -> bool:
    """Check if the packmol binary is on PATH."""
    return shutil.which("packmol") is not None


def _parse_pdb_extents(pdb_path: str) -> Tuple[int, Tuple[float, float, float]]:
    """
    Parse a PDB file and return *(n_atoms, (span_x, span_y, span_z))*.

    Only ATOM and HETATM records are considered.  Spans are in Angstroms.
    """
    x_min = y_min = z_min = float("inf")
    x_max = y_max = z_max = float("-inf")
    n = 0
    with open(pdb_path) as fh:
        for line in fh:
            if line.startswith(("ATOM  ", "HETATM")):
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                except (ValueError, IndexError):
                    continue
                n += 1
                if x < x_min:
                    x_min = x
                if x > x_max:
                    x_max = x
                if y < y_min:
                    y_min = y
                if y > y_max:
                    y_max = y
                if z < z_min:
                    z_min = z
                if z > z_max:
                    z_max = z
    if n == 0:
        raise ValueError(f"No ATOM/HETATM records in {pdb_path}")
    return n, (x_max - x_min, y_max - y_min, z_max - z_min)


def estimate_box_edge_angstrom(
    insulin_pdb_path: str,
    polymer_pdb_path: str,
    n_polymers: int,
    tolerance_angstrom: float = 2.0,
    padding_angstrom: float = 6.0,
    volume_per_atom_A3: float = 20.0,
    packing_fraction: float = 0.40,
) -> float:
    """
    Estimate the minimum cubic box edge (Angstroms) for insulin + N polymers.

    Two independent lower bounds are computed and the larger is returned:

    1. **Volume-based** – total atom count × *volume_per_atom_A3* divided by
       a target *packing_fraction*, then cube-rooted.
    2. **Insulin-extent-based** – largest insulin bounding-box dimension +
       2 × *padding_angstrom*.

    An extra *tolerance_angstrom* is added so that atoms near the periodic
    boundary do not clash with their images.
    """
    n_ins, ins_spans = _parse_pdb_extents(insulin_pdb_path)
    n_poly, _ = _parse_pdb_extents(polymer_pdb_path)

    # Volume-based estimate
    total_atoms = n_ins + n_polymers * n_poly
    box_vol = (total_atoms * volume_per_atom_A3) / packing_fraction
    edge_vol = box_vol ** (1.0 / 3.0)

    # Insulin extent based estimate
    edge_ins = max(ins_spans) + 2.0 * padding_angstrom

    # Take the larger, then add tolerance for periodic-image safety
    return max(edge_vol, edge_ins) + tolerance_angstrom


# ---------------------------------------------------------------------------
# Packmol input generation
# ---------------------------------------------------------------------------


def build_packmol_inp_content(
    insulin_pdb_path: str,
    polymer_pdb_path: str,
    n_polymers: int,
    output_path: str,
    box_edge_angstrom: float,
    tolerance_angstrom: float,
    seed: int,
    shell_only_angstrom: Optional[float] = None,
    packing_mode: PackingMode = "bulk",
    maxit: int = 20,
    nloop: int = 200,
) -> str:
    """
    Build Packmol input text.

    The box occupies [0, L]³ with insulin centred at (L/2, L/2, L/2).
    Polymer atoms are constrained to [tol/2, L − tol/2]³ so that no atom
    is closer than tol/2 to the boundary, preventing periodic-image clashes.
    """
    L = box_edge_angstrom
    half = L / 2.0

    # Inset polymer region by half the tolerance for PBC safety
    lo = tolerance_angstrom / 2.0
    hi = L - lo

    polymer_constraints = (
        f"  inside box {lo:.2f} {lo:.2f} {lo:.2f} {hi:.2f} {hi:.2f} {hi:.2f}\n"
    )

    if packing_mode == "shell" and shell_only_angstrom is not None and shell_only_angstrom > 0:
        max_radius = half - lo - tolerance_angstrom
        if shell_only_angstrom < max_radius:
            polymer_constraints += (
                f"  outside sphere {half:.2f} {half:.2f} {half:.2f}"
                f" {shell_only_angstrom:.2f}\n"
            )
        else:
            warnings.warn(
                f"shell_only_angstrom ({shell_only_angstrom:.1f} Å) exceeds usable "
                f"radius ({max_radius:.1f} Å) for this box; ignoring shell constraint"
            )

    return (
        f"tolerance {tolerance_angstrom}\n"
        f"filetype pdb\n"
        f"output {output_path}\n"
        f"seed {seed}\n"
        f"maxit {maxit}\n"
        f"nloop {nloop}\n"
        f"movebadrandom\n"
        f"\n"
        f"structure {insulin_pdb_path}\n"
        f"  number 1\n"
        f"  center\n"
        f"  fixed {half:.2f} {half:.2f} {half:.2f} 0. 0. 0.\n"
        f"end structure\n"
        f"\n"
        f"structure {polymer_pdb_path}\n"
        f"  number {n_polymers}\n"
        f"{polymer_constraints}"
        f"end structure\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def pack_insulin_polymers(
    insulin_pdb_path: str,
    polymer_pdb_path: str,
    n_polymers: int,
    output_path: str,
    box_size_nm: Optional[float] = None,
    tolerance_angstrom: float = 2.0,
    seed: int = 42,
    timeout_s: int = 300,
    shell_only_angstrom: Optional[float] = None,
    packing_mode: PackingMode = "bulk",
    padding_angstrom: float = 6.0,
    maxit: int = 20,
    nloop: int = 200,
) -> dict:
    """
    Pack insulin and *n_polymers* polymer chains into a cubic box with Packmol.

    Insulin is centred in the box and held fixed.  Polymers are placed
    around it subject to the chosen *packing_mode* and Packmol's overlap
    tolerance.

    Parameters
    ----------
    insulin_pdb_path : str
        Path to insulin PDB (should include hydrogens).
    polymer_pdb_path : str
        Path to a single polymer chain PDB.
    n_polymers : int
        Number of polymer copies to pack.
    output_path : str
        Destination path for the packed output PDB.
    box_size_nm : float or None
        Cubic box edge in nm.  ``None`` (default) triggers auto-sizing
        based on molecular content and a target packing density.
    tolerance_angstrom : float
        Minimum inter-molecular distance (Å).
    seed : int
        Random seed for Packmol.
    timeout_s : int
        Subprocess timeout in seconds.
    shell_only_angstrom : float or None
        Exclusion-sphere radius (Å) for **shell** mode.
    packing_mode : ``"bulk"`` or ``"shell"``
        Packing strategy.
    padding_angstrom : float
        Extra clearance (Å) per side when auto-sizing the box.
    maxit : int
        Packmol ``maxit`` (optimisation iterations per loop).
    nloop : int
        Packmol ``nloop`` (number of packing attempts).

    Returns
    -------
    dict
        ``{"success": bool, "box_edge_angstrom": float, "box_edge_nm": float,
        "stdout": str, "stderr": str}``
    """
    fail = {
        "success": False,
        "box_edge_angstrom": 0.0,
        "box_edge_nm": 0.0,
        "stdout": "",
        "stderr": "",
    }

    # --- Validate environment -------------------------------------------------
    if not _packmol_available():
        warnings.warn("packmol not found on PATH.  Install via: pip install packmol")
        fail["stderr"] = "packmol not found"
        return fail

    insulin_pdb_path = str(Path(insulin_pdb_path).resolve())
    polymer_pdb_path = str(Path(polymer_pdb_path).resolve())
    output_path = str(Path(output_path).resolve())

    for label, path in [("Insulin PDB", insulin_pdb_path),
                        ("Polymer PDB", polymer_pdb_path)]:
        if not Path(path).is_file():
            warnings.warn(f"{label} not found: {path}")
            fail["stderr"] = f"{label} not found: {path}"
            return fail

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # --- Determine box size ---------------------------------------------------
    if box_size_nm is not None:
        box_edge_A = box_size_nm * 10.0
    else:
        box_edge_A = estimate_box_edge_angstrom(
            insulin_pdb_path,
            polymer_pdb_path,
            n_polymers,
            tolerance_angstrom=tolerance_angstrom,
            padding_angstrom=padding_angstrom,
        )

    # --- Shell-mode radius validation -----------------------------------------
    if packing_mode == "shell" and shell_only_angstrom is not None and shell_only_angstrom > 0:
        lo = tolerance_angstrom / 2.0
        max_radius = box_edge_A / 2.0 - lo - tolerance_angstrom
        if shell_only_angstrom >= max_radius:
            warnings.warn(
                f"shell_only_angstrom ({shell_only_angstrom:.1f} Å) too large for "
                f"box edge ({box_edge_A:.1f} Å); disabling shell constraint"
            )
            shell_only_angstrom = None

    # --- Build Packmol input --------------------------------------------------
    inp_content = build_packmol_inp_content(
        insulin_pdb_path=insulin_pdb_path,
        polymer_pdb_path=polymer_pdb_path,
        n_polymers=n_polymers,
        output_path=output_path,
        box_edge_angstrom=box_edge_A,
        tolerance_angstrom=tolerance_angstrom,
        seed=seed,
        shell_only_angstrom=shell_only_angstrom,
        packing_mode=packing_mode,
        maxit=maxit,
        nloop=nloop,
    )

    # --- Run Packmol ----------------------------------------------------------
    inp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".inp", delete=False, encoding="utf-8"
        ) as f:
            f.write(inp_content)
            inp_path = f.name

        packmol_exe = shutil.which("packmol")
        with open(inp_path, encoding="utf-8") as inp_file:
            result = subprocess.run(
                [packmol_exe],
                stdin=inp_file,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(Path(output_path).parent),
            )

        success = result.returncode == 0 and Path(output_path).is_file()
        if not success:
            warnings.warn(
                f"Packmol failed (exit {result.returncode}):\n"
                f"{result.stderr or result.stdout}"
            )

        return {
            "success": success,
            "box_edge_angstrom": box_edge_A,
            "box_edge_nm": box_edge_A / 10.0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        warnings.warn(f"Packmol timed out after {timeout_s} s")
        return {
            "success": False,
            "box_edge_angstrom": box_edge_A,
            "box_edge_nm": box_edge_A / 10.0,
            "stdout": "",
            "stderr": "timeout",
        }
    except Exception as e:
        warnings.warn(f"Packmol error: {e}")
        return {
            "success": False,
            "box_edge_angstrom": box_edge_A,
            "box_edge_nm": box_edge_A / 10.0,
            "stdout": "",
            "stderr": str(e),
        }
    finally:
        if inp_path is not None:
            try:
                os.unlink(inp_path)
            except OSError:
                pass


def pack_insulin_polymers_progressive(
    insulin_pdb_path: str,
    polymer_pdb_path: str,
    n_polymers_start: int,
    output_path: str,
    *,
    n_polymers_cap: Optional[int] = None,
    per_attempt_timeout_s: float = 120.0,
    max_total_seconds: Optional[float] = None,
    seed: int = 42,
    **kwargs,
) -> Dict:
    """
    Greedily increase chain count until Packmol fails, times out, or effort limits hit.

    Starts at *n_polymers_start*, then tries *n*+1, *n*+2, … while each attempt succeeds
    within *per_attempt_timeout_s*. Stops when an increment fails (or times out), when
    *n_polymers_cap* is reached, or when *max_total_seconds* of wall time (cumulative)
    is exceeded before starting the next attempt.

    The last **successful** packed PDB is left at *output_path*.

    Parameters
    ----------
    kwargs
        Forwarded to :func:`pack_insulin_polymers` (e.g. ``box_size_nm``, ``packing_mode``,
        ``tolerance_angstrom``, ``shell_only_angstrom``, ``maxit``, ``nloop``). Do not pass
        ``n_polymers``, ``output_path``, ``timeout_s``, or ``seed`` here.

    Returns
    -------
    dict
        ``success``, ``n_polymers`` (final), ``n_polymers_start``, ``stopped_reason``,
        ``attempts``, ``total_pack_seconds``, plus box/stderr fields from the last successful
        attempt (or the last failed attempt if nothing succeeded).
    """
    out_path = str(Path(output_path).resolve())
    n0 = max(1, int(n_polymers_start))
    n = n0
    best: Optional[Dict] = None
    best_n: Optional[int] = None
    t0 = time.perf_counter()
    attempts = 0
    stopped_reason: Optional[str] = None
    last_fail: Optional[Dict] = None

    pack_kw = dict(kwargs)
    pack_kw.pop("n_polymers", None)
    pack_kw.pop("output_path", None)
    pack_kw.pop("timeout_s", None)
    pack_kw.pop("seed", None)

    while True:
        if n_polymers_cap is not None and n > int(n_polymers_cap):
            stopped_reason = "n_cap"
            break
        if max_total_seconds is not None and (time.perf_counter() - t0) >= float(
            max_total_seconds
        ):
            stopped_reason = "total_time_budget" if best_n is not None else "total_time_budget_no_success"
            break

        attempts += 1
        r = pack_insulin_polymers(
            insulin_pdb_path,
            polymer_pdb_path,
            n,
            out_path,
            timeout_s=int(max(1, per_attempt_timeout_s)),
            seed=int(seed) + n,
            **pack_kw,
        )
        if not r.get("success"):
            last_fail = r
            stopped_reason = "increment_failed_or_timeout"
            break

        best = r
        best_n = n
        n += 1

    if best_n is None:
        fail = last_fail or best or {}
        return {
            "success": False,
            "n_polymers": 0,
            "n_polymers_start": n0,
            "stopped_reason": stopped_reason or "no_initial_success",
            "attempts": attempts,
            "total_pack_seconds": time.perf_counter() - t0,
            "box_edge_angstrom": float(fail.get("box_edge_angstrom", 0.0)),
            "box_edge_nm": float(fail.get("box_edge_nm", 0.0)),
            "stdout": fail.get("stdout", "") or "",
            "stderr": fail.get("stderr", "") or "",
        }

    assert best is not None and best_n is not None
    total_pack_seconds = time.perf_counter() - t0
    reason = stopped_reason or "maximized_under_effort"
    return {
        "success": True,
        "n_polymers": best_n,
        "n_polymers_start": n0,
        "stopped_reason": reason,
        "attempts": attempts,
        "total_pack_seconds": total_pack_seconds,
        "box_edge_angstrom": float(best["box_edge_angstrom"]),
        "box_edge_nm": float(best["box_edge_nm"]),
        "stdout": best.get("stdout", "") or "",
        "stderr": best.get("stderr", "") or "",
    }
