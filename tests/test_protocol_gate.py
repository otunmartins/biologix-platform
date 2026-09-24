"""Tests for the remote discovery protocol gate.

The gate enforces the CLAUDE.md step order on the remote connector, where
clients do not reliably read server instructions. Fake tools stand in for
literature, OpenMM, and retrosynthesis so only the stage machine is tested.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.protocol_gate import (
    MAX_TOOL_RETRIES,  # noqa: E402
    BOOTSTRAP_TOOL,
    FIRST_CONTACT_DIRECTIVE,
    PROTOCOL_ORDER_ERROR,
    REMOTE_PROTOCOL_TOOLS,
    REMOTE_TOOL_STEPS,
    STATE_FILENAME,
    ProtocolGate,
    install_protocol_gate,
)

PASS_A = "[*]OCC[*]"
PASS_B = "[*]CC(O)[*]"
WARN_C = "[*]CC(C#N)[*]"
FAIL_D = "[*]CC(Cl)[*]"


def _build_fake_server(session_dir: Path, config: Dict[str, Any]) -> FastMCP:
    """Register fake protocol tools whose bodies record that they ran."""
    mcp = FastMCP("fake-biologix")
    ran: List[str] = config.setdefault("ran", [])

    def _mark(name: str) -> None:
        ran.append(name)

    @mcp.tool()
    def begin_biologix_discovery(biologic_target: str = "", polymer_target: str = "") -> str:
        _mark("begin_biologix_discovery")
        return json.dumps({"ok": True, "biologic_target": biologic_target})

    @mcp.tool()
    def resolve_biologic_target(name_or_pdb_id: str, fetch_pdb: bool = True, run_dir: str = "") -> str:
        _mark("resolve_biologic_target")
        if name_or_pdb_id in config.get("unresolvable", ()):
            return json.dumps({"query": name_or_pdb_id, "fetch_ok": False, "errors": ["no entry"]})
        return json.dumps(
            {
                "pdb_id": "3I40",
                "pdb_path": "/tmp/3I40.pdb",
                "fetch_ok": True,
                "resolved_target": config.get("resolved_target", "3I40:A,B"),
                "suggested_compute": config.get("suggested_compute", ""),
            }
        )

    @mcp.tool()
    def biologix_runtime_status() -> str:
        _mark("biologix_runtime_status")
        return json.dumps({"ok": True})

    @mcp.tool()
    def start_biologics_session(biologic_target: str, polymer_target: str = "", run_name: str = "") -> str:
        _mark("start_biologics_session")
        config.setdefault("session_targets", []).append(biologic_target)
        session_dir.mkdir(parents=True, exist_ok=True)
        return json.dumps(
            {
                "session_dir": str(session_dir),
                "biologic_resolution": {
                    "pdb_id": "3I40",
                    "pdb_path": "/tmp/3I40.pdb",
                    "fetch_ok": True,
                    "suggested_compute": config.get("suggested_compute", ""),
                },
            }
        )

    @mcp.tool()
    def mine_literature(query: str = "", iteration: int = 1, run_dir: str = "") -> str:
        _mark("mine_literature")
        config.setdefault("mine_run_dirs", []).append(run_dir)
        if config.get("mine_error"):
            return "Error: literature backend timed out"
        return "Literature digest: chitosan and PEG stabilize insulin."

    @mcp.tool()
    def mutate_psmiles(library_size: int = 10, feedback_json: str = "") -> str:
        _mark("mutate_psmiles")
        return json.dumps([{"material_name": "m1", "chemical_structure": PASS_A}])

    @mcp.tool()
    def validate_psmiles(psmiles: str, material_name: str = "", crosscheck_web: bool = False) -> str:
        _mark("validate_psmiles")
        if "BAD" in psmiles:
            return json.dumps({"valid": False, "error": "parse failed"})
        return json.dumps({"valid": True, "canonical": psmiles})

    @mcp.tool()
    def screen_candidate_library(psmiles_list: str, run_dir: str = "") -> str:
        _mark("screen_candidate_library")
        dispositions = config.get("dispositions", {})
        rows = [
            {"psmiles": p.strip(), "library_disposition": dispositions.get(p.strip(), "pass")}
            for p in psmiles_list.split(",")
            if p.strip()
        ]
        return json.dumps(rows)

    @mcp.tool()
    def openmm_evaluate_psmiles(
        psmiles_list: str = "",
        run_dir: str = "",
        max_workers: int = 1,
        response_format: str = "concise",
    ) -> str:
        _mark("openmm_evaluate_psmiles")
        if config.get("openmm_interrupted"):
            # What run_guarded_tool returns when a deploy closes Modal's client under a running job.
            return json.dumps(
                {"ok": False, "error": "47026404682448",
                 "traceback": "Traceback ...\nmodal.exception.ClientClosed: 47026404682448\n"}
            )
        if config.get("openmm_abort"):
            return json.dumps({"ok": False, "abort": True, "error": "packmol not on PATH"})
        return json.dumps(
            {
                "ok": True,
                "candidate_outcomes": [
                    {"index": 0, "status": "completed", "interaction_energy_kj_mol": -120.0}
                ],
            }
        )

    @mcp.tool()
    def save_pipeline_stage(
        candidate_psmiles: str,
        stage: str,
        disposition: str,
        detail: str = "",
        run_dir: str = "",
    ) -> str:
        _mark("save_pipeline_stage")
        return json.dumps({"ok": True, "recorded": True})

    @mcp.tool()
    def prepare_retrosynthesis(target: str, run_dir: str = "") -> str:
        _mark("prepare_retrosynthesis")
        return json.dumps({"material_name": f"poly_{len(target)}", "pdf_paths": []})

    @mcp.tool()
    def submit_retro_extractions(run_dir: str, material_name: str, extractions: str, target: str = "") -> str:
        _mark("submit_retro_extractions")
        if config.get("submit_fails"):
            return json.dumps({"ok": False, "error": "extractions must contain at least one paper entry"})
        return json.dumps(
            {
                "ok": True,
                "material_name": material_name,
                "validation": {"blocking_reactants": config.get("blocking", [])},
            }
        )

    @mcp.tool()
    def diagnose_retro_extractions(run_dir: str, material_name: str, target: str = "") -> str:
        _mark("diagnose_retro_extractions")
        return json.dumps({"ok": True, "blocking_reactants": []})

    @mcp.tool()
    def register_retro_precursors(run_dir: str, material_name: str, precursors: str) -> str:
        _mark("register_retro_precursors")
        return json.dumps({"ok": True, "registered_count": 1})

    @mcp.tool()
    def plan_retrosynthesis(target: str, run_dir: str = "") -> str:
        _mark("plan_retrosynthesis")
        return json.dumps({"polymer_routes": [], "warnings": ["kg_empty_after_session_extractions"]})

    @mcp.tool()
    def check_monomers_batch(smiles_list: str, run_dir: str = "") -> str:
        _mark("check_monomers_batch")
        return json.dumps([{"smiles": "OCCO", "safe": True}])

    @mcp.tool()
    def check_excipient_compliance(psmiles: str, run_dir: str = "") -> str:
        _mark("check_excipient_compliance")
        return json.dumps({"overall_status": "approved"})

    @mcp.tool()
    def assemble_retrosynthesis_report(run_dir: str, targets: str = "") -> str:
        _mark("assemble_retrosynthesis_report")
        return json.dumps({"ok": True, "markdown_path": "r.md"})

    @mcp.tool()
    def save_discovery_state(iteration: int, feedback_json: str, run_dir: str = "") -> str:
        _mark("save_discovery_state")
        return json.dumps({"ok": True, "saved": "agent_iteration.json"})

    @mcp.tool()
    def write_discovery_summary_report(run_dir: str = "") -> str:
        _mark("write_discovery_summary_report")
        return json.dumps({"ok": True, "markdown": "SUMMARY_REPORT.md"})

    @mcp.tool()
    def compile_discovery_markdown_to_pdf(run_dir: str = "") -> str:
        _mark("compile_discovery_markdown_to_pdf")
        return json.dumps({"ok": True, "pdf": "SUMMARY_REPORT.pdf"})

    @mcp.tool()
    def save_funnel_context(stage: str, checkpoint_data: str, run_dir: str = "") -> str:
        _mark("save_funnel_context")
        return json.dumps({"ok": True, "saved": True})

    @mcp.tool()
    def save_session_transcript(content: str, run_dir: str = "") -> str:
        _mark("save_session_transcript")
        return json.dumps({"ok": True, "saved": "SESSION_TRANSCRIPT.md"})

    return mcp


@pytest.fixture()
def harness(tmp_path: Path):
    session_dir = tmp_path / "runs" / "insulin_run"
    config: Dict[str, Any] = {}
    mcp = _build_fake_server(session_dir, config)
    gate = install_protocol_gate(mcp)

    def call(name: str, **kwargs: Any) -> Any:
        raw = mcp._tool_manager._tools[name].fn(**kwargs)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    return {"call": call, "config": config, "session": session_dir, "gate": gate, "mcp": mcp}


def _protocol(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result["protocol"]
    last_line = str(result).rstrip().splitlines()[-1]
    return json.loads(last_line)["protocol"]


def _start_session(call: Callable[..., Any], session: Path) -> None:
    call("begin_biologix_discovery", biologic_target="insulin", polymer_target="suggest")
    call("start_biologics_session", biologic_target="insulin", polymer_target="suggest")


def _through_screen(call: Callable[..., Any], session: Path, psmiles: List[str]) -> Any:
    _start_session(call, session)
    call("mine_literature", query="insulin polymer stabilization")
    for p in psmiles:
        call("validate_psmiles", psmiles=p, material_name="candidate")
    return call("screen_candidate_library", psmiles_list=",".join(psmiles), run_dir=str(session))


def test_first_contact_directive_fits_chatgpt_limit() -> None:
    assert len(FIRST_CONTACT_DIRECTIVE) <= 512
    for token in (BOOTSTRAP_TOOL, "next_required_tool", "user_stop_allowed", "one Biologix tool at a time"):
        assert token in FIRST_CONTACT_DIRECTIVE


def test_remote_tool_surface_is_the_protocol_tools_with_step_descriptions() -> None:
    assert BOOTSTRAP_TOOL == "begin_biologix_discovery"
    assert REMOTE_PROTOCOL_TOOLS[0] == BOOTSTRAP_TOOL
    assert len(REMOTE_PROTOCOL_TOOLS) == len(set(REMOTE_PROTOCOL_TOOLS)) == 25
    for hidden in ("pubmed_search", "run_autonomous_discovery", "start_discovery_session", "generate_psmiles_from_name"):
        assert hidden not in REMOTE_PROTOCOL_TOOLS
    assert set(REMOTE_TOOL_STEPS) == set(REMOTE_PROTOCOL_TOOLS)
    for step_text in REMOTE_TOOL_STEPS.values():
        assert step_text.startswith(("Step ", "Any step."))


def test_tools_before_bootstrap_are_refused_without_running(harness) -> None:
    result = harness["call"]("mine_literature", query="insulin")
    assert result["ok"] is False
    assert result["error"] == PROTOCOL_ORDER_ERROR
    assert result["required_next_tool"] == BOOTSTRAP_TOOL
    assert "mine_literature" not in harness["config"]["ran"]


def test_session_is_required_after_bootstrap(harness) -> None:
    call = harness["call"]
    begun = call("begin_biologix_discovery", biologic_target="insulin", polymer_target="suggest")
    assert _protocol(begun)["next_required_tool"] == "resolve_biologic_target"
    refused = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert refused["required_next_tool"] == "start_biologics_session"
    assert "openmm_evaluate_psmiles" not in harness["config"]["ran"]


def test_literature_only_checkpoint_is_refused(harness) -> None:
    call = harness["call"]
    _start_session(call, harness["session"])
    digest = call("mine_literature", query="insulin")
    assert isinstance(digest, str)
    assert digest.startswith("Literature digest")
    envelope = _protocol(digest)
    assert envelope["next_required_tool"] == "validate_psmiles"
    assert envelope["user_stop_allowed"] is False

    refused = call("save_discovery_state", iteration=1, feedback_json="{}", run_dir=str(harness["session"]))
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert refused["required_next_tool"] == "validate_psmiles"
    assert "save_discovery_state" not in harness["config"]["ran"]


def test_screen_only_accepts_validated_psmiles(harness) -> None:
    call = harness["call"]
    _start_session(call, harness["session"])
    call("mine_literature", query="insulin")
    invalid = call("validate_psmiles", psmiles="[*]BAD[*]")
    assert _protocol(invalid)["next_required_tool"] == "validate_psmiles"
    call("validate_psmiles", psmiles=PASS_A)

    refused = call("screen_candidate_library", psmiles_list=f"{PASS_A},{PASS_B}")
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert PASS_B in refused["reason"]

    screened = call("screen_candidate_library", psmiles_list=PASS_A)
    assert screened["results"][0]["psmiles"] == PASS_A
    assert _protocol(screened)["next_required_tool"] == "openmm_evaluate_psmiles"


def test_openmm_takes_one_pass_row_and_each_result_is_saved_first(harness) -> None:
    call = harness["call"]
    harness["config"]["dispositions"] = {WARN_C: "warning", FAIL_D: "fail"}
    _through_screen(call, harness["session"], [PASS_A, PASS_B, WARN_C, FAIL_D])

    two = call("openmm_evaluate_psmiles", psmiles_list=f"{PASS_A},{PASS_B}")
    assert two["error"] == PROTOCOL_ORDER_ERROR
    for not_pass in (WARN_C, FAIL_D):
        refused = call("openmm_evaluate_psmiles", psmiles_list=not_pass)
        assert refused["error"] == PROTOCOL_ORDER_ERROR

    first = call("openmm_evaluate_psmiles", psmiles_list=PASS_A, max_workers=1)
    assert first["ok"] is True
    assert _protocol(first)["next_required_tool"] == "save_pipeline_stage"

    unsaved = call("openmm_evaluate_psmiles", psmiles_list=PASS_B)
    assert unsaved["required_next_tool"] == "save_pipeline_stage"
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    second = call("openmm_evaluate_psmiles", psmiles_list=PASS_B)
    assert second["ok"] is True


def test_warning_rows_are_used_only_when_nothing_passes(harness) -> None:
    call = harness["call"]
    harness["config"]["dispositions"] = {WARN_C: "warning"}
    _through_screen(call, harness["session"], [WARN_C])
    allowed = call("openmm_evaluate_psmiles", psmiles_list=WARN_C)
    assert allowed["ok"] is True


def test_full_iteration_runs_without_a_user_stop_until_the_checkpoint(harness) -> None:
    call = harness["call"]
    session = str(harness["session"])
    screened = _through_screen(call, harness["session"], [PASS_A])
    assert _protocol(screened)["user_stop_allowed"] is False

    steps = [
        ("openmm_evaluate_psmiles", {"psmiles_list": PASS_A, "run_dir": session}, "save_pipeline_stage"),
        (
            "save_pipeline_stage",
            {"candidate_psmiles": PASS_A, "stage": "openmm", "disposition": "pass", "run_dir": session},
            "prepare_retrosynthesis",
        ),
        ("prepare_retrosynthesis", {"target": PASS_A, "run_dir": session}, "submit_retro_extractions"),
        (
            "submit_retro_extractions",
            {"run_dir": session, "material_name": "poly_9", "extractions": "{}", "target": PASS_A},
            "plan_retrosynthesis",
        ),
        ("plan_retrosynthesis", {"target": PASS_A, "run_dir": session}, "check_monomers_batch"),
        ("check_monomers_batch", {"smiles_list": "OCCO", "run_dir": session}, "check_excipient_compliance"),
        ("check_excipient_compliance", {"psmiles": PASS_A, "run_dir": session}, "save_pipeline_stage"),
        (
            "save_pipeline_stage",
            {"candidate_psmiles": PASS_A, "stage": "retrosynthesis", "disposition": "warning", "run_dir": session},
            "assemble_retrosynthesis_report",
        ),
        ("assemble_retrosynthesis_report", {"run_dir": session, "targets": PASS_A}, "save_discovery_state"),
        ("save_discovery_state", {"iteration": 1, "feedback_json": "{}", "run_dir": session}, "write_discovery_summary_report"),
        ("write_discovery_summary_report", {"run_dir": session}, "compile_discovery_markdown_to_pdf"),
        ("compile_discovery_markdown_to_pdf", {"run_dir": session}, "save_funnel_context"),
        ("save_funnel_context", {"stage": "post_iteration_1", "checkpoint_data": "{}", "run_dir": session}, "save_session_transcript"),
    ]
    for tool, kwargs, expected_next in steps:
        result = call(tool, **kwargs)
        envelope = _protocol(result)
        assert "error" not in result or result.get("error") != PROTOCOL_ORDER_ERROR, (tool, result)
        assert envelope["next_required_tool"] == expected_next, (tool, envelope)
        assert envelope["user_stop_allowed"] is False, tool

    done = call("save_session_transcript", content="# transcript", run_dir=session)
    envelope = _protocol(done)
    assert envelope["user_stop_allowed"] is True
    assert envelope["next_required_tool"] is None
    assert envelope["stage"] == "checkpoint"


def test_reports_run_in_order(harness) -> None:
    call = harness["call"]
    _through_screen(call, harness["session"], [PASS_A])
    refused = call("write_discovery_summary_report", run_dir=str(harness["session"]))
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert refused["required_next_tool"] == "openmm_evaluate_psmiles"


def test_retrosynthesis_plans_only_prepared_polymers(harness) -> None:
    call = harness["call"]
    session = str(harness["session"])
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)
    call("submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="{}", target=PASS_A)
    reagent = call("plan_retrosynthesis", target="CC(C)(C#N)SC(=S)c1ccccc1", run_dir=session)
    assert reagent["error"] == PROTOCOL_ORDER_ERROR
    by_name = call("plan_retrosynthesis", target="poly_9", run_dir=session)
    assert by_name.get("error") != PROTOCOL_ORDER_ERROR


def test_infrastructure_failure_blocks_and_allows_a_user_stop(harness) -> None:
    call = harness["call"]
    harness["config"]["openmm_abort"] = True
    _through_screen(call, harness["session"], [PASS_A])
    failed = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    envelope = _protocol(failed)
    assert envelope["stage"] == "blocked"
    assert envelope["user_stop_allowed"] is True
    assert "packmol not on PATH" in envelope["blocked_error"]

    refused = call("prepare_retrosynthesis", target=PASS_A, run_dir=str(harness["session"]))
    assert refused["error"] == PROTOCOL_ORDER_ERROR

    harness["config"]["openmm_abort"] = False
    retried = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    assert _protocol(retried)["stage"] != "blocked"


def test_prose_failure_from_literature_blocks(harness) -> None:
    call = harness["call"]
    harness["config"]["mine_error"] = True
    _start_session(call, harness["session"])
    failed = call("mine_literature", query="insulin")
    assert _protocol(failed)["stage"] == "blocked"


def test_state_persists_in_the_session_directory(harness) -> None:
    call = harness["call"]
    _through_screen(call, harness["session"], [PASS_A])
    state_path = harness["session"] / STATE_FILENAME
    assert state_path.is_file()

    fresh = ProtocolGate()
    refusal = fresh.check("prepare_retrosynthesis", {"target": PASS_A, "run_dir": str(harness["session"])})
    assert refusal is not None
    assert refusal["required_next_tool"] == "openmm_evaluate_psmiles"
    assert fresh.check("openmm_evaluate_psmiles", {"psmiles_list": PASS_A, "run_dir": str(harness["session"])}) is None


def _complete_iteration(call: Callable[..., Any], session: str) -> None:
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A, run_dir=session)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass", run_dir=session)
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)
    call("submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="{}", target=PASS_A)
    call("plan_retrosynthesis", target=PASS_A, run_dir=session)
    call("check_monomers_batch", smiles_list="OCCO", run_dir=session)
    call("check_excipient_compliance", psmiles=PASS_A, run_dir=session)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="retrosynthesis", disposition="warning", run_dir=session)
    call("assemble_retrosynthesis_report", run_dir=session, targets=PASS_A)
    call("save_discovery_state", iteration=1, feedback_json="{}", run_dir=session)
    call("write_discovery_summary_report", run_dir=session)
    call("compile_discovery_markdown_to_pdf", run_dir=session)
    call("save_funnel_context", stage="post_iteration_1", checkpoint_data="{}", run_dir=session)
    call("save_session_transcript", content="# transcript", run_dir=session)


def test_next_iteration_opens_only_from_the_checkpoint(harness) -> None:
    call = harness["call"]
    session = str(harness["session"])
    _through_screen(call, harness["session"], [PASS_A])

    # Re-mining mid-iteration is allowed (the user may redirect) but does not
    # open iteration 2: that still requires the finished checkpoint.
    early = call("mine_literature", query="insulin", iteration=2)
    assert _protocol(early)["iteration"] == 1
    early_mutation = call("mutate_psmiles", library_size=3)
    assert early_mutation["error"] == PROTOCOL_ORDER_ERROR

    _complete_iteration(call, session)
    stale = call("mine_literature", query="insulin", iteration=1)
    assert stale["error"] == PROTOCOL_ORDER_ERROR
    assert "iteration=2" in stale["reason"]

    opened = call("mine_literature", query="insulin", iteration=2)
    envelope = _protocol(opened)
    assert envelope["iteration"] == 2
    assert envelope["next_required_tool"] == "validate_psmiles"
    assert envelope["user_stop_allowed"] is False


def test_list_results_are_wrapped_with_the_envelope(harness) -> None:
    call = harness["call"]
    screened = _through_screen(call, harness["session"], [PASS_A])
    assert isinstance(screened, dict)
    assert isinstance(screened["results"], list)
    assert "protocol" in screened


def test_install_protocol_gate_is_idempotent(harness) -> None:
    mcp = harness["mcp"]
    install_protocol_gate(mcp, harness["gate"])
    result = harness["call"]("begin_biologix_discovery", biologic_target="insulin", polymer_target="PEG")
    assert harness["config"]["ran"].count("begin_biologix_discovery") == 1
    assert "protocol" in result


# -- per-client state, resolution retries, step instructions, retries ---------


def test_every_envelope_quotes_the_protocol_section_for_the_next_step(harness) -> None:
    call = harness["call"]
    begun = call("begin_biologix_discovery", biologic_target="semaglutide", polymer_target="suggest")
    envelope = _protocol(begun)
    assert envelope["next_required_tool"] == "resolve_biologic_target"
    assert envelope["next_arguments"] == {"name_or_pdb_id": "semaglutide", "fetch_pdb": True}
    assert envelope["step_instructions"].startswith("## Step 2")


def test_successful_resolve_passes_resolved_target_to_the_session(harness) -> None:
    call = harness["call"]
    harness["config"]["resolved_target"] = "4ZGM:B"
    call("begin_biologix_discovery", biologic_target="semaglutide", polymer_target="PEG")
    resolved = call("resolve_biologic_target", name_or_pdb_id="semaglutide")
    envelope = _protocol(resolved)
    assert envelope["next_required_tool"] == "start_biologics_session"
    assert envelope["next_arguments"] == {
        "biologic_target": "4ZGM:B",
        "biologic_name": "semaglutide",
        "polymer_target": "PEG",
    }


def test_failed_resolve_retries_without_the_user_then_blocks(harness) -> None:
    call = harness["call"]
    harness["config"]["unresolvable"] = {"etanercept", "etanercept Fc", "TNFR2"}
    call("begin_biologix_discovery", biologic_target="etanercept", polymer_target="suggest")
    first = _protocol(call("resolve_biologic_target", name_or_pdb_id="etanercept"))
    assert first["next_required_tool"] == "resolve_biologic_target"
    assert first["user_stop_allowed"] is False
    assert "Do not ask the user" in first["rule"]
    second = _protocol(call("resolve_biologic_target", name_or_pdb_id="etanercept Fc"))
    assert second["next_required_tool"] == "resolve_biologic_target"
    third = _protocol(call("resolve_biologic_target", name_or_pdb_id="TNFR2"))
    assert third["stage"] == "blocked"
    assert third["user_stop_allowed"] is True
    refused = call("start_biologics_session", biologic_target="etanercept")
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    # A new target from the user starts over.
    call("begin_biologix_discovery", biologic_target="uniprot:P20333", polymer_target="suggest")
    ok = _protocol(call("resolve_biologic_target", name_or_pdb_id="uniprot:P20333"))
    assert ok["next_required_tool"] == "start_biologics_session"


def test_clients_do_not_share_bootstrap_or_session(harness, monkeypatch) -> None:
    import biologix_ai.protocol_gate as gate_module

    call = harness["call"]
    monkeypatch.setattr(gate_module, "client_key", lambda: "client-a")
    _start_session(call, harness["session"])
    monkeypatch.setattr(gate_module, "client_key", lambda: "client-b")
    refused = call("mine_literature", query="insulin")
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert refused["required_next_tool"] == BOOTSTRAP_TOOL
    monkeypatch.setattr(gate_module, "client_key", lambda: "client-a")
    digest = call("mine_literature", query="insulin")
    assert isinstance(digest, str) and digest.startswith("Literature digest")


def test_gate_fills_in_a_dropped_run_dir(harness) -> None:
    call = harness["call"]
    _start_session(call, harness["session"])
    call("mine_literature", query="insulin")
    assert harness["config"]["mine_run_dirs"][-1] == str(harness["session"].resolve())


def test_runtime_status_is_never_gated(harness) -> None:
    result = harness["call"]("biologix_runtime_status")
    assert result == {"ok": True}


def test_openmm_next_arguments_carry_compute_and_save_carries_disposition(harness) -> None:
    call = harness["call"]
    harness["config"]["suggested_compute"] = "gpu"
    screened = _through_screen(call, harness["session"], [PASS_A])
    step = _protocol(screened)
    assert step["next_arguments"]["compute"] == "gpu"
    assert step["next_arguments"]["run_dir"] == str(harness["session"].resolve())
    ran = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    save = _protocol(ran)
    assert save["next_required_tool"] == "save_pipeline_stage"
    assert save["next_arguments"]["disposition"] == "pass"


def test_blocking_reactants_route_to_diagnose_and_retries_are_capped(harness) -> None:
    call = harness["call"]
    session = str(harness["session"])
    harness["config"]["blocking"] = ["CC(C)(C#N)SC(=S)c1ccccc1"]
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)
    for attempt in range(3):
        result = call(
            "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="{}", target=PASS_A
        )
        envelope = _protocol(result)
        if attempt < 2:
            assert envelope["next_required_tool"] == "diagnose_retro_extractions"
            call("diagnose_retro_extractions", run_dir=session, material_name="poly_9", target=PASS_A)
    assert envelope["next_required_tool"] == "plan_retrosynthesis"
    capped = call(
        "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="{}", target=PASS_A
    )
    assert capped["error"] == PROTOCOL_ORDER_ERROR
    assert "plan_retrosynthesis" in capped["reason"]
    planned = _protocol(call("plan_retrosynthesis", target=PASS_A, run_dir=session))
    call("check_monomers_batch", smiles_list="OCCO", run_dir=session)
    compliance = _protocol(call("check_excipient_compliance", psmiles=PASS_A, run_dir=session))
    assert compliance["next_arguments"]["disposition"] == "fail"
    assert planned["next_required_tool"] == "check_monomers_batch"


def test_real_submit_payload_nests_blocking_reactants_under_validation(harness) -> None:
    from biologix_ai.protocol_gate import _blocking_reactants

    assert _blocking_reactants({"ok": True, "validation": {"blocking_reactants": ["CCO"]}}) == ["CCO"]
    assert _blocking_reactants({"ok": True, "blocking_reactants": ["N"]}) == ["N"]
    assert _blocking_reactants({"ok": True, "validation": {}}) == []


def test_session_keeps_the_users_biologic_name(harness) -> None:
    call = harness["call"]
    harness["config"]["resolved_target"] = "4ZGM:B"
    call("begin_biologix_discovery", biologic_target="semaglutide", polymer_target="suggest")
    resolved = call("resolve_biologic_target", name_or_pdb_id="semaglutide")
    assert _protocol(resolved)["next_arguments"]["biologic_name"] == "semaglutide"


def test_order_refusals_say_they_are_not_failures(harness) -> None:
    call = harness["call"]
    _start_session(call, harness["session"])
    refused = call("screen_candidate_library", psmiles_list=PASS_A, run_dir=str(harness["session"]))
    assert refused["error"] == PROTOCOL_ORDER_ERROR
    assert refused["not_a_failure"] is True and "not a failure" in refused["rule"]


# -- persistence: recoverable failures, redirection, re-mining ------------------


def test_a_tool_the_model_called_wrongly_is_retried_not_fatal(harness) -> None:
    call, config = harness["call"], harness["config"]
    session = str(harness["session"])
    config["blocking"] = []
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)

    config["submit_fails"] = True
    first = call(
        "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="", target=PASS_A
    )
    envelope = _protocol(first)
    assert envelope["stage"] != "blocked"
    assert envelope["user_stop_allowed"] is False
    assert envelope["next_required_tool"] == "submit_retro_extractions"
    assert envelope["recoverable_failure"]["retries_left"] == 3
    assert "not an infrastructure failure" in envelope["rule"]

    # A rejected submission is not an attempt: the retry still goes to submit.
    config["submit_fails"] = False
    fixed = call(
        "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="{}", target=PASS_A
    )
    assert _protocol(fixed)["next_required_tool"] == "plan_retrosynthesis"
    assert "recoverable_failure" not in _protocol(fixed)


def test_a_tool_that_keeps_failing_finally_stops_the_run(harness) -> None:
    call, config = harness["call"], harness["config"]
    session = str(harness["session"])
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)
    config["submit_fails"] = True
    for _ in range(4):
        last = call(
            "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="", target=PASS_A
        )
    envelope = _protocol(last)
    assert envelope["stage"] == "blocked"
    assert envelope["user_stop_allowed"] is True
    assert "failed 4 times" in envelope["blocked_error"]


def test_the_user_can_redirect_mid_iteration_without_losing_the_session(harness) -> None:
    call = harness["call"]
    session = str(harness["session"])
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")

    # "Actually, look at something else" — mid-iteration, any iteration number.
    again = call("mine_literature", query="different polymer family", iteration=2, run_dir=session)
    assert not (isinstance(again, dict) and again.get("error") == PROTOCOL_ORDER_ERROR)
    added = call("validate_psmiles", psmiles=PASS_B, material_name="second idea")
    assert added.get("error") != PROTOCOL_ORDER_ERROR
    screened = call("screen_candidate_library", psmiles_list=f"{PASS_A},{PASS_B}", run_dir=session)
    assert _protocol(screened)["iteration"] == 1  # same session, work kept
    state = json.loads((harness["session"] / STATE_FILENAME).read_text())
    assert PASS_A in state["openmm_runs"] and PASS_B in state["validated"]


def test_a_recoverable_failure_names_the_specific_repair(harness) -> None:
    call, config = harness["call"], harness["config"]
    session = str(harness["session"])
    _through_screen(call, harness["session"], [PASS_A])
    call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    call("save_pipeline_stage", candidate_psmiles=PASS_A, stage="openmm", disposition="pass")
    call("prepare_retrosynthesis", target=PASS_A, run_dir=session)
    config["submit_fails"] = True
    failed = call(
        "submit_retro_extractions", run_dir=session, material_name="poly_9", extractions="", target=PASS_A
    )
    repair = _protocol(failed)["recoverable_failure"]["repair"]
    assert "Reactants:" in repair and "Products:" in repair
    assert repair in _protocol(failed)["rule"]


def test_repair_hints_match_the_failures_the_pipeline_actually_produces() -> None:
    from biologix_ai.protocol_gate import _repair_hint

    assert "inner salt" in _repair_hint("counterions were disabled for this server")
    assert "two [*]" in _repair_hint("Expected exactly 2 [*] connection points, found 1")
    assert "register_retro_precursors" in _repair_hint("kg_empty_after_session_extractions")
    assert "shorter repeat unit" in _repair_hint("Packmol packing failed: timeout")
    assert _repair_hint("") and _repair_hint("unrecognised")


def test_candidate_budgets_are_configurable(monkeypatch) -> None:
    """The per-iteration caps are a compute budget, not a scientific limit."""
    import importlib

    import biologix_ai.protocol_gate as gate_module

    monkeypatch.setenv("BIOLOGIX_MAX_OPENMM_CANDIDATES", "6")
    monkeypatch.setenv("BIOLOGIX_MAX_RETRO_TARGETS", "5")
    reloaded = importlib.reload(gate_module)
    try:
        assert reloaded.MAX_OPENMM_CANDIDATES == 6
        assert reloaded.MAX_RETRO_TARGETS == 5
        monkeypatch.setenv("BIOLOGIX_MAX_OPENMM_CANDIDATES", "not-a-number")
        assert importlib.reload(gate_module).MAX_OPENMM_CANDIDATES == 3
    finally:
        monkeypatch.delenv("BIOLOGIX_MAX_OPENMM_CANDIDATES", raising=False)
        monkeypatch.delenv("BIOLOGIX_MAX_RETRO_TARGETS", raising=False)
        importlib.reload(gate_module)


def test_a_call_cut_off_by_a_restart_is_not_recorded_as_a_failure(harness) -> None:
    """A deploy mid-simulation produced ClientClosed; it counted as a failed attempt and stopped the run."""
    call = harness["call"]
    _through_screen(call, harness["session"], [PASS_A])
    harness["config"]["openmm_interrupted"] = True
    for _ in range(MAX_TOOL_RETRIES + 2):  # far more than the retry cap
        result = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
        assert result.get("protocol", {}).get("stage") != "blocked"
    from biologix_ai.protocol_gate import load_state

    state = load_state(Path(harness["session"]))
    assert state.blocked_tool == "" and state.failures.get("openmm_evaluate_psmiles", 0) == 0
    harness["config"]["openmm_interrupted"] = False
    ok = call("openmm_evaluate_psmiles", psmiles_list=PASS_A)
    assert _protocol(ok)["next_required_tool"] == "save_pipeline_stage"
