import io
import json
from typing import Any

from fpdf import FPDF


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")


def audit_bytes(events: list[dict]) -> bytes:
    return b"".join(json.dumps(event, sort_keys=True).encode("utf-8") + b"\n" for event in events)


def report_pdf(name: str, results: dict) -> bytes:
    summary = results.get("summary") or {}
    compliance = results.get("compliance") or {}
    safety = results.get("safety") or {}
    capabilities = results.get("capabilities") or {}
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, "Scientific experiment report", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    sections = [
        ("Assessment", [
            f"Disposition: {summary.get('disposition', 'review')}",
            f"Biologic target: {summary.get('biologic_target', '')}",
            f"PDB identifier: {summary.get('pdb_id') or 'not resolved'}",
            f"Polymer target: {summary.get('polymer_target', '')}",
            f"PSMILES: {summary.get('psmiles', '')}",
        ]),
        ("Safety and compliance", [
            f"Safety screen: {'passed' if safety.get('safe') else 'review required'}",
            f"Compliance status: {compliance.get('overall_status', 'unknown')}",
            f"Approved match: {compliance.get('approved_name') or 'none'}",
            f"Jurisdictions: {', '.join(compliance.get('jurisdictions_matched') or []) or 'none'}",
        ]),
        ("Scientific capabilities", [f"{key}: {value}" for key, value in capabilities.items()]),
    ]
    for title, lines in sections:
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        for line in lines:
            pdf.multi_cell(0, 6, str(line), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return bytes(pdf.output())
