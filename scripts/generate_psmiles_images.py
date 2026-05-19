#!/usr/bin/env python3
"""
Generate PNG images for PSMILES structures using the psmiles package.
Uses psmiles PolymerSmiles.savefig() method.
"""

import os
from pathlib import Path

# Top materials with their PSMILES
MATERIALS = {
    # Tier 1: Literature-backed
    "PLA": ("[*]OC(=O)C([*])C", "Literature-Backed"),
    "PEG-PLA_diblock": ("[*]OCOC(=O)C([*])C", "Literature-Backed"),
    "PLA-PEG-PLA_triblock": ("[*]OCCOC(=O)C(C)OOC(=O)C([*])C", "Literature-Backed"),
    "Poly-SPB": ("[*]CCS(=O)(=O)CC[N+]([*])(C)C", "Literature-Backed"),
    # Tier 2: Mutation-discovered
    "Dithioamide": ("[*]C(=S)C([*])=S", "Mutation-Discovered"),
    "Biphenyl_aromatic": ("[*]c1ccc(c2ccc([*])cc2)cc1", "Mutation-Discovered"),
    "Fluorinated": ("[*]C(F)(F)[*]", "Mutation-Discovered"),
    "Carbonate": ("[*]C(=O)O[*]", "Mutation-Discovered"),
    "Adipic_acid": ("[*]CCC(=O)C([*])=O", "Mutation-Discovered"),
    "Dithiol": ("[*]CSSC[*]", "Mutation-Discovered"),
    "Glycerol": ("[*]C(O)C[*]", "Mutation-Discovered"),
    "PEG_monomer": ("[*]OCC[*]", "Mutation-Discovered"),
    "Benzene": ("[*]c1ccc([*])cc1", "Mutation-Discovered"),
}


def main():
    from psmiles import PolymerSmiles as PS

    # Create output directory
    output_dir = Path("runs/insulin-patch-autonomous/structures")
    output_dir.mkdir(exist_ok=True)

    print(f"Generating molecule images in {output_dir}...\n")

    generated = []

    for name, (psmiles, category) in MATERIALS.items():
        print(f"Processing {name}...")
        print(f"  PSMILES: {psmiles}")

        try:
            ps = PS(psmiles)
            output_path = output_dir / f"{name}.png"

            # savefig() saves the figure to a file
            # We need to capture the figure or use savefig directly
            ps.savefig(str(output_path))

            print(f"  [OK] Saved to {output_path.name}")
            generated.append((name, psmiles, category, output_path))

        except Exception as e:
            print(f"  [ERROR] {name}: {e}")

    print(f"\nGenerated {len(generated)} images")

    # Create a combined grid image using matplotlib
    if generated:
        try:
            import matplotlib.pyplot as plt
            from PIL import Image

            n = len(generated)
            cols = min(4, n)
            rows = (n + cols - 1) // cols

            fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 4 * rows))
            if rows == 1 and cols == 1:
                axes = [[axes]]
            elif rows == 1 or cols == 1:
                axes = [axes] if rows == 1 else [[a] for a in axes]
            else:
                axes = axes if axes.ndim == 2 else [[a] for a in axes.flat]

            for idx, (name, psmiles, category, img_path) in enumerate(generated):
                row = idx // cols
                col = idx % cols

                try:
                    img = Image.open(img_path)
                    axes[row][col].imshow(img)
                    axes[row][col].set_title(f"{name}\n({category})", fontsize=10)
                except Exception as e:
                    axes[row][col].text(
                        0.5, 0.5, f"{name}\n[Error]", ha="center", va="center"
                    )
                axes[row][col].axis("off")

            # Hide unused subplots
            for idx in range(n, rows * cols):
                row = idx // cols
                col = idx % cols
                axes[row][col].axis("off")

            plt.tight_layout()
            grid_path = output_dir / "all_materials_grid.png"
            plt.savefig(grid_path, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Grid image saved: {grid_path}")

        except Exception as e:
            print(f"Could not create grid: {e}")

    # Generate markdown with embedded images
    md_path = output_dir / "MATERIALS_IMAGES.md"

    with open(md_path, "w") as f:
        f.write("# PSMILES Structure Images\n\n")
        f.write(
            "Generated from autonomous discovery campaign - insulin patch polymer materials.\n\n"
        )

        if generated:
            f.write("## All Materials Grid\n\n")
            f.write(f"![All Materials](all_materials_grid.png)\n\n")

        f.write("## Individual Structures\n\n")
        for name, psmiles, category, path in generated:
            f.write(f"### {name}\n\n")
            f.write(f"**Category:** {category}\n\n")
            f.write(f"**PSMILES:** `{psmiles}`\n\n")
            f.write(f"![{name}]({path.name})\n\n")
            f.write("---\n\n")

    print(f"\nMarkdown with images: {md_path}")

    return output_dir, generated


if __name__ == "__main__":
    main()
