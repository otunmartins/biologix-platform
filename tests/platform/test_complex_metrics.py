import numpy as np
import pytest

from biologix_ai.simulation.complex_metrics import contact_count, kabsch_rmsd_nm, minimum_image


def test_identical_structures_have_zero_rmsd():
    coords = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    assert kabsch_rmsd_nm(coords, coords) == pytest.approx(0.0, abs=1e-9)


def test_rigid_translation_and_rotation_are_removed():
    coords = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    rotation = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])   # 90 degrees about z
    moved = coords @ rotation.T + np.array([5.0, -3.0, 2.0])
    assert kabsch_rmsd_nm(coords, moved) == pytest.approx(0.0, abs=1e-9)


def test_real_distortion_is_reported():
    coords = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    moved = coords.copy()
    moved[0] += np.array([0.4, 0.0, 0.0])
    assert kabsch_rmsd_nm(coords, moved) > 0.05


def test_mismatched_shapes_return_none():
    assert kabsch_rmsd_nm(np.zeros((4, 3)), np.zeros((5, 3))) is None


def test_contacts_counted_within_cutoff():
    protein = np.array([[0.0, 0, 0]])
    polymer = np.array([[0.3, 0, 0], [0.5, 0, 0], [10.0, 0, 0]])
    assert contact_count(protein, polymer, cutoff_nm=0.4) == 1


def test_contacts_across_a_periodic_boundary_are_found():
    box = 10.0
    protein = np.array([[0.05, 0, 0]])
    polymer = np.array([[9.95, 0, 0]])          # 0.1 nm away through the boundary
    assert contact_count(protein, polymer, cutoff_nm=0.4) == 0
    assert contact_count(protein, polymer, cutoff_nm=0.4, box_nm=box) == 1


def test_minimum_image_wraps_long_displacements():
    assert minimum_image(np.array([9.9]), 10.0) == pytest.approx(-0.1)
    assert minimum_image(np.array([0.1]), 10.0) == pytest.approx(0.1)
    assert minimum_image(np.array([9.9]), None) == pytest.approx(9.9)


def test_empty_inputs_are_safe():
    assert contact_count(np.zeros((0, 3)), np.ones((3, 3))) == 0


def test_chunking_matches_a_direct_count():
    rng = np.random.default_rng(0)
    protein = rng.random((300, 3)) * 2
    polymer = rng.random((400, 3)) * 2
    direct = int(np.count_nonzero(
        np.sum((protein[:, None, :] - polymer[None, :, :]) ** 2, axis=-1) <= 0.4 ** 2))
    assert contact_count(protein, polymer, cutoff_nm=0.4) == direct


def test_composite_score_becomes_computable():
    from biologix_ai.simulation.scoring import composite_screening_score

    assert isinstance(composite_screening_score(-305.9, 0.12), float)


def test_property_extractor_produces_a_composite_score_from_simulator_output():
    """The matrix path now emits rmsd and component energies, so the screening
    score the ranking depends on is finally computable."""
    from biologix_ai.simulation.property_extractor import PropertyExtractor

    md_results = [{
        "psmiles": "[*]OCC[*]",
        "method": "OpenMM_matrix_bulk_AMBER14SB_GAFF_Gasteiger",
        "interaction_energy_kj_mol": -305.86,
        "potential_energy_complex_kj_mol": -10922.5,
        "potential_energy_insulin_kj_mol": -8100.0,
        "potential_energy_polymer_kj_mol": -2516.6,
        "insulin_rmsd_to_initial_nm": 0.12,
        "insulin_polymer_contacts": 42,
    }]
    analysis = PropertyExtractor().extract_feedback(md_results)["property_analysis"]["[*]OCC[*]"]
    assert analysis["composite_screening_score"] is not None
    assert analysis["insulin_rmsd_to_initial_nm"] == 0.12
    assert analysis["insulin_polymer_contacts"] == 42
    assert analysis["potential_energy_insulin_kj_mol"] == -8100.0
    assert analysis["potential_energy_polymer_kj_mol"] == -2516.6


def test_the_old_simulator_output_left_the_score_null():
    """Regression guard: this is what production actually emitted."""
    from biologix_ai.simulation.property_extractor import PropertyExtractor

    analysis = PropertyExtractor().extract_feedback([{
        "psmiles": "[*]OCC[*]",
        "interaction_energy_kj_mol": -305.86,
        "potential_energy_complex_kj_mol": -10922.5,
    }])["property_analysis"]["[*]OCC[*]"]
    assert analysis["composite_screening_score"] is None


def test_an_unparseable_structure_is_never_reported_safe():
    """poly(vinyl acetate) reached the screen as a name, matched no SMARTS pattern,
    got no ADMET prediction, and came back safe=True with no warnings."""
    from biologix_ai.services.toxicity_service import screen_monomer

    result = screen_monomer("poly(vinyl acetate)")
    assert result.parsed is False
    assert result.safe is False
    assert "could not be parsed" in result.warnings[0]


def test_a_real_structure_still_screens_normally():
    from biologix_ai.services.toxicity_service import screen_monomer

    epoxide = screen_monomer("C1CO1")
    assert epoxide.parsed is True
    assert epoxide.safe is False
    assert any("epoxide" in w for w in epoxide.warnings)

    water = screen_monomer("O")
    assert water.parsed is True and water.safe is True and water.warnings == []


def test_trivial_reagents_are_not_given_admet_findings():
    """A real run reported water as AMES-positive with an LD50 of 309 mg/kg. The
    models are trained on drug-like molecules; water is far outside that domain."""
    from biologix_ai.services.toxicity_service import _heavy_atom_count, ADMET_MIN_HEAVY_ATOMS

    assert _heavy_atom_count("O") == 1
    assert _heavy_atom_count("CO") == 2
    assert _heavy_atom_count("C1CO1") == 3
    assert _heavy_atom_count("CC1C(=O)OC(C(=O)O1)C") >= ADMET_MIN_HEAVY_ATOMS


def test_out_of_domain_monomers_are_reported_by_the_audit():
    from biologix_ai.platform.completeness import audit

    report = audit({
        "retrosynthesis": {"status": "completed", "result": {"polymer_routes": [{
            "steps": [{"product_name": "x"}],
            "monomers": [{"name": "water", "smiles": "O"}]}]}},
        "monomer_safety": [{"name": "water", "smiles": "O", "parsed": True,
                            "admet": {"available": True, "in_domain": False}}],
    })
    verdict = next(s for s in report["stages"] if s["stage"] == "monomer_safety")
    assert verdict["status"] == "degraded"
    assert "applicability domain" in verdict["reasons"][0]
