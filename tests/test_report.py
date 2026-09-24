"""The session report: PDF layout, content, and the writing-style gate."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

PIL = pytest.importorskip("PIL")
pytest.importorskip("fpdf")

from PIL import Image  # noqa: E402

from biologix_ai.report import style_check  # noqa: E402
from biologix_ai.report.builder import GENERATED_MARKER, build_report  # noqa: E402
from biologix_ai.report.pdf_render import render_markdown_to_pdf  # noqa: E402

needs_node = pytest.mark.skipif(style_check.node_executable() is None, reason="node not installed")
needs_poppler = pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler not installed")

PSMILES = "[*]CC(C)(C(=O)OCC[N+](C)(C)CCCS(=O)(=O)[O-])[*]"


def _png(path: Path, size=(400, 300), color=(30, 90, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _ink_in_margins(pdf: Path, tmp: Path, margin_mm: float = 17.0) -> int:
    """Count non-white pixels left of the left margin or right of the right one (footer excluded)."""
    subprocess.run(["pdftoppm", "-r", "50", "-png", str(pdf), str(tmp / "pg")], check=True)
    dark = 0
    for page in sorted(tmp.glob("pg-*.png")):
        im = Image.open(page).convert("L")
        w, h = im.size
        px = int(margin_mm / 25.4 * 50)
        body = h - int(14 / 25.4 * 50)  # the footer sits below the body
        for x in list(range(0, px)) + list(range(w - px, w)):
            for y in range(0, body):
                if im.getpixel((x, y)) < 200:
                    dark += 1
    return dark


# -- PDF layout ---------------------------------------------------------------------


@needs_poppler
def test_an_oversized_image_is_scaled_into_the_margins(tmp_path):
    """The complaint: a figure ran off the right edge of the page."""
    _png(tmp_path / "structures" / "big.png", size=(3200, 2400))
    md = "# T\n\nText.\n\n![Complex](structures/big.png)\n\nMore text.\n"
    result = render_markdown_to_pdf(md, tmp_path / "r.pdf", tmp_path)
    assert result.figures == 1 and not result.missing_images
    assert _ink_in_margins(tmp_path / "r.pdf", tmp_path) == 0


@needs_poppler
def test_a_wide_image_and_a_long_unbroken_string_stay_inside_the_margins(tmp_path):
    _png(tmp_path / "wide.png", size=(4000, 300))
    long_psmiles = "[*]" + "CC(C)(C(=O)OCC[N+](C)(C)CCCS(=O)(=O)[O-])" * 6 + "[*]"
    md = f"# T\n\n**PSMILES:** {long_psmiles}\n\n![w](wide.png)\n\n| a | b |\n|---|---|\n| x | {long_psmiles} |\n"
    render_markdown_to_pdf(md, tmp_path / "r.pdf", tmp_path)
    assert _ink_in_margins(tmp_path / "r.pdf", tmp_path) == 0


def test_the_same_image_is_drawn_once(tmp_path):
    """The complaint: one figure appeared twice. Two paths to identical pixels count as one."""
    _png(tmp_path / "a.png")
    shutil.copy(tmp_path / "a.png", tmp_path / "b.png")
    md = "# T\n\n![first](a.png)\n\n![second](b.png)\n\n![again](a.png)\n"
    result = render_markdown_to_pdf(md, tmp_path / "r.pdf", tmp_path)
    assert result.figures == 1
    assert sorted(result.duplicate_figures_skipped) == ["a.png", "b.png"]


def test_a_missing_image_is_reported_not_fatal(tmp_path):
    result = render_markdown_to_pdf("# T\n\n![x](nope.png)\n", tmp_path / "r.pdf", tmp_path)
    assert result.missing_images == ["nope.png"] and (tmp_path / "r.pdf").is_file()


def test_a_figure_and_its_heading_stay_together_across_a_page_break(tmp_path):
    _png(tmp_path / "f.png", size=(900, 900))
    filler = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(14))
    md = f"# T\n\n{filler}\n\n### Candidate\n\n![Only figure](f.png)\n"
    result = render_markdown_to_pdf(md, tmp_path / "r.pdf", tmp_path)
    assert result.figures == 1 and result.pages >= 2


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler not installed")
def test_typographic_characters_survive_into_the_pdf(tmp_path):
    """The core PDF fonts turned these into '?'."""
    md = "# Denosumab — screen\n\nEnergy −3931.2 kJ/mol, radius ≥ 5 Å, ΔG.\n"
    render_markdown_to_pdf(md, tmp_path / "r.pdf", tmp_path)
    text = subprocess.run(["pdftotext", str(tmp_path / "r.pdf"), "-"], capture_output=True, text=True).stdout
    assert "−3931.2" in text and "Å" in text and "ΔG" in text and "?" not in text


# -- the built report ----------------------------------------------------------------


def _session(tmp_path: Path, *, notes: str = "Only the isolated VH domain was modeled.") -> Path:
    s = tmp_path / "denosumab_screen"
    (s / "structures").mkdir(parents=True)
    (s / "audit").mkdir()
    (s / "retrosynthesis").mkdir()
    (s / "structures" / "biologic_target.json").write_text(
        json.dumps({"canonical_name": "denosumab", "resolved_target": "sequence:EVQLL", "source": "esmfold",
                    "chains": ["A"], "n_residues": 122, "n_atoms": 1797})
    )
    _png(s / "structures" / "000_PSBMA.png", color=(10, 10, 10))
    _png(s / "structures" / "Candidate_0_monomer.png", color=(10, 10, 10))  # the same drawing again
    _png(s / "structures" / "Candidate_0_complex_chemviz.png", size=(2400, 1800), color=(200, 20, 20))
    (s / "structures" / "Candidate_0_complex_meta.json").write_text(json.dumps({"psmiles": PSMILES}))
    (s / "agent_iteration_1.json").write_text(
        json.dumps(
            {
                "iteration": 1,
                "notes": notes,
                "feedback": {
                    "high_performers": ["PSBMA, exploratory candidate only"],
                    "effective_mechanisms": ["Favorable calculated interaction energy"],
                    "problematic_features": ["Isolated VH domain, not the full antibody"],
                    "property_analysis": {"PSBMA": {"psmiles": PSMILES, "interaction_energy_kj_mol": -3931.18,
                                                    "retrosynthesis_disposition": "fail: target mismatch"}},
                    "references": ["Macromolecules 1999 32 2141-2148 DOI 10.1021/ma980543h"],
                },
            }
        )
    )
    rows = [
        {"candidate_psmiles": PSMILES, "stage": "validation", "disposition": "pass", "detail": "{}"},
        {"candidate_psmiles": PSMILES, "stage": "compliance", "disposition": "fail", "detail": "{}"},
        {"candidate_psmiles": PSMILES, "stage": "openmm", "disposition": "pass",
         "detail": json.dumps({"interaction_energy_kj_mol": -3931.18, "platform": "CPU 8 threads",
                               "target": "ESMFold VH, 122 residues"})},
    ]
    (s / "audit" / "pipeline_audit.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (s / "retrosynthesis" / "RETROSYNTHESIS_REPORT.md").write_text(
        "## Retrosynthesis\n\n### Route\n\n- Monomer: `CC(=C)C(=O)OCCN(C)C`\n\n"
        "| Step | Reactants |\n|---|---|\n| 1 | DMAEMA |\n\n"
        "*Artifact: `/__modal/volumes/vo-x/denosumab_screen/retrosynthesis/plan_1.json`*\n"
    )
    return s


def test_the_report_uses_the_whole_session(tmp_path):
    session = _session(tmp_path)
    md, info = build_report(session)
    for heading in ("## Summary", "## Method", "## Candidates (1)", "## What the results support",
                    "## Limitations and open problems", "## Retrosynthesis", "## References", "## Files"):
        assert heading in md
    assert md.startswith(GENERATED_MARKER)
    assert "−3,931.2" in md and "CPU 8 threads" in md
    assert "DMAEMA" in md and "Monomer:" in md                    # the retrosynthesis report is in it
    assert "/__modal" not in md and "Artifact:" not in md         # no server paths
    assert "did not pass review" in md                            # a failed route is flagged as such
    assert info["n_simulated"] == 1


def test_long_notes_are_kept_whole(tmp_path):
    """The old report cut notes at 200 characters and printed 'Singl'."""
    notes = "Only isolated 122-residue VH model. " * 12
    md, _ = build_report(_session(tmp_path, notes=notes))
    assert notes.strip() in md


def test_each_figure_appears_once_in_the_markdown_and_the_pdf(tmp_path):
    session = _session(tmp_path)
    md, _ = build_report(session)
    assert md.count("![") == 2  # repeat unit and complex; the identical 000_PSBMA drawing is not listed too
    (session / "SUMMARY_REPORT.md").write_text(md, encoding="utf-8")
    from biologix_ai.discovery_report import compile_markdown_to_pdf

    out = compile_markdown_to_pdf(session)
    assert out["ok"] and out["figures"] == 2 and not out.get("missing_images")


@needs_node
def test_the_text_the_builder_writes_itself_passes_the_style_check(tmp_path):
    md, _ = build_report(_session(tmp_path))
    generated_only = "\n".join(
        block for block in md.split("\n\n") if block.startswith(("This report covers", "Each polymer repeat"))
    )
    assert generated_only
    assert style_check.analyze(generated_only)["issues"] == []


# -- the style gate --------------------------------------------------------------------

AI_TEXT = (
    "In today's rapidly evolving landscape, it's worth noting that this comprehensive, robust "
    "screen seamlessly leverages zwitterionic interactions. Moreover, the results are transformative."
)
PLAIN_TEXT = (
    "The interaction energy was -3931.2 kJ/mol on the CPU platform. Only the VH domain was modeled, "
    "so the result says nothing about the full antibody."
)


def test_mechanical_fixes_leave_code_alone():
    text = "Result — the energy “improved”.\n\n`a — b`\n\n```\nx — y\n```\n"
    fixed, changes = style_check.mechanical_fixes(text)
    assert "—" not in fixed.split("`")[0] and '"improved"' in fixed
    assert "`a — b`" in fixed and "x — y" in fixed
    assert changes


@needs_node
def test_ai_sounding_prose_is_sent_back_with_findings():
    result = style_check.review(AI_TEXT)
    assert result["revise"] is True and result["publish"] is False
    assert {f["text"] for f in result["findings"]} >= {"comprehensive", "seamlessly", "Moreover"}


@needs_node
def test_plain_technical_prose_passes():
    result = style_check.review(PLAIN_TEXT)
    assert result["revise"] is False and result["findings_count"] == 0


@needs_node
def test_after_the_allowed_rewrites_the_text_is_published_with_a_note():
    result = style_check.review(AI_TEXT, attempt=style_check.MAX_REVISIONS)
    assert result["publish"] is True and result["revise"] is False and "published as written" in result["note"]


def test_a_missing_detector_never_blocks_the_report(monkeypatch):
    monkeypatch.setattr(style_check, "node_executable", lambda: None)
    result = style_check.review(AI_TEXT)
    assert result["publish"] is True and result["detector_available"] is False


@needs_node
def test_the_tool_asks_for_a_rewrite_then_writes_after_two_rounds(tmp_path, monkeypatch):
    import biologix_ai_mcp_server as server

    session = _session(tmp_path)
    monkeypatch.setattr(server, "_session_dir_for_mcp", lambda run_dir="": session)
    first = json.loads(server.write_discovery_summary_report(narrative=AI_TEXT))
    assert first["error"] == "STYLE_REVISION_REQUESTED" and first["not_a_failure"] is True
    assert not (session / "SUMMARY_REPORT.md").exists()          # nothing written yet
    second = json.loads(server.write_discovery_summary_report(narrative=AI_TEXT))
    assert second["error"] == "STYLE_REVISION_REQUESTED"
    third = json.loads(server.write_discovery_summary_report(narrative=AI_TEXT))
    assert third["ok"] is True and (session / "SUMMARY_REPORT.pdf").is_file()
    assert third["style"]["rewrites_requested"] == 2


@needs_node
def test_a_rewrite_that_is_plain_goes_straight_through(tmp_path, monkeypatch):
    import biologix_ai_mcp_server as server

    session = _session(tmp_path)
    monkeypatch.setattr(server, "_session_dir_for_mcp", lambda run_dir="": session)
    done = json.loads(server.write_discovery_summary_report(narrative=PLAIN_TEXT))
    assert done["ok"] is True
    assert PLAIN_TEXT in (session / "SUMMARY_REPORT.md").read_text(encoding="utf-8")
