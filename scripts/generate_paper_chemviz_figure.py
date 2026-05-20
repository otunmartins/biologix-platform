#!/usr/bin/env python3
"""
Generate the composite chemviz panel figure for the paper.

Steps:
  1. Render missing chemviz PNGs from PDB files using PyMOL.
  2. Generate monomer 2D PNGs for the top-3 candidates per campaign.
  3. Compose a single publication-quality figure:
       - 3×3 grid of complex chemviz images (rows = representative structures,
         columns = campaigns A/B/C).
       - Below: 3×3 strip of monomer 2D structures with labels.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.simulation.pymol_complex_viz import write_complex_pymol_png

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.gridspec import GridSpec
import numpy as np


def save_monomer_rdkit(psmiles: str, output_path: Path, size: int = 400) -> dict:
    """Render a PSMILES repeat unit to PNG via RDKit."""
    from rdkit import Chem
    from rdkit.Chem import Draw, AllChem

    smi = psmiles.replace("[*]", "[H]")
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return {"ok": False, "error": f"RDKit parse failed for {psmiles}"}
    AllChem.Compute2DCoords(mol)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = Draw.MolToImage(mol, size=(size, size))
    img.save(str(output_path))
    return {"ok": True, "path": str(output_path)}

RUNS = ROOT / "runs"

CAMPAIGNS = {
    "A": RUNS / "autonomous-25iter",
    "B": RUNS / "insulin_patch_autonomous_25iter",
    "C": RUNS / "insulin_patch_autonomous_25iter_1",
}

COMPLEX_INDICES = {
    "A": [0, 4, 9],
    "B": [0, 2, 5],
    "C": [0, 2, 5],
}

TOP3_MONOMERS = {
    "A": [
        ("[*]CNC(=O)NC([*])=O", "poly(NAc-DAE)", r"$-2263$ kJ/mol"),
        ("[*]C(NC(=O)N[*])C(=O)NO", "poly(NHAc-DAE)", r"$-2200$ kJ/mol"),
        ("[*]CNNC(=O)NC([*])=O", "poly(NAc-NMe-HAA)", r"$-2145$ kJ/mol"),
    ],
    "B": [
        ("[*]NC(Cc1cnc[nH]1)C([*])=O", "polyhistidine", r"$-1545$ kJ/mol"),
        ("[*]NC(CC(N)=O)C([*])=O", "polyasparagine", r"$-1535$ kJ/mol"),
        ("[*]OC1C([*])OC(CO)C(O)C1N", "chitosan", r"$-1460$ kJ/mol"),
    ],
    "C": [
        ("[*]OC(=O)C(O)C(O)C(O)C([*])C=O", "polygalact. acid", r"$-1765$ kJ/mol"),
        ("[*]OC1C(O)C(O)OC(C([*])=O)C1O", "polyglucur. acid", r"$-1574$ kJ/mol"),
        ("[*]NNC(=O)C([*])=O", "polyhydrazide", r"$-1496$ kJ/mol"),
    ],
}

OUTPUT_DIR = ROOT / "paper" / "figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def ensure_chemviz_png(campaign: str, idx: int, *, force: bool = False) -> Path:
    """Return path to chemviz PNG, generating via PyMOL if missing."""
    base = CAMPAIGNS[campaign] / "structures"
    png = base / f"Candidate_{idx}_complex_chemviz.png"
    if png.is_file() and not force:
        print(f"  [OK] {png.name} exists")
        return png
    pdb = base / f"Candidate_{idx}_complex_minimized.pdb"
    if not pdb.is_file():
        raise FileNotFoundError(f"PDB not found: {pdb}")
    print(f"  [RENDER] {pdb.name} -> {png.name}")
    r = write_complex_pymol_png(str(pdb), str(png), width=1600, height=1600)
    if not r.get("ok"):
        raise RuntimeError(f"PyMOL render failed for {pdb}: {r.get('error')}")
    return png


def ensure_monomer_png(
    psmiles: str, label: str, campaign: str, rank: int, *, force: bool = False
) -> Path:
    """Return path to monomer PNG, generating via psmiles if missing."""
    out = OUTPUT_DIR / f"monomer_{campaign}_{rank}.png"
    if out.is_file() and not force:
        print(f"  [OK] {out.name} exists")
        return out
    print(f"  [DRAW] {label.split(chr(10))[0]} -> {out.name}")
    r = save_monomer_rdkit(psmiles, out)
    if not r.get("ok"):
        raise RuntimeError(f"Monomer render failed for {label}: {r.get('error')}")
    return out


def compose_figure(*, force: bool = False):
    """Build the composite figure."""

    print("\n=== Step 1: Ensure chemviz PNGs ===")
    chemviz_paths: dict[str, list[Path]] = {}
    for camp in ("A", "B", "C"):
        print(f"\nCampaign {camp}:")
        chemviz_paths[camp] = []
        for idx in COMPLEX_INDICES[camp]:
            p = ensure_chemviz_png(camp, idx, force=force)
            chemviz_paths[camp].append(p)

    print("\n=== Step 2: Ensure monomer PNGs ===")
    monomer_paths: dict[str, list[Path]] = {}
    for camp in ("A", "B", "C"):
        print(f"\nCampaign {camp}:")
        monomer_paths[camp] = []
        for rank, (psm, label, _energy) in enumerate(TOP3_MONOMERS[camp]):
            p = ensure_monomer_png(psm, label, camp, rank, force=force)
            monomer_paths[camp].append(p)

    print("\n=== Step 3: Compose figure ===")

    n_complex_rows = 3
    n_cols = 3
    campaign_order = ("A", "B", "C")
    campaign_labels = ["Campaign A", "Campaign B", "Campaign C"]

    def _build_figure():
        # Single 4×3 grid: rows 0–2 = chemviz; row 3 = monomers (avoids a tall empty band
        # between nested GridSpecs). Tight hspace between chemviz and monomer strip.
        fig = plt.figure(figsize=(7.0, 9.2), dpi=300)

        gs = GridSpec(
            4,
            3,
            figure=fig,
            height_ratios=[1.0, 1.0, 1.0, 0.42],
            width_ratios=[1.0, 1.0, 1.0],
            hspace=0.05,
            wspace=0.04,
            left=0.02,
            right=0.98,
            top=0.97,
            bottom=0.05,
        )

        for col, camp in enumerate(campaign_order):
            for row in range(n_complex_rows):
                ax = fig.add_subplot(gs[row, col])
                img = mpimg.imread(str(chemviz_paths[camp][row]))
                ax.imshow(img)
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_linewidth(0.3)
                    spine.set_color("gray")
                if row == 0:
                    ax.set_title(
                        campaign_labels[col],
                        fontsize=7,
                        fontfamily="serif",
                        pad=3,
                        fontweight="bold",
                    )

        mono_gs = gs[3, :].subgridspec(1, 9, wspace=0.15)

        mono_idx = 0
        for camp in campaign_order:
            for rank in range(3):
                ax = fig.add_subplot(mono_gs[0, mono_idx])
                mono_img = mpimg.imread(str(monomer_paths[camp][rank]))
                ax.imshow(mono_img)
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_linewidth(0.0)

                _, label, energy = TOP3_MONOMERS[camp][rank]
                ax.set_xlabel(
                    f"{label}\n{energy}",
                    fontsize=4.2, fontfamily="serif",
                    labelpad=2,
                )
                mono_idx += 1

        return fig

    fig = _build_figure()
    out_path = OUTPUT_DIR / "chemviz_panel_figure.png"
    fig.savefig(out_path, dpi=300, bbox_inches="tight", pad_inches=0.03,
                facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"\n=== Figure saved: {out_path} ===")

    fig2 = _build_figure()
    pdf_path = OUTPUT_DIR / "chemviz_panel_figure.pdf"
    fig2.savefig(pdf_path, dpi=300, bbox_inches="tight", pad_inches=0.03,
                 facecolor="white", edgecolor="none")
    plt.close(fig2)
    print(f"=== PDF saved: {pdf_path} ===")

    return out_path, pdf_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build chemviz panel figure (PyMOL + matplotlib composite)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate chemviz and monomer PNGs even if they already exist.",
    )
    args = parser.parse_args()
    compose_figure(force=args.force)
