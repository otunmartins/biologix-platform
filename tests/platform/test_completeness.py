"""Completeness rules, anchored on a real production run.

production_result.json is the captured output of an experiment the platform
reported as done: every stage status said success, the retrosynthesis route was
empty, and no residual monomer was ever screened.
"""

import json
from pathlib import Path

import pytest

from biologix_ai.platform.completeness import audit, failure_message

PRODUCTION = json.loads((Path(__file__).parent / "production_result.json").read_text())["results"]


def stage(report, name):
    return next(item for item in report["stages"] if item["stage"] == name)


def test_the_production_run_that_reported_done_is_not_complete():
    assert audit(PRODUCTION)["verdict"] == "incomplete"


def test_a_route_with_no_steps_is_a_failure_not_a_completion():
    assert PRODUCTION["retrosynthesis"]["status"] == "completed"
    assert PRODUCTION["retrosynthesis"]["result"]["polymer_routes"][0]["steps"] == []
    verdict = stage(audit(PRODUCTION), "retrosynthesis")
    assert verdict["status"] == "failed"
    assert "reaction steps" in verdict["reasons"][0]


def test_monomer_screen_that_never_ran_is_reported_as_skipped():
    verdict = stage(audit(PRODUCTION), "monomer_safety")
    assert verdict["status"] == "skipped"


def test_structure_warning_is_not_hidden_behind_valid_true():
    assert PRODUCTION["validation"]["valid"] is True
    verdict = stage(audit(PRODUCTION), "structure_validation")
    assert verdict["status"] == "degraded"
    assert "may not represent this material" in verdict["reasons"][0]


def test_null_physics_metrics_are_reported():
    verdict = stage(audit(PRODUCTION), "physics")
    assert verdict["status"] == "degraded"
    assert any("no value computed" in reason for reason in verdict["reasons"])


def _sampled_physics(frames: int, npt: bool = True, std: float = 12.5):
    """A physics payload that did sample, so only the sampling rules are under test."""
    payload = json.loads(json.dumps(PRODUCTION))
    payload["physics"]["sampling"] = {
        "npt_enabled": npt,
        "duration_ps": 20.0,
        "wall_clock_limit_s": 900.0,
        "frames_averaged": frames,
        "expected_frames": 40,
        "truncated_by_wall_clock": False,
    }
    for record in payload["physics"]["result"]["results"]["md_results_raw"]:
        record["n_frames_averaged"] = frames
        record["interaction_energy_kj_mol_std"] = std
    return payload


def test_an_unsampled_single_pose_energy_is_called_out():
    payload = _sampled_physics(frames=0, npt=False)
    reasons = stage(audit(payload), "physics")["reasons"]
    assert any("single minimised pose" in reason for reason in reasons)


def test_too_few_frames_is_called_out():
    reasons = stage(audit(_sampled_physics(frames=3)), "physics")["reasons"]
    assert any("frame minimum" in reason for reason in reasons)


def test_enough_frames_is_not_called_out():
    reasons = stage(audit(_sampled_physics(frames=40)), "physics")["reasons"]
    assert not any("frame minimum" in reason or "single minimised pose" in reason for reason in reasons)


def test_energy_without_a_spread_is_called_out():
    payload = _sampled_physics(frames=40)
    for record in payload["physics"]["result"]["results"]["md_results_raw"]:
        record.pop("interaction_energy_kj_mol_std")
    reasons = stage(audit(payload), "physics")["reasons"]
    assert any("standard deviation" in reason for reason in reasons)


def test_a_still_drifting_trajectory_is_called_out():
    """Measured on PEG/insulin: at 20 ps the energy was still falling by hundreds of
    kJ/mol, so a mean taken there describes the approach to equilibrium, not equilibrium."""
    payload = _sampled_physics(frames=40, std=41.0)
    for record in payload["physics"]["result"]["results"]["md_results_raw"]:
        record["interaction_energy_drift_kj_mol"] = -600.0
    reasons = stage(audit(payload), "physics")["reasons"]
    assert any("still drifting" in reason and "has not converged" in reason for reason in reasons)


def test_a_converged_trajectory_is_not_called_out():
    payload = _sampled_physics(frames=200, std=40.8)
    for record in payload["physics"]["result"]["results"]["md_results_raw"]:
        record["interaction_energy_drift_kj_mol"] = 13.4
    reasons = stage(audit(payload), "physics")["reasons"]
    assert not any("still drifting" in reason for reason in reasons)


def test_a_truncated_trajectory_is_called_out():
    payload = _sampled_physics(frames=12)
    payload["physics"]["sampling"]["truncated_by_wall_clock"] = True
    reasons = stage(audit(payload), "physics")["reasons"]
    assert any("wall-clock limit" in reason for reason in reasons)


def test_failure_message_names_the_blocking_stages():
    message = failure_message(audit(PRODUCTION))
    assert "retrosynthesis" in message
    assert "monomer safety" in message


@pytest.mark.parametrize("status", ["requires_input", "no_routes", "failed"])
def test_every_degenerate_retro_status_blocks(status):
    report = audit({"retrosynthesis": {"status": status}})
    assert stage(report, "retrosynthesis")["status"] == "failed"
    assert report["verdict"] == "incomplete"


@pytest.mark.parametrize("status", ["disabled", "unavailable"])
def test_a_stage_that_was_switched_off_still_blocks(status):
    report = audit({"retrosynthesis": {"status": status}, "physics": {"status": status}})
    assert stage(report, "retrosynthesis")["status"] == "skipped"
    assert stage(report, "physics")["status"] == "skipped"
    assert report["verdict"] == "incomplete"


def test_admet_that_never_loaded_is_not_a_passed_safety_screen():
    report = audit({"safety": {"safe": True, "admet": {"available": False}}})
    assert stage(report, "safety_screen")["status"] == "degraded"


def test_physics_without_an_interaction_energy_fails():
    report = audit({"physics": {"status": "completed", "result": {"results": {"md_results_raw": [{}]}}}})
    assert stage(report, "physics")["status"] == "failed"


def test_a_genuinely_complete_run_passes():
    payload = {
        "summary": {"pdb_id": "4F1C"},
        "biologic": {"pdb_id": "4F1C", "provenance": "llm_verified"},
        "structure": {"psmiles": "[*]OCC[*]", "provenance": "llm_verified", "confidence": "high"},
        "validation": {"valid": True, "name_consistency": {"consistent": True}},
        "safety": {"safe": True, "admet": {"available": True}},
        "compliance": {"overall_status": "approved"},
        "retrosynthesis": {"status": "completed", "result": {"polymer_routes": [{
            "steps": [{"reactant_names": ["ethylene oxide"], "product_name": "poly(ethylene glycol)"}],
            "monomers": [{"name": "ethylene oxide", "smiles": "C1CO1"}],
        }]}},
        "monomer_safety": [{"name": "ethylene oxide", "smiles": "C1CO1", "admet": {"available": True}}],
        "physics": {"status": "completed", "sampling": {
            "npt_enabled": True, "duration_ps": 20.0, "frames_averaged": 40,
            "expected_frames": 40, "truncated_by_wall_clock": False,
        }, "result": {"results": {
            "md_results_raw": [{
                "interaction_energy_kj_mol": -305.8,
                "interaction_energy_kj_mol_std": 11.2,
                "n_frames_averaged": 40,
            }],
            "evaluation_progress": [{"status": "completed"}],
            "property_analysis": {"[*]OCC[*]": {"interaction_energy_kj_mol": -305.8}},
        }}},
    }
    report = audit(payload)
    assert report["verdict"] == "complete", report["stages"]


def test_target_resolution_uses_the_summary_available_at_audit_time():
    """The audit runs before the final summary is assembled; if the pdb_id is not
    in the payload it is passed, every run wrongly reports an unresolved target."""
    assert stage(audit({"summary": {"pdb_id": "4F1C"}}), "target_resolution")["status"] == "complete"
    assert stage(audit({"summary": {}}), "target_resolution")["status"] == "degraded"


def test_monomers_without_a_resolved_structure_are_flagged():
    report = audit({"retrosynthesis": {"status": "completed", "result": {"polymer_routes": [{
        "steps": [{"product_name": "poly(vinyl alcohol)"}],
        "monomers": [{"name": "poly(vinyl acetate)", "smiles": ""},
                     {"name": "methanol", "smiles": "CO"}],
    }]}}})
    verdict = stage(report, "monomer_safety")
    assert verdict["status"] == "degraded"
    assert "poly(vinyl acetate)" in verdict["reasons"][0]


def test_a_screen_that_could_not_parse_is_reported():
    report = audit({
        "retrosynthesis": {"status": "completed", "result": {"polymer_routes": [{
            "steps": [{"product_name": "x"}],
            "monomers": [{"name": "methanol", "smiles": "CO"}]}]}},
        "monomer_safety": [{"name": "methanol", "smiles": "CO", "parsed": False,
                            "admet": {"available": True}}],
    })
    verdict = stage(report, "monomer_safety")
    assert verdict["status"] == "degraded"
    assert "could not be parsed" in verdict["reasons"][0]


def test_a_degraded_stage_blocks_the_run():
    """The rule is that anything not actually done is a failure. A stage that ran
    but returned a degraded result has not produced what the pipeline claims."""
    report = audit({
        "summary": {"pdb_id": "4F1C"},
        "validation": {"valid": True, "name_consistency": {"consistent": True}},
        "safety": {"safe": True, "admet": {"available": False}},   # degraded
        "compliance": {"overall_status": "approved"},
        "retrosynthesis": {"status": "completed", "result": {"polymer_routes": [{
            "steps": [{"product_name": "x"}], "monomers": [{"name": "m", "smiles": "CO"}]}]}},
        "monomer_safety": [{"name": "m", "smiles": "CO", "admet": {"available": True}}],
        "physics": {"status": "completed", "sampling": {
            "npt_enabled": True, "frames_averaged": 40, "expected_frames": 40,
        }, "result": {"results": {
            "md_results_raw": [{
                "interaction_energy_kj_mol": -1.0,
                "interaction_energy_kj_mol_std": 5.0,
                "n_frames_averaged": 40,
            }],
            "evaluation_progress": [{"status": "completed"}], "property_analysis": {}}}},
    })
    assert stage(report, "safety_screen")["status"] == "degraded"
    assert report["verdict"] == "incomplete"
    assert "safety_screen" in report["blocking"]


def test_no_partial_verdict_exists():
    for payload in ({}, {"safety": {"admet": {"available": False}}}):
        assert audit(payload)["verdict"] in ("complete", "incomplete")


def test_a_fully_purchasable_route_is_complete():
    """Every leaf buyable is the goal state of retrosynthesis, not a skipped stage."""
    report = audit({"retrosynthesis": {"status": "completed", "result": {
        "polymer_routes": [{"steps": [{"product_name": "PLGA"}],
                            "monomers": [{"name": "lactide", "smiles": "CC1C(=O)OC(C(=O)O1)C"}]}],
        "metadata": {"route_provenance": "session_agent_llm",
                     "aizynth_not_needed": "Every monomer is purchasable, so no route enrichment was required"},
    }}})
    assert stage(report, "retrosynthesis")["status"] == "complete"


def test_enrichment_that_was_genuinely_unavailable_still_blocks():
    report = audit({"retrosynthesis": {"status": "completed", "result": {
        "polymer_routes": [{"steps": [{"product_name": "X"}], "monomers": [{"name": "m", "smiles": "CO"}]}],
        "metadata": {"aizynth_skip_reason": "enrich_monomers_with_aizynth=false"},
    }}})
    assert stage(report, "retrosynthesis")["status"] == "degraded"
