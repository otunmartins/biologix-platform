#!/usr/bin/env python3
"""
Render insulin cartoon + polymer ball-and-stick PNGs from OpenMM complex PDBs (PyMOL only).

Requires ``pymol`` on PATH (open-source: conda-forge or ``pip install pymol-open-source``).

  python scripts/render_complex_chemviz.py runs/.../structures/Candidate_0_complex_minimized.pdb
  python scripts/render_complex_chemviz.py runs/.../structures/   # all *_complex_minimized.pdb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ribbon + ball-stick PNG from complex PDB")
    ap.add_argument(
        "path",
        help="PDB file or directory containing *_complex_minimized.pdb",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="",
        help="Output PNG path (default: next to PDB with _chemviz.png)",
    )
    ap.add_argument(
        "--protein-chains",
        default="A,B",
        help="Comma-separated protein chains for CA ribbon (default: A,B)",
    )
    args = ap.parse_args()
    p = Path(args.path).expanduser().resolve()
    chains = tuple(c.strip() for c in args.protein_chains.split(",") if c.strip())

    from insulin_ai.simulation.pymol_complex_viz import write_complex_pymol_png

    pdbs: list[Path] = []
    if p.is_file() and p.suffix.lower() == ".pdb":
        pdbs = [p]
    elif p.is_dir():
        pdbs = sorted(p.glob("*_complex_minimized.pdb"))
        if not pdbs:
            pdbs = sorted(p.glob("*.pdb"))
    else:
        print(f"Not a PDB file or directory: {p}", file=sys.stderr)
        return 1

    if not pdbs:
        print("No PDB files found.", file=sys.stderr)
        return 1

    out_one = Path(args.output).resolve() if args.output.strip() else None
    rc = 0
    for pdb in pdbs:
        if out_one and len(pdbs) == 1:
            outp = out_one
        else:
            outp = pdb.with_name(pdb.stem.replace("_complex_minimized", "") + "_complex_chemviz.png")
            if "_complex_minimized" not in pdb.stem:
                outp = pdb.with_suffix("").with_name(pdb.stem + "_chemviz.png").with_suffix(".png")
        r = write_complex_pymol_png(str(pdb), str(outp), protein_chains=chains)
        if not r.get("ok"):
            print(f"{pdb.name}: {r.get('error')}", file=sys.stderr)
            rc = 1
            continue
        print(r.get("path"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
