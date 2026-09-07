import json
import re
import zlib

from biologix_ai.platform.reporting import audit_bytes, json_bytes, report_pdf


RESULTS = {
    "summary": {"disposition": "review", "biologic_target": "insulin", "psmiles": "[*]OCC[*]"},
    "validation": {"valid": True},
    "safety": {"safe": True},
    "compliance": {"overall_status": "flagged", "jurisdictions_matched": ["FDA"]},
    "monomer_safety": [{"name": "ethylene oxide", "smiles": "C1CO1", "safe": False,
                        "warnings": ["IARC Group 1 carcinogen"]}],
    "retrosynthesis": {"status": "completed", "result": {
        "polymer_routes": [{
            "target_polymer": "poly(ethylene glycol)",
            "polymerization_type": "anionic_ring_opening",
            "steps": [{"reactant_names": ["ethylene oxide"], "product_name": "poly(ethylene glycol)",
                       "conditions": "KOH, 120 C"}],
            "monomers": [{"name": "ethylene oxide", "smiles": "C1CO1", "source": "purchasable_bundled"}],
        }],
        "metadata": {"route_provenance": "session_agent_llm"},
    }},
    "physics": {"status": "completed", "result": {"results": {
        "md_results_raw": [{"interaction_energy_kj_mol": -142.77, "method": "OpenMM MD"}]}}},
    "capabilities": {"rdkit": True},
}


def pdf_text(data: bytes) -> str:
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            chunks.append(zlib.decompress(match.group(1)).decode("latin-1"))
        except zlib.error:
            continue
    return "\n".join(re.findall(r"\((.*?)\) *Tj", "\n".join(chunks))).replace("\\", "")


def test_generated_artifact_formats_are_valid():
    assert json.loads(json_bytes(RESULTS))["summary"]["biologic_target"] == "insulin"
    event = {"sequence": 1, "stage": "queued"}
    assert json.loads(audit_bytes([event]).decode().strip()) == event
    assert report_pdf("Insulin screen", RESULTS).startswith(b"%PDF")


def test_report_contains_every_scientific_stage():
    text = pdf_text(report_pdf("Insulin screen", RESULTS))
    for heading in ("Assessment", "Safety and regulatory compliance", "Retrosynthesis",
                    "Residual monomer safety screen", "Molecular physics", "Scientific runtime"):
        assert heading in text, f"{heading} missing from report"


def test_report_carries_the_route_the_worker_computed():
    text = pdf_text(report_pdf("Insulin screen", RESULTS))
    assert "ethylene oxide -> poly(ethylene glycol)" in text
    assert "Conditions: KOH, 120 C" in text
    assert "session agent llm" in text


def test_report_carries_physics_and_monomer_findings():
    text = pdf_text(report_pdf("Insulin screen", RESULTS))
    assert "-142.77 kJ/mol" in text
    assert "IARC Group 1 carcinogen" in text


def test_report_states_plainly_when_no_route_was_found():
    text = pdf_text(report_pdf("Empty", {"retrosynthesis": {"status": "requires_input"}}))
    assert "requires input" in text
    assert "No synthesis route was established" in text


def test_report_survives_unicode_and_empty_results():
    assert report_pdf("Insulin – PEG → screen", {}).startswith(b"%PDF")
    assert pdf_text(report_pdf("x", {"summary": {"biologic_target": "α-amylase"}}))
