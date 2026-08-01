import json

from biologix_ai.platform.reporting import audit_bytes, json_bytes, report_pdf


RESULTS = {
    "summary": {"disposition": "review", "biologic_target": "insulin", "psmiles": "[*]OCC[*]"},
    "safety": {"safe": True},
    "compliance": {"overall_status": "flagged", "jurisdictions_matched": ["FDA"]},
    "capabilities": {"rdkit": True},
}


def test_generated_artifact_formats_are_valid():
    assert json.loads(json_bytes(RESULTS))["summary"]["biologic_target"] == "insulin"
    event = {"sequence": 1, "stage": "queued"}
    assert json.loads(audit_bytes([event]).decode().strip()) == event
    assert report_pdf("Insulin screen", RESULTS).startswith(b"%PDF")
