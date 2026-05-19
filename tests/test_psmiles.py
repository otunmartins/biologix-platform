"""
Smoke tests for PSMILES generator.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_psmiles_generator_import():
    """PSMILESGenerator can be imported."""
    try:
        from insulin_ai.psmiles_generator import PSMILESGenerator
        assert PSMILESGenerator is not None
    except ImportError:
        import pytest
        pytest.skip("PSMILES dependencies not installed")


def test_psmiles_fallback_extraction():
    """Fallback extracts PSMILES from LLM response via pattern matching (no hardcoded mappings)."""
    try:
        from insulin_ai.psmiles_generator import PSMILESGenerator
        gen = PSMILESGenerator.__new__(PSMILESGenerator)
        # Extract from response text; regex matches strings starting with [A-Z] and containing [*]
        result = gen._fallback_psmiles_extraction("peg", "The PSMILES is C[*]CC[*] for polyethylene.")
        assert result is not None
        assert result["psmiles"] == "C[*]CC[*]"
        assert result.get("pattern") == "chemical_pattern"
    except ImportError:
        import pytest
        pytest.skip("PSMILES dependencies not installed")


def test_psmiles_basic_syntax_check():
    """Basic syntax check validates PSMILES."""
    try:
        from insulin_ai.psmiles_generator import PSMILESGenerator
        gen = PSMILESGenerator()
        valid = gen._basic_syntax_check("[*]OCC[*]")
        assert valid["valid"] is True
        assert valid["connection_count"] == 2
        
        invalid = gen._basic_syntax_check("C C C")  # spaces
        assert invalid["valid"] is False
        assert any("space" in str(e).lower() for e in invalid["errors"])
    except ImportError:
        import pytest
        pytest.skip("PSMILES dependencies not installed")
