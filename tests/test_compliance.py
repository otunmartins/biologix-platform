"""Tests for excipient compliance service."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))

from biologix_ai.services.compliance_service import check_excipient_compliance, ComplianceResult


def test_peg_is_approved_fda_ema():
    result = check_excipient_compliance("[*]OCC[*]", jurisdiction="FDA,EMA")
    assert result.overall_status in ("approved", "flagged")
    assert "FDA" in result.jurisdictions_matched or "EMA" in result.jurisdictions_matched
    assert result.approved_name is not None


def test_peg_gras():
    result = check_excipient_compliance("[*]OCC[*]", jurisdiction="FDA")
    assert result.gras is True


def test_peg_immunogenicity_anti_peg_flag():
    result = check_excipient_compliance("[*]OCC[*]", check_immunogenicity=True)
    names = [f["name"] for f in result.immunogenicity_flags]
    assert "anti_PEG_ether_repeat" in names


def test_unknown_psmiles_no_match():
    result = check_excipient_compliance("[*]C(=O)NC([*])(CC)C", jurisdiction="FDA,EMA")
    assert result.overall_status in ("no_match", "flagged")
    assert not result.jurisdictions_matched


def test_to_dict_serialisable():
    import json
    result = check_excipient_compliance("[*]OCC[*]")
    d = result.to_dict()
    json.dumps(d)  # must not raise
    assert "psmiles" in d
    assert "overall_status" in d


def test_plga_approved():
    result = check_excipient_compliance("[*]OC(=O)C(C)OC(=O)C[*]", jurisdiction="FDA")
    assert result.overall_status in ("approved", "flagged")


def test_jurisdiction_filter():
    # PLGA in EMA only
    result = check_excipient_compliance("[*]OC(=O)C(C)OC(=O)C[*]", jurisdiction="EMA")
    assert isinstance(result, ComplianceResult)
