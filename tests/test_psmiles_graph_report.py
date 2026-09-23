"""Tests for the PSMILES graph report returned by validate_psmiles.

The report states what a model-written PSMILES encodes (connection atoms,
ring substituents, stereocenters) so the model can compare it with the
intended polymer. It never proposes a replacement string.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from biologix_ai.material_mappings import psmiles_graph_report  # noqa: E402
from biologix_ai.services.psmiles_service import validate_psmiles as service_validate  # noqa: E402

pytest.importorskip("rdkit")

# The string the former name table returned for chitosan.
OLD_CHITOSAN = "[*]OC1C(N)C(O)C(CO)OC1[*]"
# beta-(1->4) D-glucosamine repeat unit: [*] on C1, linking O on C4, stereo defined.
GLUCOSAMINE_14 = "OC[C@H]1O[C@@H]([*])[C@H](N)[C@@H](O)[C@@H]1O[*]"


def test_old_chitosan_string_reports_wrong_linkage_and_missing_stereo() -> None:
    report = psmiles_graph_report(OLD_CHITOSAN)

    assert report["ok"] is True
    assert report["star_count"] == 2
    assert [a["position"] for a in report["attachments"]] == ["O on C2", "C1"]
    ring = report["rings"][0]
    assert ring["type"] == "pyranose"
    substituents = ring["substituents"]
    assert substituents["C1"] == ["[*]"]
    assert substituents["C2"] == ["O-[*]"]
    assert substituents["C3"] == ["NH2"]
    assert substituents["C4"] == ["OH"]
    assert substituents["C5"] == ["CH2OH"]
    assert report["unspecified_stereocenters"] == ["C1", "C2", "C3", "C4", "C5"]


def test_glucosamine_14_repeat_unit_reports_intended_graph() -> None:
    report = psmiles_graph_report(GLUCOSAMINE_14)

    assert [a["position"] for a in report["attachments"]] == ["C1", "O on C4"]
    substituents = report["rings"][0]["substituents"]
    assert substituents["C2"] == ["NH2"]
    assert substituents["C3"] == ["OH"]
    assert substituents["C4"] == ["O-[*]"]
    assert substituents["C5"] == ["CH2OH"]
    assert report["unspecified_stereocenters"] == []


def test_acyclic_repeat_unit_reports_attachment_elements_and_backbone() -> None:
    report = psmiles_graph_report("[*]OCC[*]")

    assert [a["element"] for a in report["attachments"]] == ["O", "C"]
    assert report["rings"] == []
    assert report["backbone_atoms"] == 3
    assert report["unspecified_stereocenters"] == []


def test_report_never_suggests_a_replacement_structure() -> None:
    serialized = json.dumps(psmiles_graph_report(OLD_CHITOSAN))
    assert GLUCOSAMINE_14 not in serialized
    assert "suggest" not in serialized.lower()


def test_report_rejects_unparseable_input() -> None:
    report = psmiles_graph_report("[*]C(C[*]")
    assert report["ok"] is False
    assert report["error"]


def test_validation_service_includes_graph_report() -> None:
    out = service_validate(OLD_CHITOSAN, material_name="chitosan")
    assert out["valid"] is True
    assert out["graph_report"]["unspecified_stereocenters"] == ["C1", "C2", "C3", "C4", "C5"]
