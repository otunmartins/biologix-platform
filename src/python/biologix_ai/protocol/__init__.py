"""The Biologix discovery protocol shared by every MCP client.

``PROTOCOL.md`` is the single source of the agent instructions. The server
returns it from ``begin_biologix_discovery``, the ``biologix_discovery`` prompt
and the ``biologix://protocol`` resource; the protocol gate quotes the section
for the current step in every result; ``scripts/build_client_adapters.py``
renders it into Cursor, Claude Code, Antigravity, ChatGPT, and Grok setups.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict

PROTOCOL_PATH = Path(__file__).resolve().parent / "PROTOCOL.md"
STEP_INSTRUCTIONS_MAX_CHARS = 1500
_LOCAL_ONLY_MARKERS = ("CLI-only", "./install", "MCP_CLI_FALLBACK")

# The protocol section each tool belongs to.
TOOL_SECTIONS: Dict[str, str] = {
    "begin_biologix_discovery": "Step 1",
    "resolve_biologic_target": "Step 2",
    "start_biologics_session": "Step 2",
    "mine_literature": "Step 3",
    "validate_psmiles": "Step 3",
    "screen_candidate_library": "Step 4",
    "openmm_evaluate_psmiles": "Step 4",
    "save_pipeline_stage": "Step 4",
    "prepare_retrosynthesis": "Step 5",
    "submit_retro_extractions": "Step 5",
    "diagnose_retro_extractions": "Step 5",
    "register_retro_precursors": "Step 5",
    "plan_retrosynthesis": "Step 5",
    "check_monomers_batch": "Step 5",
    "check_excipient_compliance": "Step 5",
    "assemble_retrosynthesis_report": "Step 6",
    "save_discovery_state": "Step 6",
    "write_discovery_summary_report": "Step 6",
    "compile_discovery_markdown_to_pdf": "Step 6",
    "save_funnel_context": "Step 6",
    "save_session_transcript": "Step 7",
    "mutate_psmiles": "Step 7",
    "await_biologix_job": "How to drive",
}


@lru_cache(maxsize=1)
def load_protocol() -> str:
    """The protocol text. Raises when it contains instructions for local installs."""
    text = PROTOCOL_PATH.read_text(encoding="utf-8").strip()
    local_only = [marker for marker in _LOCAL_ONLY_MARKERS if marker in text]
    if local_only:
        raise RuntimeError("PROTOCOL.md contains local-only instructions: " + ", ".join(local_only))
    return text


@lru_cache(maxsize=None)
def _sections() -> Dict[str, str]:
    """``{"Step 4": "## Step 4 — Screen ...", "Failure policy": ...}`` keyed by heading prefix."""
    out: Dict[str, str] = {}
    heading, lines, in_fence = "", [], False
    for line in load_protocol().splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if line.startswith("## ") and not in_fence:
            if heading:
                out[heading] = "\n".join(lines).strip()
            heading, lines = line[3:].split(" —")[0].strip(), []
        lines.append(line)
    if heading:
        out[heading] = "\n".join(lines).strip()
    return out


def section(key: str) -> str:
    """One protocol section by heading prefix (``"Step 5"``, ``"Failure policy"``)."""
    for heading, text in _sections().items():
        if heading.startswith(key):
            return _clip(text)
    return ""


def step_instructions(tool: str) -> str:
    """The section an agent needs before calling *tool* (empty for unknown tools)."""
    key = TOOL_SECTIONS.get(tool or "", "")
    return section(key) if key else ""


def _clip(text: str) -> str:
    if len(text) <= STEP_INSTRUCTIONS_MAX_CHARS:
        return text
    cut = text[: STEP_INSTRUCTIONS_MAX_CHARS].rsplit("\n", 1)[0]
    return cut + "\n… (full protocol: begin_biologix_discovery or the biologix://protocol resource)"
