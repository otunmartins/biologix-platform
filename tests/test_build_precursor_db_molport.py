"""Unit tests for Molport tier-3 parsing helpers."""

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from scripts.build_precursor_db import molport_smiles_from_tsv_line


def test_molport_smiles_from_tsv_line_header_skipped() -> None:
    assert molport_smiles_from_tsv_line("SMILES\tSMILES_CANONICAL\tMOLPORTID") is None


def test_molport_smiles_from_tsv_line_prefers_canonical_column() -> None:
    line = "C\tCCO\tMolport-000-000-000"
    assert molport_smiles_from_tsv_line(line) == "CCO"


def test_molport_smiles_from_tsv_line_blank_and_malformed() -> None:
    assert molport_smiles_from_tsv_line("") is None
    assert molport_smiles_from_tsv_line("no-tabs-here") is None
