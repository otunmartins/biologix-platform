#!/usr/bin/env python3
"""
Server-side discovery protocol for remote MCP clients (ChatGPT, Claude Web).

Remote connectors have no system-prompt slot: ChatGPT reliably reads only the
first 512 characters of ``InitializeResult.instructions`` and claude.ai drops
them entirely. Every client does read tool descriptions and tool results, so
this module enforces the CLAUDE.md step order on the server:

* ``check`` refuses an out-of-order call before the tool body runs and returns
  ``PROTOCOL_ORDER`` with the tool that must run next.
* ``record`` updates the per-session stage machine and attaches a ``protocol``
  envelope (``next_required_tool``, ``user_stop_allowed``) to the result.

State lives in ``<run_dir>/protocol_state.json`` so it survives container
restarts. Pre-session state (bootstrap, structure resolution) is kept per MCP
client (``mcp_client.client_key``), so concurrent users of one server never see
each other's session. Every envelope also quotes the protocol section for the
next step (``step_instructions``), because some clients ignore server
instructions entirely.

The gate runs on the remote HTTP server and on stdio with
``BIOLOGIX_MCP_PROFILE=protocol`` (the default); ``full`` leaves stdio ungated
for the OpenCode agent, which carries its own prompt.
"""

from __future__ import annotations

import functools
import inspect
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from biologix_ai.mcp_client import client_key
from biologix_ai.mcp_tool_guard import log_tool_event
from biologix_ai.protocol import section, step_instructions

PROTOCOL_ORDER_ERROR = "PROTOCOL_ORDER"
STATE_FILENAME = "protocol_state.json"
BOOTSTRAP_TOOL = "begin_biologix_discovery"
MAX_OPENMM_CANDIDATES = 3
MAX_RETRO_TARGETS = 3
MAX_RESOLVE_RETRIES = 2
MAX_RETRO_RETRIES = 2
MAX_TOOL_RETRIES = 3
FAILURE_SECTION = "When something goes wrong"
AWAIT_TOOL = "await_biologix_job"
STATUS_TOOL = "biologix_runtime_status"

FIRST_CONTACT_DIRECTIVE = (
    "Biologix runs a fixed discovery pipeline. If the user has not named a biologic "
    "(name, PDB ID, UniProt ID, or sequence) and a polymer or \"suggest\", ask only for "
    "those and wait. Then call begin_biologix_discovery first: it returns the protocol. "
    "Call one Biologix tool at a time. Every result names next_required_tool: call it next "
    "(status \"running\" means call await_biologix_job). Do not stop, summarize, or ask "
    "the user until user_stop_allowed is true or a tool fails. Write PSMILES yourself. "
    "Invent no results."
)

REMOTE_TOOL_STEPS: Dict[str, str] = {
    BOOTSTRAP_TOOL: (
        "Step 1 of 7. Call this first in every Biologix conversation, before any other "
        "Biologix tool. Returns the protocol you must follow. Next: resolve_biologic_target."
    ),
    "resolve_biologic_target": (
        "Step 2 of 7. Use after begin_biologix_discovery. Accepts a name, PDB:chains "
        "(4ZGM:B), uniprot:ACCESSION[:start-end], or sequence:ONE_LETTER (<=400 aa). If it "
        "fails, retry with a more specific form you find yourself; do not ask the user. "
        "Next: start_biologics_session with biologic_target=resolved_target."
    ),
    "start_biologics_session": (
        "Step 2 of 7. Use after resolve_biologic_target, passing its resolved_target. Pass "
        "the returned session_dir as run_dir to every later tool. Next: mine_literature."
    ),
    "mine_literature": (
        "Step 3 of 7. Use after start_biologics_session. The digest is evidence for the "
        "PSMILES you write, not the iteration result. Next: validate_psmiles."
    ),
    "validate_psmiles": (
        "Step 3 of 7. Use after mine_literature, once per PSMILES you wrote. Read the graph "
        "report; rewrite or drop a mismatched structure. Next: screen_candidate_library."
    ),
    "screen_candidate_library": (
        "Step 4 of 7. Use after validate_psmiles, with validated PSMILES only. "
        "Next: openmm_evaluate_psmiles."
    ),
    "openmm_evaluate_psmiles": (
        "Step 4 of 7. Use after screen_candidate_library, with one pass-row PSMILES per call. "
        "Next: save_pipeline_stage."
    ),
    "save_pipeline_stage": (
        "Step 4-5 of 7. Use after each OpenMM result (stage=\"openmm\") and each "
        "retrosynthesis disposition (stage=\"retrosynthesis\"). Next: next_required_tool."
    ),
    "prepare_retrosynthesis": (
        "Step 5 of 7. Use after OpenMM results are saved, with a passing polymer PSMILES. "
        "Next: submit_retro_extractions."
    ),
    "submit_retro_extractions": (
        "Step 5 of 7. Use after prepare_retrosynthesis. Next: plan_retrosynthesis, or "
        "diagnose_retro_extractions when blocking_reactants is non-empty."
    ),
    "diagnose_retro_extractions": (
        "Step 5 of 7. Use after submit_retro_extractions reports blocking reactants. "
        "Next: register_retro_precursors or submit_retro_extractions."
    ),
    "register_retro_precursors": (
        "Step 5 of 7. Use after diagnose_retro_extractions, for commercially available "
        "reagents. Next: plan_retrosynthesis."
    ),
    "plan_retrosynthesis": (
        "Step 5 of 7. Use after submit_retro_extractions, on the polymer PSMILES, never on a "
        "reagent. Next: check_monomers_batch."
    ),
    "check_monomers_batch": (
        "Step 5 of 7. Use after plan_retrosynthesis, on the route monomers. "
        "Next: check_excipient_compliance."
    ),
    "check_excipient_compliance": (
        "Step 5 of 7. Use after check_monomers_batch, on the polymer. "
        "Next: save_pipeline_stage with stage=\"retrosynthesis\"."
    ),
    "assemble_retrosynthesis_report": (
        "Step 6 of 7. Use after every retrosynthesis disposition is saved. "
        "Next: save_discovery_state."
    ),
    "save_discovery_state": (
        "Step 6 of 7. Use after assemble_retrosynthesis_report; the summary report is built "
        "from this state. Next: write_discovery_summary_report."
    ),
    "write_discovery_summary_report": (
        "Step 6 of 7. Use after save_discovery_state. Next: compile_discovery_markdown_to_pdf."
    ),
    "compile_discovery_markdown_to_pdf": (
        "Step 6 of 7. Use after write_discovery_summary_report. Next: save_funnel_context."
    ),
    "save_funnel_context": (
        "Step 6 of 7. Use after compile_discovery_markdown_to_pdf. "
        "Next: save_session_transcript."
    ),
    "save_session_transcript": (
        "Step 7 of 7. Use after save_funnel_context. Then present the iteration checkpoint "
        "and wait for the user."
    ),
    "mutate_psmiles": (
        "Step 7 of 7. Use only after the user approves another iteration, to refine high "
        "performers. Next: validate_psmiles."
    ),
    AWAIT_TOOL: (
        "Any step. Use when a Biologix result has status \"running\" and a job_id: waits for "
        "that job and returns its result. Not a failure and not a checkpoint. "
        "Next: next_required_tool."
    ),
    STATUS_TOOL: (
        "Any step. Reports the server's Packmol, OpenMM platforms (CPU/GPU), AiZynthFinder, "
        "and ADMET status. Never changes the pipeline."
    ),
}

REMOTE_PROTOCOL_TOOLS: Tuple[str, ...] = tuple(REMOTE_TOOL_STEPS)

_SESSION_TOOLS = ("resolve_biologic_target", "start_biologics_session")
_UNGATED_TOOLS = (STATUS_TOOL,)
_REPORT_SEQUENCE = (
    "assemble_retrosynthesis_report",
    "save_discovery_state",
    "write_discovery_summary_report",
    "compile_discovery_markdown_to_pdf",
    "save_funnel_context",
    "save_session_transcript",
)
_ALLOWED_WHILE_BLOCKED = ("save_pipeline_stage", "save_session_transcript")
_INFRASTRUCTURE_MARKERS = ("./install", "not installed", "not importable")

_RULE_CONTINUE = "Call next_required_tool now. Do not summarize for the user or ask a question yet."
_RULE_BLOCKED = (
    "A tool failed. Stop the pipeline, show the user the exact error in blocked_error and "
    "the last completed stage. Do not estimate or work around the missing result."
)
_RULE_ONBOARD = (
    "Ask the user only these questions, then wait. Call begin_biologix_discovery again "
    "with both answers."
)
_RULE_RESOLVE_RETRY = (
    "Structure resolution failed. Do not ask the user. Find a more specific form yourself "
    "(a PDB ID with chains such as 4ZGM:B, uniprot:ACCESSION[:start-end], or "
    "sequence:ONE_LETTER of at most 400 residues) and call resolve_biologic_target again."
)


def _clean(value: Any) -> str:
    """Strip whitespace and stray quotes from one PSMILES or name argument."""
    return str(value or "").strip().strip('"').strip("'").strip()


def _as_list(value: Any) -> List[str]:
    """Accept a comma-separated string, a JSON array string, or a list of PSMILES."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value if _clean(item)]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [_clean(item) for item in parsed if _clean(item)]
    return [_clean(part) for part in text.split(",") if _clean(part)]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse(result: str) -> Any:
    """Return the JSON object or array in *result*, or None for prose."""
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


@dataclass
class ProtocolState:
    """Progress of one discovery iteration inside one session directory."""

    session_dir: str
    iteration: int = 1
    literature: bool = False
    validated: List[str] = field(default_factory=list)
    screen: Dict[str, str] = field(default_factory=dict)
    openmm_runs: List[str] = field(default_factory=list)
    openmm_unsaved: str = ""
    retro_prepared: Dict[str, str] = field(default_factory=dict)
    retro_submitted: List[str] = field(default_factory=list)
    retro_planned: List[str] = field(default_factory=list)
    last_planned: str = ""
    monomers_checked: List[str] = field(default_factory=list)
    compliance_checked: List[str] = field(default_factory=list)
    retro_saved: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    blocked_tool: str = ""
    blocked_error: str = ""
    retro_submissions: Dict[str, int] = field(default_factory=dict)
    retro_blocking: Dict[str, List[str]] = field(default_factory=dict)
    openmm_disposition: Dict[str, str] = field(default_factory=dict)
    retro_disposition: Dict[str, str] = field(default_factory=dict)
    md_ready: Dict[str, bool] = field(default_factory=dict)
    compute: str = ""
    failures: Dict[str, int] = field(default_factory=dict)
    last_error: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtocolState":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def done(self, tool: str) -> bool:
        return tool in self.completed

    def mark(self, tool: str) -> None:
        if tool not in self.completed:
            self.completed.append(tool)

    def eligible(self) -> List[str]:
        """Pass rows, or warning rows when nothing passed (CLAUDE.md Step 4)."""
        passing = [p for p, disposition in self.screen.items() if disposition == "pass"]
        if passing:
            return passing
        return [p for p, disposition in self.screen.items() if disposition == "warning"]

    def no_eligible(self) -> bool:
        return bool(self.screen) and not self.eligible()

    def retro_targets(self) -> List[str]:
        ordered = list(self.openmm_runs)
        ordered.extend(p for p in self.retro_prepared if p not in ordered)
        return ordered[:MAX_RETRO_TARGETS]

    def at_checkpoint(self) -> bool:
        return self.done("save_session_transcript")

    def next_iteration(self) -> "ProtocolState":
        return ProtocolState(
            session_dir=self.session_dir, iteration=self.iteration + 1, compute=self.compute
        )

    def openmm_queue(self, eligible: List[str]) -> List[str]:
        """Eligible rows in OpenMM order: rows whose oligomer parameterizes first."""
        return sorted(eligible, key=lambda p: self.md_ready.get(p) is False)


def load_state(session_dir: Path) -> Optional[ProtocolState]:
    path = session_dir / STATE_FILENAME
    if not path.is_file():
        return None
    try:
        return ProtocolState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save_state(state: ProtocolState) -> None:
    path = Path(state.session_dir) / STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


@dataclass
class ClientState:
    """Pre-session progress of one MCP client (bootstrap and structure resolution)."""

    begun: bool = False
    active_session: Optional[Path] = None
    biologic_target: str = ""
    polymer_target: str = ""
    resolved_target: str = ""
    suggested_compute: str = ""
    resolve_failures: int = 0
    resolve_error: str = ""


class ProtocolGate:
    """Enforce the discovery step order and describe the next step in every result."""

    def __init__(self) -> None:
        self._clients: Dict[str, ClientState] = {}

    def client(self) -> ClientState:
        """State of the MCP client making the current call."""
        key = client_key()
        state = self._clients.get(key)
        if state is None:
            if len(self._clients) > 2048:
                self._clients.pop(next(iter(self._clients)))
            state = self._clients[key] = ClientState()
        return state

    # Kept for callers that inspected the single-client gate.
    @property
    def _begun(self) -> bool:
        return self.client().begun

    @property
    def _active_session(self) -> Optional[Path]:
        return self.client().active_session

    # -- session resolution -------------------------------------------------

    def session_for(self, arguments: Mapping[str, Any]) -> Optional[Path]:
        """Session named by ``run_dir``, else the one this client last started."""
        raw = _clean(arguments.get("run_dir"))
        if raw:
            return Path(raw).expanduser().resolve()
        return self.client().active_session

    def _state_for(self, arguments: Mapping[str, Any]) -> Optional[ProtocolState]:
        session = self.session_for(arguments)
        return load_state(session) if session is not None else None

    # -- refusal ------------------------------------------------------------

    def check(self, tool: str, arguments: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a ``PROTOCOL_ORDER`` refusal, or None when *tool* may run now."""
        if tool == BOOTSTRAP_TOOL or tool in _UNGATED_TOOLS:
            return None
        state = self._state_for(arguments)
        client = self.client()
        if tool in _SESSION_TOOLS:
            if client.resolve_failures > MAX_RESOLVE_RETRIES and state is None:
                return self._refuse(
                    tool,
                    None,
                    "Structure resolution failed "
                    f"{client.resolve_failures} times: {client.resolve_error}. Report this to "
                    "the user and ask for a PDB ID with chains, a UniProt accession, or a "
                    "sequence; then call begin_biologix_discovery with the new target.",
                )
            if client.begun or state is not None:
                return None
            return self._refuse(
                tool,
                state,
                "Call begin_biologix_discovery first; it returns the protocol you must follow.",
                required=BOOTSTRAP_TOOL,
            )
        if state is None:
            if not client.begun:
                return self._refuse(
                    tool,
                    None,
                    "Call begin_biologix_discovery first; it returns the protocol you must follow.",
                    required=BOOTSTRAP_TOOL,
                )
            return self._refuse(
                tool,
                None,
                "No discovery session is active. Call start_biologics_session, or pass the "
                "session_dir it returned as run_dir.",
                required="start_biologics_session",
            )
        reason = self._prerequisite_failure(tool, arguments, state)
        if reason:
            return self._refuse(tool, state, reason)
        return None

    def _refuse(
        self,
        tool: str,
        state: Optional[ProtocolState],
        reason: str,
        required: Optional[str] = None,
    ) -> Dict[str, Any]:
        envelope = self.envelope(state)
        required_tool = required if required is not None else envelope["next_required_tool"]
        if required is not None:
            envelope["next_required_tool"] = required
            envelope["next_arguments"] = {}
            envelope["step_instructions"] = step_instructions(required)
        log_tool_event(
            Path(state.session_dir) if state is not None else None,
            tool=tool,
            status="refused",
            stage="protocol_gate",
            error=PROTOCOL_ORDER_ERROR,
            message=f"required_next_tool={required_tool}",
        )
        return {
            "ok": False,
            "error": PROTOCOL_ORDER_ERROR,
            "not_a_failure": envelope["stage"] != "blocked",
            "tool": tool,
            "stage": envelope["stage"],
            "required_next_tool": required_tool,
            "next_arguments": envelope.get("next_arguments", {}),
            "reason": reason,
            "rule": (
                "This is not a failure and not a checkpoint: call required_next_tool now "
                "and continue the pipeline."
                if envelope["stage"] != "blocked"
                else envelope.get("rule", "")
            ),
            "protocol": envelope,
        }

    def _prerequisite_failure(
        self, tool: str, arguments: Mapping[str, Any], state: ProtocolState
    ) -> str:
        if state.blocked_tool:
            if tool in _ALLOWED_WHILE_BLOCKED:
                return ""
            if tool != state.blocked_tool:
                return (
                    f"The pipeline is blocked because {state.blocked_tool} failed: "
                    f"{state.blocked_error}. Report this to the user, or retry {state.blocked_tool}."
                )

        if state.at_checkpoint():
            return self._checkpoint_failure(tool, arguments, state)

        if tool == "mine_literature":
            # Always allowed inside an iteration: the user may redirect the search
            # at any time, and re-mining adds candidates without discarding the
            # work already done. Only the checkpoint requires iteration N+1.
            return ""
        if tool == "mutate_psmiles":
            if state.iteration < 2:
                return (
                    "mutate_psmiles refines high performers from a finished iteration. "
                    "Use it after the Step 7 checkpoint."
                )
            return ""
        if tool == "validate_psmiles":
            return "" if state.literature else "Call mine_literature for this iteration first."
        if tool == "screen_candidate_library":
            return self._screen_failure(arguments, state)
        if tool == "openmm_evaluate_psmiles":
            return self._openmm_failure(arguments, state)
        if tool == "save_pipeline_stage":
            return ""
        if tool == "prepare_retrosynthesis":
            return self._prepare_failure(arguments, state)
        if tool == "submit_retro_extractions":
            if not state.retro_prepared:
                return "Call prepare_retrosynthesis on a passing polymer first."
            target = self._retro_target(arguments, state)
            if target is None:
                return (
                    "Pass the polymer PSMILES given to prepare_retrosynthesis as target, "
                    "and its returned material_name."
                )
            if state.retro_submissions.get(target, 0) > MAX_RETRO_RETRIES:
                return (
                    f"Extractions for {target} were submitted {state.retro_submissions[target]} "
                    f"times (the first try plus {MAX_RETRO_RETRIES} retries). Call "
                    "plan_retrosynthesis on the polymer to record the failure detail."
                )
            return ""
        if tool in ("diagnose_retro_extractions", "register_retro_precursors"):
            return "" if state.retro_submitted else "Call submit_retro_extractions first."
        if tool == "plan_retrosynthesis":
            target = self._retro_target(arguments, state)
            if target is None or target not in state.retro_submitted:
                return (
                    "Plan only a polymer that went through prepare_retrosynthesis and "
                    "submit_retro_extractions. Chain-transfer agents, initiators, and other "
                    "reagents are precursors: register them with register_retro_precursors."
                )
            return ""
        if tool in ("check_monomers_batch", "check_excipient_compliance"):
            return "" if state.retro_planned else "Call plan_retrosynthesis first."
        if tool in _REPORT_SEQUENCE:
            return self._report_failure(tool, arguments, state)
        return ""

    def _checkpoint_failure(
        self, tool: str, arguments: Mapping[str, Any], state: ProtocolState
    ) -> str:
        following = state.iteration + 1
        if tool == "mine_literature":
            if _as_int(arguments.get("iteration"), 1) != following:
                return (
                    f"Iteration {state.iteration} is complete. After the user approves, call "
                    f"mine_literature with iteration={following}."
                )
            return ""
        if tool in ("mutate_psmiles", "save_discovery_state") + _ALLOWED_WHILE_BLOCKED:
            return ""
        return (
            f"Iteration {state.iteration} is complete. Present the Step 7 checkpoint and wait "
            f"for the user. If they approve, call mine_literature with iteration={following} "
            "or mutate_psmiles."
        )

    def _screen_failure(self, arguments: Mapping[str, Any], state: ProtocolState) -> str:
        if not state.validated:
            return "Validate at least one PSMILES with validate_psmiles first."
        candidates = _as_list(arguments.get("psmiles_list"))
        if not candidates:
            return "Pass the validated PSMILES as psmiles_list."
        missing = [p for p in candidates if p not in state.validated]
        if missing:
            return (
                "These PSMILES were not validated with validate_psmiles: "
                + ", ".join(missing)
                + ". Validate each one, or leave it out."
            )
        return ""

    def _openmm_failure(self, arguments: Mapping[str, Any], state: ProtocolState) -> str:
        if not state.screen:
            return "Screen the validated candidates with screen_candidate_library first."
        candidates = _as_list(arguments.get("psmiles_list"))
        if len(candidates) != 1:
            return "Pass exactly one PSMILES per openmm_evaluate_psmiles call."
        psmiles = candidates[0]
        if state.openmm_unsaved and state.openmm_unsaved != psmiles:
            return (
                f"Save the OpenMM result for {state.openmm_unsaved} with "
                "save_pipeline_stage(stage=\"openmm\") before evaluating the next candidate."
            )
        eligible = state.eligible()
        if psmiles not in eligible:
            return (
                f"{psmiles} is not an eligible screening row. OpenMM takes pass rows, or "
                "warning rows only when nothing passed. Eligible: "
                + (", ".join(eligible) or "none")
                + "."
            )
        if psmiles not in state.openmm_runs and len(state.openmm_runs) >= MAX_OPENMM_CANDIDATES:
            return f"At most {MAX_OPENMM_CANDIDATES} candidates go to OpenMM per iteration."
        return ""

    def _prepare_failure(self, arguments: Mapping[str, Any], state: ProtocolState) -> str:
        if state.openmm_unsaved:
            return (
                f"Save the OpenMM result for {state.openmm_unsaved} with "
                "save_pipeline_stage(stage=\"openmm\") first."
            )
        if not state.openmm_runs:
            return "Run openmm_evaluate_psmiles on a passing candidate and save it first."
        target = _clean(arguments.get("target"))
        eligible = state.eligible()
        if target not in eligible:
            return (
                "prepare_retrosynthesis takes a passing polymer PSMILES from screening. "
                "Eligible: " + (", ".join(eligible) or "none") + "."
            )
        if target not in state.retro_prepared and len(state.retro_prepared) >= MAX_RETRO_TARGETS:
            return f"At most {MAX_RETRO_TARGETS} polymers go to retrosynthesis per iteration."
        return ""

    def _report_failure(self, tool: str, arguments: Mapping[str, Any], state: ProtocolState) -> str:
        if tool == "assemble_retrosynthesis_report":
            return self._retro_incomplete(state)
        if tool == "save_discovery_state":
            if not state.done("assemble_retrosynthesis_report") and not state.no_eligible():
                return "Finish retrosynthesis and call assemble_retrosynthesis_report first."
            iteration = _as_int(arguments.get("iteration"), state.iteration)
            if iteration != state.iteration:
                return f"This is iteration {state.iteration}; pass iteration={state.iteration}."
            return ""
        position = _REPORT_SEQUENCE.index(tool)
        previous = _REPORT_SEQUENCE[position - 1]
        if not state.done(previous):
            return f"Call {previous} first."
        return ""

    def _retro_incomplete(self, state: ProtocolState) -> str:
        if state.no_eligible():
            return "No candidate passed screening, so there is no retrosynthesis to assemble."
        if state.openmm_unsaved:
            return (
                f"Save the OpenMM result for {state.openmm_unsaved} with "
                "save_pipeline_stage(stage=\"openmm\") first."
            )
        if not state.retro_planned:
            return (
                "Run retrosynthesis (prepare_retrosynthesis, submit_retro_extractions, "
                "plan_retrosynthesis) on at least one passing polymer first."
            )
        unchecked = [p for p in state.retro_planned if p not in state.compliance_checked]
        if unchecked:
            return "Call check_excipient_compliance on: " + ", ".join(unchecked) + "."
        unsaved = [p for p in state.retro_planned if p not in state.retro_saved]
        if unsaved:
            return (
                "Save the retrosynthesis disposition with "
                "save_pipeline_stage(stage=\"retrosynthesis\") for: " + ", ".join(unsaved) + "."
            )
        return ""

    def _retro_target(self, arguments: Mapping[str, Any], state: ProtocolState) -> Optional[str]:
        """Map a ``target`` PSMILES or ``material_name`` back to a prepared polymer."""
        target = _clean(arguments.get("target")) or _clean(arguments.get("candidate_psmiles"))
        if target in state.retro_prepared:
            return target
        names = {
            _clean(arguments.get("material_name")).lower(),
            target.lower(),
        } - {""}
        for psmiles, material_name in state.retro_prepared.items():
            if material_name and material_name.strip().lower() in names:
                return psmiles
        return None

    # -- recording ----------------------------------------------------------

    def record(self, tool: str, arguments: Mapping[str, Any], result: str) -> str:
        """Update the stage machine from *result* and attach the protocol envelope."""
        payload = _parse(result)
        if tool in _UNGATED_TOOLS:
            return result
        if tool == BOOTSTRAP_TOOL:
            return _attach(result, payload, self._record_bootstrap(arguments, payload))
        if tool == "start_biologics_session":
            return _attach(result, payload, self._record_session_start(payload))

        state = self._state_for(arguments)
        if state is None:
            if tool == "resolve_biologic_target":
                return _attach(result, payload, self._record_resolve(payload))
            return _attach(result, payload, self.envelope(None, after=tool))

        failure = _infrastructure_failure(payload, result)
        if failure:
            state.blocked_tool = tool
            state.blocked_error = failure
        else:
            if state.blocked_tool == tool:
                state.blocked_tool = ""
                state.blocked_error = ""
            if state.at_checkpoint() and tool in ("mine_literature", "mutate_psmiles"):
                state = state.next_iteration()
            self._apply(tool, arguments, payload, result, state)
            self._record_recoverable(tool, payload, result, state)
        save_state(state)
        return _attach(result, payload, self.envelope(state, after=tool))

    def _record_bootstrap(self, arguments: Mapping[str, Any], payload: Any) -> Dict[str, Any]:
        client = self.client()
        client.begun = True
        needs_input = isinstance(payload, dict) and payload.get("needs_user_input") is True
        if needs_input:
            return {
                "iteration": 0,
                "stage": "onboard",
                "done": [],
                "next_required_tool": None,
                "next_arguments": {},
                "user_stop_allowed": True,
                "rule": _RULE_ONBOARD,
                "step_instructions": section("Step 1"),
            }
        fresh = ClientState(begun=True)
        fresh.biologic_target = _clean(arguments.get("biologic_target"))
        fresh.polymer_target = _clean(arguments.get("polymer_target"))
        self._clients[client_key()] = fresh
        return self.envelope(None, after=BOOTSTRAP_TOOL)

    def _record_resolve(self, payload: Any) -> Dict[str, Any]:
        """Resolution before a session: pass the resolved form on, or retry, or block."""
        client = self.client()
        ok = isinstance(payload, dict) and payload.get("fetch_ok") is True and payload.get("pdb_path")
        if ok:
            client.resolve_failures = 0
            client.resolve_error = ""
            client.resolved_target = _clean(payload.get("resolved_target")) or _clean(
                payload.get("query")
            )
            client.suggested_compute = _clean(payload.get("suggested_compute"))
            return self.envelope(None, after="resolve_biologic_target")
        client.resolve_failures += 1
        errors = payload.get("errors") if isinstance(payload, dict) else None
        client.resolve_error = "; ".join(str(e) for e in (errors or [str(payload)[:300]]))
        if client.resolve_failures > MAX_RESOLVE_RETRIES:
            return _resolve_blocked_envelope(client)
        return {
            "iteration": 0,
            "stage": "session",
            "done": [],
            "next_required_tool": "resolve_biologic_target",
            "next_arguments": {"fetch_pdb": True},
            "user_stop_allowed": False,
            "resolve_retries_left": MAX_RESOLVE_RETRIES + 1 - client.resolve_failures,
            "rule": _RULE_RESOLVE_RETRY,
            "step_instructions": section(FAILURE_SECTION),
        }

    def _record_session_start(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict) or not payload.get("session_dir") or payload.get("error"):
            return self.envelope(None)
        session = Path(str(payload["session_dir"])).expanduser().resolve()
        state = ProtocolState(session_dir=str(session))
        resolution = payload.get("biologic_resolution") or {}
        if isinstance(resolution, dict) and not resolution.get("pdb_path"):
            errors = resolution.get("errors") or ["the biologic structure could not be resolved"]
            state.blocked_tool = "start_biologics_session"
            state.blocked_error = "; ".join(str(e) for e in errors)
        if isinstance(resolution, dict):
            state.compute = _clean(resolution.get("suggested_compute"))
        save_state(state)
        client = self.client()
        client.active_session = session
        client.begun = True
        return self.envelope(state)

    def _apply(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        payload: Any,
        result: str,
        state: ProtocolState,
    ) -> None:
        # A failed route still counts as an attempt: the failure detail is the
        # scientific result the report has to show.
        if tool == "submit_retro_extractions":
            target = self._retro_target(arguments, state)
            # A rejected submission wrote nothing, so it is not an attempt: the
            # model fixes the extractions and submits again. A failed *route*
            # (plan_retrosynthesis) is a scientific result and does count.
            if target and _succeeded(tool, payload, result):
                if target not in state.retro_submitted:
                    state.retro_submitted.append(target)
                state.retro_submissions[target] = state.retro_submissions.get(target, 0) + 1
                state.retro_blocking[target] = _blocking_reactants(payload)
            return
        if tool == "plan_retrosynthesis":
            target = self._retro_target(arguments, state)
            if target:
                if target not in state.retro_planned:
                    state.retro_planned.append(target)
                state.last_planned = target
                state.retro_disposition[target] = _retro_disposition(payload)
            return
        if not _succeeded(tool, payload, result):
            return

        if tool in ("mine_literature", "mutate_psmiles"):
            state.literature = True
        elif tool == "validate_psmiles":
            for candidate in _as_list(arguments.get("psmiles"))[:1] + [_clean(payload.get("canonical"))]:
                if candidate and candidate not in state.validated:
                    state.validated.append(candidate)
        elif tool == "screen_candidate_library":
            rows = payload if isinstance(payload, list) else []
            for row in rows:
                if isinstance(row, dict) and _clean(row.get("psmiles")):
                    key = _clean(row["psmiles"])
                    state.screen[key] = str(row.get("library_disposition", "fail"))
                    if isinstance(row.get("md_ready"), bool):
                        state.md_ready[key] = row["md_ready"]
        elif tool == "openmm_evaluate_psmiles":
            psmiles = _as_list(arguments.get("psmiles_list"))[0]
            if psmiles not in state.openmm_runs:
                state.openmm_runs.append(psmiles)
            state.openmm_unsaved = psmiles
            state.openmm_disposition[psmiles] = _openmm_disposition(payload)
        elif tool == "save_pipeline_stage":
            self._apply_stage_record(arguments, state)
        elif tool == "prepare_retrosynthesis":
            material_name = payload.get("material_name", "") if isinstance(payload, dict) else ""
            state.retro_prepared[_clean(arguments.get("target"))] = str(material_name or "")
        elif tool == "check_monomers_batch":
            if state.last_planned and state.last_planned not in state.monomers_checked:
                state.monomers_checked.append(state.last_planned)
        elif tool == "check_excipient_compliance":
            psmiles = _clean(arguments.get("psmiles"))
            if psmiles and psmiles not in state.compliance_checked:
                state.compliance_checked.append(psmiles)
        elif tool in _REPORT_SEQUENCE:
            state.mark(tool)

    def _record_recoverable(
        self, tool: str, payload: Any, result: str, state: ProtocolState
    ) -> None:
        """Count a scientific or input failure that the model can fix and retry.

        These are not infrastructure failures: an unparameterizable PSMILES, an
        empty extraction set, a route the graph cannot close. The model should
        correct the input and call the tool again; only a tool that keeps failing
        stops the pipeline.
        """
        if _succeeded(tool, payload, result):
            state.failures.pop(tool, None)
            state.last_error = ""
            return
        attempts = state.failures.get(tool, 0) + 1
        state.failures[tool] = attempts
        state.last_error = _failure_text(payload, result)
        if attempts > MAX_TOOL_RETRIES:
            state.blocked_tool = tool
            state.blocked_error = (
                f"{tool} failed {attempts} times; the last error was: {state.last_error}"
            )

    def _apply_stage_record(self, arguments: Mapping[str, Any], state: ProtocolState) -> None:
        stage = _clean(arguments.get("stage")).lower()
        candidate = _clean(arguments.get("candidate_psmiles"))
        if "openmm" in stage and candidate == state.openmm_unsaved:
            state.openmm_unsaved = ""
        elif "retro" in stage:
            target = self._retro_target({"target": candidate}, state)
            if target and target not in state.retro_saved:
                state.retro_saved.append(target)

    # -- envelope -----------------------------------------------------------

    def envelope(self, state: Optional[ProtocolState], after: str = "") -> Dict[str, Any]:
        """Describe where the pipeline is and which tool must run next."""
        if state is None:
            client = self.client()
            if client.resolve_failures > MAX_RESOLVE_RETRIES:
                return _resolve_blocked_envelope(client)
            next_arguments: Dict[str, Any] = {}
            if not client.begun:
                next_tool = BOOTSTRAP_TOOL
            elif after == BOOTSTRAP_TOOL or not client.resolved_target:
                next_tool = "resolve_biologic_target"
                if client.biologic_target:
                    next_arguments = {"name_or_pdb_id": client.biologic_target, "fetch_pdb": True}
            else:
                next_tool = "start_biologics_session"
                next_arguments = {"biologic_target": client.resolved_target}
                if client.biologic_target and client.biologic_target != client.resolved_target:
                    next_arguments["biologic_name"] = client.biologic_target
                if client.polymer_target:
                    next_arguments["polymer_target"] = client.polymer_target
            return {
                "iteration": 0,
                "stage": "session",
                "done": [],
                "next_required_tool": next_tool,
                "next_arguments": next_arguments,
                "user_stop_allowed": False,
                "rule": _RULE_CONTINUE,
                "step_instructions": step_instructions(next_tool),
            }
        retry_of = after if after and state.failures.get(after) else ""
        stage, next_tool, next_arguments, hint = _next_step(state)
        if retry_of and not state.blocked_tool:
            # The step did not advance, so _next_step still points at it. Say so
            # explicitly instead of letting the model read a bare error and stop.
            next_tool, next_arguments = retry_of, dict(arguments_for_retry(retry_of, next_arguments))
        if next_tool and "run_dir" not in next_arguments:
            next_arguments = {**next_arguments, "run_dir": state.session_dir}
        envelope: Dict[str, Any] = {
            "iteration": state.iteration,
            "stage": stage,
            "done": _done_labels(state),
            "next_required_tool": next_tool,
            "next_arguments": next_arguments,
            "user_stop_allowed": stage in ("blocked", "checkpoint"),
            "run_dir": state.session_dir,
        }
        if stage == "blocked":
            envelope["blocked_tool"] = state.blocked_tool
            envelope["blocked_error"] = state.blocked_error
            envelope["rule"] = _RULE_BLOCKED
            envelope["step_instructions"] = section(FAILURE_SECTION)
        elif stage == "checkpoint":
            envelope["rule"] = (
                f"Iteration {state.iteration} is complete. Present the Step 7 checkpoint with "
                "this run's values and wait for the user. If they approve iteration "
                f"{state.iteration + 1}, call mine_literature(iteration={state.iteration + 1}) "
                "or mutate_psmiles."
            )
        else:
            envelope["rule"] = f"{_RULE_CONTINUE} {hint}".strip()
        if retry_of and not state.blocked_tool:
            attempts = state.failures[retry_of]
            envelope["recoverable_failure"] = {
                "tool": retry_of,
                "attempt": attempts,
                "retries_left": MAX_TOOL_RETRIES + 1 - attempts,
                "error": state.last_error,
            }
            envelope["rule"] = (
                f"{retry_of} did not succeed: {state.last_error} This is not an infrastructure "
                "failure and not a reason to stop. Fix what you sent (a different PSMILES, "
                "complete extractions, a registered precursor, another candidate) and call it "
                f"again. {MAX_TOOL_RETRIES + 1 - attempts} attempt(s) left before the pipeline stops."
            )
        if stage == "checkpoint":
            envelope["step_instructions"] = section("Step 7")
        elif next_tool:
            envelope["step_instructions"] = step_instructions(next_tool)
        return envelope


def arguments_for_retry(tool: str, suggested: Dict[str, Any]) -> Dict[str, Any]:
    """Arguments to suggest when *tool* is retried after a recoverable failure."""
    return suggested if suggested else {}


def _resolve_blocked_envelope(client: ClientState) -> Dict[str, Any]:
    return {
        "iteration": 0,
        "stage": "blocked",
        "done": [],
        "next_required_tool": None,
        "next_arguments": {},
        "user_stop_allowed": True,
        "blocked_tool": "resolve_biologic_target",
        "blocked_error": client.resolve_error,
        "rule": (
            f"{_RULE_BLOCKED} Structure resolution failed {client.resolve_failures} times; ask "
            "the user for a PDB ID with chains, a UniProt accession, or a sequence, then call "
            "begin_biologix_discovery with it."
        ),
        "step_instructions": section(FAILURE_SECTION),
    }


def _next_step(state: ProtocolState) -> Tuple[str, Optional[str], Dict[str, Any], str]:
    """Return (stage, next tool, suggested arguments, hint) for *state*."""
    if state.blocked_tool:
        return "blocked", None, {}, ""
    if state.at_checkpoint():
        return "checkpoint", None, {}, ""
    if not state.literature:
        return "literature", "mine_literature", {"iteration": state.iteration}, ""
    if not state.validated:
        return (
            "validate",
            "validate_psmiles",
            {},
            "Write a PSMILES for each literature candidate (at most six) and validate each.",
        )
    if not state.screen:
        return (
            "screen",
            "screen_candidate_library",
            {"psmiles_list": ",".join(state.validated)},
            "Screen the validated PSMILES you want to keep in one call.",
        )
    eligible = state.eligible()
    if eligible:
        step = _simulation_and_retro_step(state, eligible)
        if step is not None:
            return step
    for tool in _REPORT_SEQUENCE:
        if tool == "assemble_retrosynthesis_report" and not eligible:
            continue
        if not state.done(tool):
            hint = ""
            if not eligible and tool == "save_discovery_state":
                hint = (
                    "No candidate passed screening. Write and validate new PSMILES and screen "
                    "again, or record the exclusions with save_discovery_state."
                )
            stage = "transcript" if tool == "save_session_transcript" else "report"
            arguments: Dict[str, Any] = {}
            if tool == "assemble_retrosynthesis_report":
                arguments = {"targets": ",".join(state.retro_planned)}
            elif tool == "save_discovery_state":
                arguments = {"iteration": state.iteration}
            return stage, tool, arguments, hint
    return "checkpoint", None, {}, ""


def _simulation_and_retro_step(
    state: ProtocolState, eligible: List[str]
) -> Optional[Tuple[str, Optional[str], Dict[str, Any], str]]:
    if state.openmm_unsaved:
        return (
            "openmm",
            "save_pipeline_stage",
            {
                "candidate_psmiles": state.openmm_unsaved,
                "stage": "openmm",
                "disposition": state.openmm_disposition.get(state.openmm_unsaved, "pass"),
            },
            "Save this OpenMM result before the next candidate; put the energies or the "
            "failure reason in detail.",
        )
    wanted = min(MAX_OPENMM_CANDIDATES, len(eligible))
    if len(state.openmm_runs) < wanted:
        pending = next(p for p in state.openmm_queue(eligible) if p not in state.openmm_runs)
        arguments: Dict[str, Any] = {
            "psmiles_list": pending,
            "max_workers": 1,
            "response_format": "concise",
        }
        if state.compute:
            arguments["compute"] = state.compute
        return "openmm", "openmm_evaluate_psmiles", arguments, ""
    for target in state.retro_targets():
        material_name = state.retro_prepared.get(target, "")
        if target not in state.retro_prepared:
            return "retrosynthesis", "prepare_retrosynthesis", {"target": target}, ""
        if target not in state.retro_submitted:
            return (
                "retrosynthesis",
                "submit_retro_extractions",
                {"target": target, "material_name": material_name},
                "Write the reaction extractions from the literature returned by prepare_retrosynthesis.",
            )
        blocking = state.retro_blocking.get(target) or []
        retries_left = MAX_RETRO_RETRIES + 1 - state.retro_submissions.get(target, 0)
        if target not in state.retro_planned and blocking and retries_left > 0:
            return (
                "retrosynthesis",
                "diagnose_retro_extractions",
                {"target": target, "material_name": material_name},
                f"Blocking reactants: {', '.join(blocking)}. Diagnose, then register a "
                "commercial precursor with register_retro_precursors or add the upstream "
                f"reaction and call submit_retro_extractions again ({retries_left} retries left).",
            )
        if target not in state.retro_planned:
            return "retrosynthesis", "plan_retrosynthesis", {"target": target}, ""
        if target not in state.monomers_checked:
            return "retrosynthesis", "check_monomers_batch", {}, "Screen the route monomers."
        if target not in state.compliance_checked:
            return "retrosynthesis", "check_excipient_compliance", {"psmiles": target}, ""
        if target not in state.retro_saved:
            return (
                "retrosynthesis",
                "save_pipeline_stage",
                {
                    "candidate_psmiles": target,
                    "stage": "retrosynthesis",
                    "disposition": state.retro_disposition.get(target, "warning"),
                },
                "Record the retrosynthesis disposition with the route or failure detail.",
            )
    return None


def _done_labels(state: ProtocolState) -> List[str]:
    labels = ["session"]
    if state.literature:
        labels.append("literature")
    if state.validated:
        labels.append("validate")
    if state.screen:
        labels.append("screen")
    if state.openmm_runs and not state.openmm_unsaved:
        labels.append("openmm")
    if state.retro_saved:
        labels.append("retrosynthesis")
    labels.extend(tool for tool in _REPORT_SEQUENCE if state.done(tool))
    return labels


def _blocking_reactants(payload: Any) -> List[str]:
    """Blocking reactants from submit_retro_extractions (nested under ``validation``)."""
    if not isinstance(payload, dict):
        return []
    validation = payload.get("validation")
    blocking = payload.get("blocking_reactants")
    if blocking is None and isinstance(validation, dict):
        blocking = validation.get("blocking_reactants")
    return [str(b) for b in blocking or []]


def _failure_text(payload: Any, result: str) -> str:
    """Short reason a tool call failed, for the retry rule."""
    if isinstance(payload, dict):
        for key in ("error", "reason", "hint"):
            if payload.get(key):
                return str(payload[key])[:300]
    return str(result).strip()[:300]


def _openmm_disposition(payload: Any) -> str:
    """``pass`` when every OpenMM candidate completed, else ``fail``."""
    outcomes = payload.get("candidate_outcomes") if isinstance(payload, dict) else None
    if not outcomes:
        return "fail"
    return "pass" if all(o.get("status") == "completed" for o in outcomes if isinstance(o, dict)) else "fail"


def _retro_disposition(payload: Any) -> str:
    """``pass`` when a polymer route was planned, else ``fail``."""
    if not isinstance(payload, dict):
        return "fail"
    if payload.get("error") or payload.get("ok") is False:
        return "fail"
    routes = payload.get("polymer_routes") or payload.get("routes") or []
    return "pass" if routes else "fail"


def _infrastructure_failure(payload: Any, result: str) -> str:
    """Return the error text when a tool failed for a non-scientific reason."""
    if payload is None:
        text = result.strip()
        return text[:500] if text.startswith("Error") else ""
    if not isinstance(payload, dict):
        return ""
    error = str(payload.get("error") or "")
    if payload.get("abort") is True or payload.get("stage") == "timeout" or payload.get("traceback"):
        return error or "tool failed"
    if any(marker in error for marker in _INFRASTRUCTURE_MARKERS):
        return error
    return ""


def _succeeded(tool: str, payload: Any, result: str) -> bool:
    if payload is None:
        return not result.strip().startswith("Error")
    if isinstance(payload, list):
        return True
    if tool == "validate_psmiles":
        return payload.get("valid") is True
    if payload.get("error"):
        return False
    return payload.get("ok", True) is not False


def _attach(result: str, payload: Any, envelope: Dict[str, Any]) -> str:
    """Attach *envelope* as ``protocol`` without changing the tool's own fields."""
    if isinstance(payload, dict):
        out = dict(payload)
        out["protocol"] = envelope
        return json.dumps(out, indent=2, default=str)
    if isinstance(payload, list):
        return json.dumps({"results": payload, "protocol": envelope}, indent=2, default=str)
    return f"{result.rstrip()}\n\n{json.dumps({'protocol': envelope}, default=str)}"


def _is_gated(fn: Callable[..., Any]) -> bool:
    current: Any = fn
    while current is not None:
        if getattr(current, "_biologix_protocol_gate", False):
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def _bound_arguments(sig: inspect.Signature, args: tuple, kwargs: dict) -> Dict[str, Any]:
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _wrap_tool_fn(name: str, fn: Callable[..., Any], gate: ProtocolGate) -> Callable[..., str]:
    sig = inspect.signature(fn)

    takes_run_dir = "run_dir" in sig.parameters

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        arguments = _bound_arguments(sig, args, kwargs)
        if takes_run_dir and not _clean(arguments.get("run_dir")) and name not in _SESSION_TOOLS:
            # Clients often drop run_dir; the tool must still write into this
            # client's session, never into another user's.
            active = gate.client().active_session
            if active is not None:
                kwargs["run_dir"] = str(active)
                arguments["run_dir"] = str(active)
        if name == "start_biologics_session" and "biologic_name" in sig.parameters:
            # Keep the user's name for the biologic when the session is started
            # from a resolved form such as 4ZGM:B (literature and screening use it).
            known = gate.client().biologic_target
            if known and not _clean(arguments.get("biologic_name")) and known != _clean(
                arguments.get("biologic_target")
            ):
                kwargs["biologic_name"] = known
                arguments["biologic_name"] = known
        refusal = gate.check(name, arguments)
        if refusal is not None:
            return json.dumps(refusal, indent=2, default=str)
        result = fn(*args, **kwargs)
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return gate.record(name, arguments, text)

    setattr(wrapped, "_biologix_protocol_gate", True)
    return wrapped


def install_protocol_gate(mcp: Any, gate: Optional[ProtocolGate] = None) -> ProtocolGate:
    """Wrap every registered synchronous FastMCP tool with *gate*.

    Install before ``install_stdio_guards`` so the serialization lock also
    covers state updates. Re-installing is a no-op for already gated tools.
    """
    active_gate = gate if gate is not None else ProtocolGate()
    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    if not isinstance(tools, dict):
        return active_gate
    for name, tool in tools.items():
        fn = getattr(tool, "fn", None)
        if not callable(fn) or _is_gated(fn) or inspect.iscoroutinefunction(fn):
            continue
        tool.fn = _wrap_tool_fn(str(name), fn, active_gate)
    return active_gate
