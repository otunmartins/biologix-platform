"""Session-scoped RetroSynthesisAgent workspace paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def material_slug(material_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", material_name.lower()).strip("_")
    return slug or "material"


def workspace_path(session_dir: Path, material_name: str) -> Path:
    return session_dir / "retrosynthesis" / "retro_workspace" / material_slug(material_name)


def ensure_workspace(session_dir: Path, material_name: str) -> dict[str, Path]:
    ws = workspace_path(session_dir, material_name)
    pdf_folder = ws / "pdfs"
    result_folder = ws / "results"
    tree_folder = ws / "trees"
    for d in (pdf_folder, result_folder, tree_folder):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "workspace": ws,
        "pdfs": pdf_folder,
        "results": result_folder,
        "trees": tree_folder,
    }


def extractions_manifest_path(session_dir: Path) -> Path:
    return session_dir / "retrosynthesis" / "extractions_manifest.json"


EXTRACTION_SCHEMA = {
    "description": (
        "JSON object mapping paper_name (str) -> reaction_text (str). "
        "Each reaction_text uses RetroSynAgent prompt_reaction_extraction_cot format. "
        "Include at least 2 reactions when polymerization reactants are not commodity chemicals: "
        "Reaction 001 = polymerization step (specialty monomer → polymer); "
        "Reaction 002 = upstream synthesis (commodity → specialty monomer)."
    ),
    "example_single_step": {
        "Smith2020_acrylic": (
            "Reaction 001:\n"
            "Reactants: acrylic acid\n"
            "Products: poly(acrylic acid)\n"
            "Conditions: RAFT polymerization, Catalyst: AIBN, 60°C, 24h"
        ),
    },
    "example_multi_step_plga": {
        "Zhang2022_PLGA_synthesis": (
            "Reaction 001:\n"
            "Reactants: lactide, glycolide\n"
            "Products: poly(lactic-co-glycolic acid)\n"
            "Conditions: ring-opening polymerization, Catalyst: tin(II) 2-ethylhexanoate, "
            "130°C, 6h, initiator: benzyl alcohol\n\n"
            "Reaction 002:\n"
            "Reactants: lactic acid\n"
            "Products: lactide\n"
            "Conditions: condensation and cyclization, 180°C, reduced pressure"
        ),
    },
    "example_multi_step_chitosan": {
        "Rinaudo2006_chitosan": (
            "Reaction 001:\n"
            "Reactants: chitin\n"
            "Products: chitosan\n"
            "Conditions: alkaline deacetylation, 50% NaOH, 100°C, 2h\n\n"
            "Reaction 002:\n"
            "Reactants: n-acetylglucosamine\n"
            "Products: chitin\n"
            "Conditions: enzymatic polymerization or biosynthesis"
        ),
    },
    "format_reference": (
        "Reaction NNN:\\nReactants: ...\\nProducts: ...\\nConditions: ... "
        "(field labels must use capital R/P/C; Products in at least one reaction "
        "must include the target polymer name; add upstream reactions for specialty intermediates)"
    ),
}
