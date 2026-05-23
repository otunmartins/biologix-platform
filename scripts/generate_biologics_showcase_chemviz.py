#!/usr/bin/env python3
"""Build showcase chemviz panel for paper/biologics (insulin + adalimumab campaigns).

Layout:
  Row 0: PyMOL complex renderings (lead candidate per biologic)
  Row 1: Top-3 monomer 2D structures with E_int labels

Usage:
  python scripts/generate_biologics_showcase_chemviz.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "python"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from biologix_ai.simulation.pymol_complex_viz import write_complex_pymol_png

INSULIN_RUN = ROOT / "runs" / "insulin-stabilize-iter1"
ADALIMUMAB_RUN = ROOT / "runs" / "adalimumab-stabilization-iter1"
OUT_DIR = ROOT / "paper" / "biologics" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INSULIN_COMPLEX_PDB = INSULIN_RUN / "structures" / "Candidate_0_complex_minimized.pdb"
ADALIMUMAB_COMPLEX_PDB = ADALIMUMAB_RUN / "structures" / "Candidate_4_complex_minimized.pdb"


def _count_protein_atoms_before_unk(pdb: Path) -> int:
    """OpenMM matrix PDBs: native protein first, polymer UNK residues after."""
    n = 0
    for line in pdb.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        if line[17:20].strip() == "UNK":
            break
        n += 1
    if n <= 0:
        raise RuntimeError(f"no protein atoms before UNK in {pdb}")
    return n

TOP3 = {
    "insulin": [
        ("[*]OC(=O)OC(C)C(=O)[*]", "PC-alt-lactide", r"$-1787.6$ kJ/mol"),
        ("[*]OC(=O)OCC(CO)[*]", "Hydroxy-PC", r"$-1699.4$ kJ/mol"),
        ("[*]OC(=O)OCCC(=O)[*]", "PC-alt-3HP", r"$-1434.0$ kJ/mol"),
    ],
    "adalimumab": [
        ("[*]CCC(=O)NC([*])=O", "Poly(amide-ketone)", r"$-3337.1$ kJ/mol"),
        ("[*]CC(=O)CC[*]", "RND_000", r"$-2408.4$ kJ/mol"),
        ("CCN1CCCC1=O", "PVP (fragment)", r"$-2101.6$ kJ/mol"),
    ],
}


def save_monomer_rdkit(psmiles: str, output_path: Path, size: int = 400) -> None:
    from rdkit import Chem
    from rdkit.Chem import AllChem, Draw

    mol = Chem.MolFromSmiles(psmiles)
    if mol is None:
        raise RuntimeError(f"RDKit parse failed: {psmiles}")
    AllChem.Compute2DCoords(mol)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Draw.MolToFile(mol, str(output_path), size=(size, size))


def ensure_chemviz(pdb: Path, out: Path, *, force: bool = False) -> Path:
    if out.is_file() and not force:
        return out
    if not pdb.is_file():
        raise FileNotFoundError(f"PDB not found: {pdb}")
    n_prot = _count_protein_atoms_before_unk(pdb)
    print(f"  [RENDER] {pdb.name} -> {out.name} (n_protein_atoms={n_prot})")
    r = write_complex_pymol_png(
        str(pdb),
        str(out),
        n_protein_atoms=n_prot,
        width=1600,
        height=1600,
        timeout_s=300.0,
    )
    if not r.get("ok"):
        raise RuntimeError(
            f"PyMOL render failed for {pdb}: {r.get('error')}\n"
            "Install pymol on PATH (e.g. conda-forge pymol or pip pymol-open-source)."
        )
    return out


def compose(*, force: bool = False) -> tuple[Path, Path]:
    print("=== Chemviz: insulin lead complex ===")
    insulin_chem = ensure_chemviz(
        INSULIN_COMPLEX_PDB,
        OUT_DIR / "insulin_lead_complex_chemviz.png",
        force=force,
    )
    print("=== Chemviz: adalimumab lead complex ===")
    ada_chem = ensure_chemviz(
        ADALIMUMAB_COMPLEX_PDB,
        OUT_DIR / "adalimumab_lead_complex_chemviz.png",
        force=force,
    )

    print("=== Monomer PNGs ===")
    mono_paths: dict[str, list[Path]] = {"insulin": [], "adalimumab": []}
    for camp, entries in TOP3.items():
        for i, (psm, _label, _e) in enumerate(entries):
            p = OUT_DIR / f"monomer_{camp}_{i}.png"
            if not p.is_file() or force:
                print(f"  [DRAW] {camp} rank {i + 1}")
                save_monomer_rdkit(psm, p)
            mono_paths[camp].append(p)

    def _build():
        fig = plt.figure(figsize=(7.2, 6.8), dpi=300)
        gs = GridSpec(
            2,
            2,
            figure=fig,
            height_ratios=[1.15, 0.55],
            hspace=0.08,
            wspace=0.06,
            left=0.03,
            right=0.97,
            top=0.94,
            bottom=0.06,
        )
        col_titles = ["Insulin (4F1C)", "Adalimumab (3WD5)"]
        chem_paths = [insulin_chem, ada_chem]
        for col, title in enumerate(col_titles):
            ax = fig.add_subplot(gs[0, col])
            ax.imshow(mpimg.imread(str(chem_paths[col])))
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(title, fontsize=8, fontfamily="serif", fontweight="bold", pad=4)
            for spine in ax.spines.values():
                spine.set_linewidth(0.3)
                spine.set_color("gray")

        mono_gs = gs[1, :].subgridspec(1, 6, wspace=0.12)
        idx = 0
        for camp in ("insulin", "adalimumab"):
            for rank, (_psm, label, energy) in enumerate(TOP3[camp]):
                ax = fig.add_subplot(mono_gs[0, idx])
                ax.imshow(mpimg.imread(str(mono_paths[camp][rank])))
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_linewidth(0.0)
                ax.set_xlabel(f"{label}\n{energy}", fontsize=4.5, fontfamily="serif", labelpad=2)
                idx += 1
        return fig

    png_path = OUT_DIR / "showcase_chemviz_panel.png"
    pdf_path = OUT_DIR / "showcase_chemviz_panel.pdf"
    fig = _build()
    fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0.03, facecolor="white")
    plt.close(fig)
    fig2 = _build()
    fig2.savefig(pdf_path, dpi=300, bbox_inches="tight", pad_inches=0.03, facecolor="white")
    plt.close(fig2)
    print(f"Saved {png_path}")
    print(f"Saved {pdf_path}")
    return png_path, pdf_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    compose(force=args.force)
