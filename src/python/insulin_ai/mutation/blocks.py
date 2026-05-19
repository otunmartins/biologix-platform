#!/usr/bin/env python3
"""
PSMILES building blocks for polymer material mutation.

From FRIDGEFREENET proposal.tex: random blocks and functional group variants
for systematic chemical space exploration.
"""

_RANDOM_BLOCKS = [
    "[*]CC(=O)[*]",           # Ketone
    "[*]C(=O)O[*]",           # Carboxyl
    "[*]NC(=O)[*]",           # Amide
    "[*]c1ccc(cc1)[*]",       # Aromatic
    "[*]CC(C)[*]",            # Branched alkyl
    "[*]C(=O)N[*]",           # Alternative amide
    "[*]OC(=O)[*]",           # Ester
    "[*]CCOC(=O)[*]",         # Extended ester
    "[*]NC(C)C(=O)[*]",       # Amino acid-like
    "[*]C(=O)CCC(=O)[*]",     # Diketone for crosslinking
    "[*]C(O)C[*]",            # Hydroxyl
    "[*]C(F)(F)[*]",          # Fluorinated
    "[*]SC[*]",               # Sulfur-containing
    "[*]C(=S)[*]",            # Thiocarbonyl
    "[*]OCC[*]",              # PEG-like
    "[*]CC[*]",               # Polyethylene-like
]

_FUNCTIONAL_GROUPS = {
    "hydroxyl": "[*]C(O)[*]",
    "carboxyl": "[*]C(=O)O[*]",
    "amine": "[*]C(N)[*]",
    "amide": "[*]C(=O)N[*]",
    "carbonyl": "[*]C(=O)[*]",
    "ester": "[*]C(=O)OC[*]",
    "ether": "[*]COC[*]",
    "alkene": "[*]C=C[*]",
    "alkyne": "[*]C#C[*]",
    "haloalkane": "[*]C(Cl)[*]",
    "aromatic": "[*]c1ccc(cc1)[*]",
}


def get_random_blocks():
    """Return list of PSMILES polymer blocks for random exploration."""
    return list(_RANDOM_BLOCKS)


def get_functional_groups():
    """Return dict of functional group name -> PSMILES."""
    return dict(_FUNCTIONAL_GROUPS)


def get_all_blocks():
    """Return combined set of blocks for maximum diversity."""
    return list(_RANDOM_BLOCKS) + list(_FUNCTIONAL_GROUPS.values())
