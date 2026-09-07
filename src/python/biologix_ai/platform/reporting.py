import io
import json
from typing import Any

from fpdf import FPDF


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")


def audit_bytes(events: list[dict]) -> bytes:
    return b"".join(json.dumps(event, sort_keys=True).encode("utf-8") + b"\n" for event in events)


_REPLACEMENTS = {
    "→": "->", "←": "<-", "↔": "<->", "⇌": "<=>",
    "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "·": ".",
    "≥": ">=", "≤": "<=", "≠": "!=", "±": "+/-",
}


def latin1(value: Any) -> str:
    """FPDF core fonts are latin-1 only; degrade gracefully instead of raising."""
    text = str(value)
    for source, target in _REPLACEMENTS.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


def _stage_label(value: Any, fallback: str = "not run") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ") if text else fallback


def _physics_records(physics: dict) -> list[dict]:
    payload = (physics.get("result") or {}).get("results")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [item for item in (payload.get("md_results_raw") or []) if isinstance(item, dict)]
    return []


def _physics_failures(physics: dict) -> list[str]:
    payload = (physics.get("result") or {}).get("results")
    if not isinstance(payload, dict):
        return []
    return [
        _stage_label(item.get("reason"), "no reason recorded")
        for item in (payload.get("evaluation_progress") or [])
        if isinstance(item, dict) and item.get("status") != "completed"
    ]


class _Report(FPDF):
    def __init__(self, title: str):
        super().__init__()
        self.report_title = latin1(title)
        self.set_auto_page_break(auto=True, margin=18)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 8, f"{self.report_title} - page {self.page_no()}", align="C")
        self.set_text_color(0)

    def heading(self, text: str):
        if self.get_y() > 240:
            self.add_page()
        self.ln(2)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 9, latin1(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_draw_color(0)
        self.ln(2)

    def field(self, label: str, value: Any):
        self.set_font("Helvetica", "B", 9)
        self.cell(46, 6, latin1(label), new_x="RIGHT", new_y="TOP")
        self.set_font("Helvetica", size=9)
        self.multi_cell(0, 6, latin1(value), new_x="LMARGIN", new_y="NEXT")

    def body(self, text: str, indent: float = 0.0, style: str = ""):
        self.set_font("Helvetica", style, 9)
        if indent:
            self.set_x(self.l_margin + indent)
        self.multi_cell(0, 5, latin1(text), new_x="LMARGIN", new_y="NEXT")

    def bullet(self, text: str, indent: float = 4.0):
        self.body(f"- {text}", indent=indent)


_STATUS_LABEL = {
    "complete": "complete",
    "degraded": "incomplete - degraded",
    "skipped": "NOT RUN",
    "failed": "FAILED",
}


def _completeness(pdf: _Report, results: dict):
    report = results.get("completeness") or {}
    stages = report.get("stages") or []
    if not stages:
        return
    pdf.heading("Run completeness")
    verdict = report.get("verdict", "unknown")
    headline = {
        "complete": "Every scientific stage produced a result.",
        "incomplete": "This run did NOT produce a complete scientific result.",
    }.get(verdict, verdict)
    pdf.field("Verdict", verdict)
    pdf.body(headline, style="B")
    for stage in stages:
        pdf.ln(1)
        pdf.body(
            f"{stage['stage'].replace('_', ' ')}: {_STATUS_LABEL.get(stage['status'], stage['status'])}",
            style="B",
        )
        for reason in stage.get("reasons") or []:
            pdf.bullet(reason, indent=6)


def _assessment(pdf: _Report, results: dict):
    summary = results.get("summary") or {}
    validation = results.get("validation") or {}
    pdf.heading("Assessment")
    pdf.field("Disposition", _stage_label(summary.get("disposition"), "review"))
    pdf.field("Biologic target", summary.get("biologic_target") or "not specified")
    pdf.field("PDB identifier", summary.get("pdb_id") or "not resolved")
    pdf.field("Polymer target", summary.get("polymer_target") or "not specified")
    pdf.field("PSMILES", summary.get("psmiles") or "not resolved")
    structure = results.get("structure") or {}
    biologic = results.get("biologic") or {}
    pdf.field("Repeat unit from", _stage_label(structure.get("provenance"), "not recorded"))
    if structure.get("model"):
        pdf.field("Resolved by", f"{structure['model']} ({structure.get('confidence', 'confidence not stated')})")
    if structure.get("fallback_reason"):
        pdf.bullet(str(structure["fallback_reason"]), indent=6)
    pdf.field("Structure entry from", _stage_label(biologic.get("provenance"), "not recorded"))
    entry = biologic.get("entry") or {}
    if entry.get("title"):
        pdf.body(f"{entry.get('pdb_id')}: {entry['title']}", indent=4, style="I")
        detail = ", ".join(
            part for part in (entry.get("method"), f"{entry['resolution_a']} A" if entry.get("resolution_a") else "")
            if part
        )
        if detail:
            pdf.body(detail, indent=4, style="I")
    if biologic.get("fallback_reason"):
        pdf.bullet(str(biologic["fallback_reason"]), indent=6)
    pdf.field("Completeness", summary.get("completeness") or "not audited")
    pdf.field("Structure", "valid" if validation.get("valid") else "review required")
    for key in ("canonical_psmiles", "molecular_weight", "error"):
        if validation.get(key):
            pdf.field(key.replace("_", " ").capitalize(), validation[key])


def _safety(pdf: _Report, results: dict):
    safety = results.get("safety") or {}
    compliance = results.get("compliance") or {}
    pdf.heading("Safety and regulatory compliance")
    pdf.field("Repeat-unit screen", "passed" if safety.get("safe") else "review required")
    for warning in safety.get("warnings") or []:
        pdf.bullet(warning)
    pdf.field("Compliance status", _stage_label(compliance.get("overall_status"), "unknown"))
    pdf.field("Approved match", compliance.get("approved_name") or "none")
    pdf.field("Jurisdictions", ", ".join(compliance.get("jurisdictions_matched") or []) or "none")
    for note in compliance.get("warnings") or compliance.get("alerts") or []:
        pdf.bullet(note)


def _retrosynthesis(pdf: _Report, results: dict):
    retro = results.get("retrosynthesis") or {}
    plan = retro.get("result") or {}
    routes = plan.get("polymer_routes") or []
    metadata = plan.get("metadata") or {}
    pdf.heading("Retrosynthesis")
    pdf.field("Status", _stage_label(retro.get("status")))
    pdf.field("Route provenance", _stage_label(metadata.get("route_provenance"), "none recorded"))
    pdf.field("Routes returned", str(len(routes)))
    if retro.get("reason"):
        pdf.field("Reason", retro["reason"])
    if metadata.get("reporting_honesty"):
        pdf.body(str(metadata["reporting_honesty"]), style="I")
    planning = retro.get("planning") or {}
    pdf.field("Evidence from", _stage_label(retro.get("evidence_source"), "not recorded"))
    if planning.get("model"):
        pdf.field("Planned by", str(planning["model"]))
    if planning.get("error"):
        pdf.bullet(str(planning["error"]), indent=6)
    sources = planning.get("sources") or []
    if sources:
        pdf.body("Literature the route was built from", style="B")
        for source in sources:
            pdf.bullet(str(source.get("name") or "unnamed source"), indent=6)
    for warning in plan.get("warnings") or []:
        pdf.bullet(warning)
    if not routes:
        pdf.body("No synthesis route was established for this target.", style="I")
        return
    for index, route in enumerate(routes, start=1):
        if pdf.get_y() > 225:
            pdf.add_page()
        pdf.ln(1)
        pdf.body(f"Route {index}: {route.get('target_polymer', 'unnamed target')}", style="B")
        if route.get("polymerization_type"):
            pdf.body(_stage_label(route["polymerization_type"]), indent=4, style="I")
        for step_index, step in enumerate(route.get("steps") or [], start=1):
            reactants = ", ".join(step.get("reactant_names") or []) or "unspecified reactants"
            pdf.body(f"{step_index}. {reactants} -> {step.get('product_name', 'unspecified product')}", indent=4)
            for label, key in (("Reaction", "reaction_type"), ("Conditions", "conditions")):
                if step.get(key):
                    pdf.body(f"{label}: {step[key]}", indent=9)
        monomers = route.get("monomers") or []
        if monomers:
            pdf.body("Monomers", indent=4, style="B")
            for monomer in monomers:
                source = _stage_label(monomer.get("source"), "source not recorded")
                pdf.body(f"- {monomer.get('name') or monomer.get('smiles')} [{monomer.get('smiles')}] ({source})", indent=9)


def _monomer_safety(pdf: _Report, results: dict):
    screened = results.get("monomer_safety") or []
    if not screened:
        return
    pdf.heading("Residual monomer safety screen")
    for monomer in screened:
        label = monomer.get("name") or monomer.get("smiles")
        verdict = "no structural or predicted ADMET alerts" if monomer.get("safe") else "review required"
        pdf.body(f"{label}: {verdict}", style="B")
        for warning in monomer.get("warnings") or []:
            pdf.bullet(warning, indent=6)


def _physics(pdf: _Report, results: dict):
    physics = results.get("physics") or {}
    pdf.heading("Molecular physics")
    pdf.field("Status", _stage_label(physics.get("status")))
    if physics.get("reason"):
        pdf.field("Reason", physics["reason"])
    sampling = physics.get("sampling") or {}
    if sampling:
        if sampling.get("npt_enabled"):
            pdf.field(
                "Sampling",
                f"{sampling.get('duration_ps')} ps constant-pressure trajectory, "
                f"{sampling.get('frames_averaged')} of {sampling.get('expected_frames')} frames averaged",
            )
        else:
            pdf.field("Sampling", "none - single minimised pose")
        pdf.field("Minimisation", f"{sampling.get('minimize_iterations')} iterations")
        if sampling.get("truncated_by_wall_clock"):
            pdf.bullet(
                f"Trajectory stopped at the {sampling.get('wall_clock_limit_s')}s wall-clock limit",
                indent=6,
            )
    records = _physics_records(physics)
    for record in records:
        energy = record.get("interaction_energy_kj_mol")
        if isinstance(energy, (int, float)):
            spread = record.get("interaction_energy_kj_mol_std")
            frames = record.get("n_frames_averaged")
            if isinstance(spread, (int, float)) and frames:
                pdf.field(
                    "Interaction energy",
                    f"{energy:.2f} +/- {spread:.2f} kJ/mol (mean of {frames} frames)",
                )
            else:
                pdf.field("Interaction energy", f"{energy:.2f} kJ/mol (single pose, no uncertainty)")
        for label, key in (("Method", "method"), ("Contacts", "insulin_polymer_contacts")):
            if record.get(key) is not None:
                pdf.field(label, record[key])
    for failure in _physics_failures(physics):
        pdf.bullet(failure)
    if not records and not physics.get("reason"):
        pdf.body("No simulation result was produced.", style="I")


def _capabilities(pdf: _Report, results: dict):
    capabilities = results.get("capabilities") or {}
    if not capabilities:
        return
    pdf.heading("Scientific runtime")
    for key, value in capabilities.items():
        pdf.field(key.replace("_", " "), value)


def report_pdf(name: str, results: dict) -> bytes:
    pdf = _Report(name)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(0, 11, latin1(name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.set_text_color(90)
    pdf.cell(0, 7, "Scientific experiment report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    for section in (_completeness, _assessment, _safety, _retrosynthesis, _monomer_safety, _physics, _capabilities):
        section(pdf, results)
    return bytes(pdf.output())
