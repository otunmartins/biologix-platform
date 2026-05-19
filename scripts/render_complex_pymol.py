#!/usr/bin/env python3
"""
Re-render insulin+polymer complex PNGs with PyMOL (ribbon + DSS for protein, sticks for polymer).

Usage:
  mamba activate insulin-ai-sim   # env with pymol on PATH
  python scripts/render_complex_pymol.py runs/insulin_patch_iter1_1/structures

Counts insulin atoms as chain A+B in each PDB (same idea as the matrix viewer when
``n_insulin_atoms`` is inferred). Writes ``<stem>_pymol.png`` beside each PDB.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_pymol_viz():
    """Load ``pymol_complex_viz`` without importing ``insulin_ai.simulation`` (RDKit, etc.)."""
    path = REPO / "src" / "python" / "insulin_ai" / "simulation" / "pymol_complex_viz.py"
    spec = importlib.util.spec_from_file_location("pymol_complex_viz", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_chain_ab_atoms(pdb: Path) -> int:
    n = 0
    for line in pdb.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        if line[21:22] in ("A", "B"):
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "path",
        help="Directory containing *_complex_minimized.pdb or a single .pdb file",
    )
    ap.add_argument(
        "--suffix",
        default="_pymol.png",
        help="Output suffix (default: _pymol.png → Candidate_0_complex_minimized_pymol.png)",
    )
    args = ap.parse_args()
    pv = _load_pymol_viz()
    pymol_available = pv.pymol_available
    write_complex_pymol_png = pv.write_complex_pymol_png

    if not pymol_available():
        print("ERROR: pymol not on PATH. Install: conda install -c conda-forge pymol", file=sys.stderr)
        sys.exit(2)

    target = Path(args.path).resolve()
    pdbs: list[Path]
    if target.is_file() and target.suffix.lower() == ".pdb":
        pdbs = [target]
    elif target.is_dir():
        pdbs = sorted(target.glob("Candidate_*_complex_minimized.pdb"))
        if not pdbs:
            pdbs = sorted(target.glob("*.pdb"))
    else:
        print(f"Not a file or directory: {target}", file=sys.stderr)
        sys.exit(1)

    if not pdbs:
        print(f"No PDB files found under {target}", file=sys.stderr)
        sys.exit(1)

    ok = 0
    for pdb in pdbs:
        n = _count_chain_ab_atoms(pdb)
        out = pdb.with_name(pdb.stem + args.suffix)
        r = write_complex_pymol_png(str(pdb), str(out), n_protein_atoms=n, timeout_s=300.0)
        if r.get("ok"):
            print(f"OK {pdb.name} -> {out.name} (n_prot={n})")
            ok += 1
        else:
            print(f"FAIL {pdb.name}: {r.get('error')}", file=sys.stderr)
    print(f"Done: {ok}/{len(pdbs)}")
    sys.exit(0 if ok == len(pdbs) else 1)


if __name__ == "__main__":
    main()
