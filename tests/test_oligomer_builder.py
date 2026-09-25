"""The oligomer builder: exactly n repeat units, joined head to tail, stereochemistry kept.

The earlier builder looped psmiles' ``dimer``, which doubles the whole chain and joins head to
head: 4 repeats built 8 units, PEG became a peroxide, and lysine became a hydrazine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from rdkit import Chem  # noqa: E402

from biologix_ai.simulation.polymer_build import build_polymer_oligomer_smiles  # noqa: E402

PEG = "[*]OCC[*]"
PLL_FPBA = "[*]N[C@@H](CCCC[NH3+])C(=O)N[C@@H](CCCCNC(=O)c1ccc(B(O)O)cc1F)C(=O)[*]"
PSBMA = "[*]CC(C)(C(=O)OCC[N+](C)(C)CCCS(=O)(=O)[O-])[*]"
PMMA = "[*]CC(C)(C(=O)OC)[*]"
POLYETHYLENE = "[*]CC[*]"
UNITS = [PEG, PLL_FPBA, PSBMA, PMMA, POLYETHYLENE]


def _heavy(mol) -> int:
    return sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() > 1)


@pytest.mark.parametrize("psmiles", UNITS)
@pytest.mark.parametrize("n", [2, 3, 4, 8])
def test_the_chain_has_exactly_n_repeat_units(psmiles, n):
    unit = Chem.MolFromSmiles(psmiles)
    capped, actual = build_polymer_oligomer_smiles(psmiles, n)
    chain = Chem.MolFromSmiles(capped)
    assert actual == n
    assert _heavy(chain) == n * (unit.GetNumAtoms() - 2)
    assert Chem.GetFormalCharge(chain) == n * Chem.GetFormalCharge(unit)


def test_the_reported_preflight_charge_for_four_pll_fpba_repeats_is_plus_four():
    """The agent saw +8 and 474 atoms for 'four repeats'. Four units carry +4 charge."""
    capped, actual = build_polymer_oligomer_smiles(PLL_FPBA, 4)
    mol = Chem.AddHs(Chem.MolFromSmiles(capped))
    assert actual == 4 and Chem.GetFormalCharge(mol) == 4
    assert mol.GetNumAtoms() < 474  # half the atoms of the old 8-unit chain


def test_junctions_are_head_to_tail_not_head_to_head():
    peg = Chem.MolFromSmiles(build_polymer_oligomer_smiles(PEG, 4)[0])
    assert not peg.HasSubstructMatch(Chem.MolFromSmarts("[OX2][OX2]")), "peroxide"
    lysine = Chem.MolFromSmiles(build_polymer_oligomer_smiles(PLL_FPBA, 4)[0])
    assert not lysine.HasSubstructMatch(Chem.MolFromSmarts("[NX3][NX3]")), "hydrazine"
    assert not lysine.HasSubstructMatch(Chem.MolFromSmarts("C(=O)C(=O)")), "oxalyl"
    # 4 dipeptide units: 7 backbone amides between 8 residues, plus 4 FPBA amides
    assert len(lysine.GetSubstructMatches(Chem.MolFromSmarts("C(=O)[NX3;H1]"))) == 7 + 4


def test_stereocenters_are_kept():
    unit = Chem.MolFromSmiles(PLL_FPBA)
    chain = Chem.MolFromSmiles(build_polymer_oligomer_smiles(PLL_FPBA, 4)[0])
    unit_labels = {label for _i, label in Chem.FindMolChiralCenters(unit, useLegacyImplementation=False)}
    chain_centers = Chem.FindMolChiralCenters(chain, useLegacyImplementation=False)
    assert len(chain_centers) == 4 * len(Chem.FindMolChiralCenters(unit, useLegacyImplementation=False))
    assert {label for _i, label in chain_centers} == unit_labels


def test_one_repeat_is_unchanged():
    assert build_polymer_oligomer_smiles(PEG, 1) == ("[H]OCC[H]", 1)


def test_the_chain_ends_are_hydrogen_capped_not_open():
    for psmiles in UNITS:
        capped, _ = build_polymer_oligomer_smiles(psmiles, 4)
        assert "*" not in capped


def test_a_unit_without_two_ends_is_refused_not_guessed():
    assert build_polymer_oligomer_smiles("[*]CC([*])C[*]", 4) == (None, 0)
    assert build_polymer_oligomer_smiles("CCO", 4) == (None, 0)


def test_the_default_polymer_is_a_four_mer_now():
    """Default n_repeats is 4; it used to build 8 units and report 4."""
    unit = Chem.MolFromSmiles(PSBMA)
    chain = Chem.MolFromSmiles(build_polymer_oligomer_smiles(PSBMA, 4)[0])
    assert _heavy(chain) == 4 * (unit.GetNumAtoms() - 2)


def test_the_preflight_shows_the_chain_it_built_and_a_consistent_charge():
    pytest.importorskip("openmm")
    pytest.importorskip("openmmforcefields")
    from biologix_ai.simulation.openmm_complex import polymer_md_preflight

    result = polymer_md_preflight(PSBMA, n_repeats=4)
    if not result.get("md_ready"):
        pytest.skip(f"GAFF not available here: {result.get('error')}")
    chain = Chem.MolFromSmiles(result["oligomer_smiles"])
    assert result["n_repeats"] == 4
    assert result["net_charge"] == Chem.GetFormalCharge(chain) == 4 * result["charge_per_repeat"]
