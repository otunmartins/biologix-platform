"""Tests for PSMILES validation."""

import sys
import types

import pytest

from insulin_ai.material_mappings import validate_psmiles


def test_validate_rejects_empty():
    assert validate_psmiles("")["valid"] is False
    assert validate_psmiles("CC")["valid"] is False


def test_validate_canonicalize_as_property():
    """
    New psmiles API: .canonicalize is a property (str).
    Calling .canonicalize() raises 'PolymerSmiles' object is not callable.
    """

    class FakePolymerSmiles:
        def __init__(self, _s: str) -> None:
            self.canonicalize = "[*]OCC[*]"

    mod = types.ModuleType("psmiles")
    mod.PolymerSmiles = FakePolymerSmiles
    sys.modules["psmiles"] = mod
    try:
        r = validate_psmiles("[*]OCC[*]")
        assert r["valid"] is True
        assert r["canonical"] == "[*]OCC[*]"
    finally:
        del sys.modules["psmiles"]


def test_validate_rdkit_fallback_no_psmiles():
    pytest.importorskip("rdkit")
    """If psmiles missing, RDKit path still validates capped SMILES."""
    # Ensure psmiles not used
    saved = sys.modules.pop("psmiles", None)
    try:
        r = validate_psmiles("[*]CC[*]")
        assert r["valid"] is True
    finally:
        if saved is not None:
            sys.modules["psmiles"] = saved
