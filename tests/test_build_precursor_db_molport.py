"""Unit tests for Molport tier-3 parsing helpers."""

import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from scripts import build_precursor_db
from scripts.build_precursor_db import molport_smiles_from_tsv_line


def test_molport_smiles_from_tsv_line_header_skipped() -> None:
    assert molport_smiles_from_tsv_line("SMILES\tSMILES_CANONICAL\tMOLPORTID") is None


def test_molport_smiles_from_tsv_line_prefers_canonical_column() -> None:
    line = "C\tCCO\tMolport-000-000-000"
    assert molport_smiles_from_tsv_line(line) == "CCO"


def test_molport_smiles_from_tsv_line_blank_and_malformed() -> None:
    assert molport_smiles_from_tsv_line("") is None
    assert molport_smiles_from_tsv_line("no-tabs-here") is None


def test_tier_four_failure_aborts_database_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["build_precursor_db.py", "--tiers", "4"])
    monkeypatch.setattr(build_precursor_db, "verify_zinc_bridge", lambda: False)

    with pytest.raises(SystemExit) as error_info:
        build_precursor_db.main()

    assert error_info.value.code == 1
