"""Regression tests for PSMILES chemistry and LD50 unit handling."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))


def test_peg_dimer_is_head_to_tail_not_peroxide():
    from rdkit import Chem

    from biologix_ai.services.psmiles_service import dimerize_psmiles

    dimer = dimerize_psmiles("[*]OCC[*]")
    assert dimer == "[*]CCOCCO[*]"
    capped = dimer.replace("[*]", "[CH3]")
    molecule = Chem.MolFromSmiles(capped)
    assert molecule is not None
    assert molecule.GetNumAtoms() == 8
    assert not molecule.HasSubstructMatch(Chem.MolFromSmarts("[OX2][OX2]"))


def test_similarity_and_fingerprint_use_installed_psmiles_api():
    from biologix_ai.services.psmiles_service import fingerprint_psmiles, similarity_psmiles

    similarity = similarity_psmiles("[*]OCC[*]", "[*]OCC[*]")
    assert similarity == 1.0
    fingerprint = fingerprint_psmiles("[*]OCC[*]", "rdkit")
    assert len(fingerprint) > 20


def test_peg_retrosynthesis_target_is_not_ethanol():
    from biologix_ai.retrosynthesis.psmiles_bridge import psmiles_to_smiles_target

    smiles = psmiles_to_smiles_target("[*]OCC[*]")
    assert smiles != "CCO"
    assert "O" in smiles


def test_ld50_log_value_is_not_compared_with_a_mg_threshold():
    from biologix_ai.services.toxicity_service import _profile_from_predictions

    mild = _profile_from_predictions("CCO", {"LD50_Zhu": 1.5})
    assert not any(flag.startswith("LD50") for flag in mild.flags)

    toxic = _profile_from_predictions("CCO", {"LD50_Zhu": 4.0})
    assert any(flag.startswith("LD50") for flag in toxic.flags)


def test_md_status_reports_openmm_without_a_runner_attribute(monkeypatch):
    from biologix_ai.simulation import openmm_compat

    monkeypatch.setattr(openmm_compat, "openmm_available", lambda: True)
    monkeypatch.setattr(openmm_compat, "packmol_available", lambda: True)
    assert "unavailable" not in openmm_compat.describe_md_backend().lower()
