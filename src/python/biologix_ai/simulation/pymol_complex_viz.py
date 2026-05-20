#!/usr/bin/env python3
"""
PyMOL (open-source) rendering for insulin–polymer complexes:

- **Protein (insulin):** cartoon ribbon with ``dss``-assigned secondary structure
  (helix/sheet/coil).
- **Polymer:** ball-and-stick (``stick_ball`` mode).

Requires the ``pymol`` executable on ``PATH`` (e.g. ``conda install -c conda-forge pymol``
or ``pip install pymol-open-source``). Headless: ``pymol -c``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


def pymol_available() -> bool:
    return shutil.which("pymol") is not None


def _pml_path_str(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def build_pymol_complex_script(
    pdb_path: Path,
    png_path: Path,
    *,
    n_protein_atoms: Optional[int],
    protein_chains: Sequence[str] = ("A", "B"),
    width: int = 1600,
    height: int = 1600,
    ray: int = 1,
) -> str:
    """
    PyMOL command script: load PDB, cartoon + DSS for protein, sticks for polymer.

    Protein selection: ``index 1–N`` when *n_protein_atoms* is set (OpenMM matrix order:
    protein first); otherwise ``chain A`` / ``chain B`` union.
    """
    pdb_s = _pml_path_str(pdb_path)
    png_s = _pml_path_str(png_path)

    if n_protein_atoms is not None and int(n_protein_atoms) > 0:
        n = int(n_protein_atoms)
        prot_expr = f"m and index 1-{n}"
    else:
        chs = " or ".join(f"chain {c}" for c in protein_chains if c)
        prot_expr = f"m and ({chs})" if chs else "m and none"

    # Object name `m`; keep selections explicit for batch safety.
    lines = [
        "reinitialize",
        f"load {pdb_s}, m",
        "hide everything",
        f"select prot, {prot_expr}",
        "select poly, m and not prot",
        "dss prot",
        "show cartoon, prot",
        "cartoon automatic, prot",
        "show sticks, poly",
        "set stick_ball, 1",
        "set stick_radius, 0.12",
        "set sphere_scale, 0.22",
        "color marine, prot",
        "color grey, elem C and poly",
        "color red, elem O and poly",
        "color blue, elem N and poly",
        "color yellow, elem S and poly",
        "bg_color white",
        "set ray_opaque_background, 1",
        "set antialias, 2",
        "set ray_shadows, 0",
        "orient",
        "zoom complete=1",
        f"png {png_s}, width={width}, height={height}, dpi=150, ray={ray}",
        "quit",
    ]
    return "\n".join(lines) + "\n"


def write_complex_pymol_png(
    pdb_path: str,
    output_path: str,
    *,
    n_protein_atoms: Optional[int] = None,
    protein_chains: Sequence[str] = ("A", "B"),
    width: int = 1600,
    height: int = 1600,
    timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """
    Ray-traced PNG via PyMOL batch (``pymol -c``).

    Parameters
    ----------
    n_protein_atoms
        Number of leading atoms in the PDB that belong to insulin (matrix minimize output
        order). Strongly recommended for correct protein vs polymer split.
    protein_chains
        Used only when *n_protein_atoms* is unset: cartoon for these chains, sticks for
        the rest.
    """
    path = Path(pdb_path)
    out = Path(output_path)
    if out.suffix.lower() != ".png":
        out = out.with_suffix(".png")

    if not pymol_available():
        return {
            "ok": False,
            "error": "pymol not on PATH (install conda-forge::pymol or pip pymol-open-source)",
            "backend": "pymol",
        }
    if not path.is_file():
        return {"ok": False, "error": f"PDB not found: {path}", "backend": "pymol"}

    path_input = path
    tmp_unwrap: Optional[Path] = None
    try:
        from .pbc_unwrap import preprocess_pdb_path_for_pymol_viz

        pre = preprocess_pdb_path_for_pymol_viz(
            path, n_protein_atoms=n_protein_atoms
        )
        if pre is not None:
            path_input = pre
            tmp_unwrap = pre
    except Exception:
        path_input = path

    out.parent.mkdir(parents=True, exist_ok=True)
    script = build_pymol_complex_script(
        path_input,
        out,
        n_protein_atoms=n_protein_atoms,
        protein_chains=protein_chains,
        width=width,
        height=height,
    )

    pymol_exe = shutil.which("pymol")
    assert pymol_exe is not None
    env = os.environ.copy()
    env.setdefault("PYMOL_HEADLESS", "1")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".pml", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(script)
        pml_path = tf.name

    try:
        proc = subprocess.run(
            [pymol_exe, "-c", pml_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"PyMOL timed out after {timeout_s}s",
            "backend": "pymol",
        }
    except OSError as e:
        return {"ok": False, "error": str(e), "backend": "pymol"}
    finally:
        try:
            os.unlink(pml_path)
        except OSError:
            pass
        if tmp_unwrap is not None:
            try:
                tmp_unwrap.unlink(missing_ok=True)
            except OSError:
                pass

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return {
            "ok": False,
            "error": f"pymol exit {proc.returncode}: {tail}",
            "backend": "pymol",
        }
    if not out.is_file():
        return {
            "ok": False,
            "error": f"PNG not written after pymol: {out}",
            "backend": "pymol",
        }
    return {
        "ok": True,
        "path": str(out.resolve()),
        "backend": "pymol",
        "n_protein_atoms": n_protein_atoms,
    }


def write_complex_viz_png_auto(
    pdb_path: str,
    output_path: str,
    *,
    n_protein_atoms: Optional[int] = None,
    protein_chains: Sequence[str] = ("A", "B"),
) -> Tuple[Dict[str, Any], str]:
    """
    Render ``*_complex_chemviz.png`` using **only** open-source PyMOL (``pymol -c``).

    There is no matplotlib fallback: install ``pymol`` on PATH or the call returns
    ``ok: false`` with an error message.

    Returns ``(result_dict, "pymol")``.
    """
    r = write_complex_pymol_png(
        pdb_path,
        output_path,
        n_protein_atoms=n_protein_atoms,
        protein_chains=protein_chains,
    )
    return r, "pymol"
