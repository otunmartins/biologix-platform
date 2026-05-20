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
        "Each reaction_text uses RetroSynAgent prompt_reaction_extraction_cot format."
    ),
    "example_entry": {
        "paper_title_here": (
            "Reaction 001:\n"
            "Reactants: acrylic acid\n"
            "Products: poly(acrylic acid)\n"
            "Conditions: RAFT polymerization, Catalyst: AIBN, 60°C, 24h"
        ),
    },
    "format_reference": "Reaction NNN:\\nReactants: ...\\nProducts: ...\\nConditions: ...",
}
