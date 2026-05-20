"""Adapt agent-produced extractions to RetroSynthesisAgent llm_res file layout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace, extractions_manifest_path


def resolve_material_name(target_or_name: str) -> str:
    """Canonical searchable polymer name for workspace paths and RetroSyn tree root."""
    from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target

    resolved = resolve_retro_target((target_or_name or "").strip())
    return resolved["material_name"] or (target_or_name or "").strip()


def tree_root_name(material_name: str) -> str:
    return material_name.strip().lower()


def _mentions_product(text: str, material_name: str) -> bool:
    needle = tree_root_name(material_name)
    if not needle:
        return False
    for line in text.splitlines():
        if line.strip().lower().startswith("products:"):
            products = line.split(":", 1)[-1].lower()
            if needle in products:
                return True
    return False


def ensure_root_product_in_extractions(
    extractions: Dict[str, str],
    material_name: str,
) -> Tuple[Dict[str, str], bool]:
    """Ensure at least one reaction lists the target polymer as a product (Tree root).

    Returns (extractions, used_connector).
    """
    root = tree_root_name(material_name)
    if any(_mentions_product(text, material_name) for text in extractions.values()):
        return extractions, False
    connector = (
        f"Reaction 001:\n"
        f"Reactants: precursor monomer\n"
        f"Products: {root}\n"
        f"Conditions: polymerization (connector for tree root)"
    )
    out = dict(extractions)
    out["_root_connector"] = connector
    return out, True


def validate_extractions_for_tree(
    extractions: Dict[str, str],
    material_name: str,
    *,
    used_connector: bool = False,
) -> Dict[str, Any]:
    root = tree_root_name(material_name)
    root_found = any(_mentions_product(text, material_name) for text in extractions.values())
    warnings: list[str] = []
    if used_connector:
        warnings.append(
            f"No reaction listed {material_name!r} in Products:; injected _root_connector. "
            "Re-extract with Products containing the polymer name for literature-KG routes."
        )
    elif not root_found:
        warnings.append(
            f"No reaction lists Products containing {root!r}; RetroSyn tree may return no paths."
        )
    return {
        "root_product_found": root_found or used_connector,
        "tree_root": root,
        "used_root_connector": used_connector,
        "paper_count": len(extractions),
        "warnings": warnings,
    }


def normalize_extractions(raw: Any) -> Dict[str, str]:
    """Validate and normalize extractions to {paper_name: reaction_text}."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("extractions must be a JSON object mapping paper_name -> reaction_text")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("each paper_name key must be a non-empty string")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"reaction text for {key!r} must be a non-empty string")
        text = value.strip()
        if "Final Output:" in text:
            text = text.split("Final Output:")[-1].strip()
        out[key.strip()] = text
    if not out:
        raise ValueError("extractions must contain at least one paper entry")
    return out


def write_llm_res(
    session_dir: Path,
    material_name: str,
    extractions: Dict[str, str],
    *,
    ensure_tree_root: bool = True,
) -> Tuple[Path, bool]:
    """Write llm_res.json and llm_res_modified.json for EntityAlignment fast-path.

    Returns (path, used_connector).
    """
    used_connector = False
    if ensure_tree_root:
        extractions, used_connector = ensure_root_product_in_extractions(
            extractions, material_name
        )
    dirs = ensure_workspace(session_dir, material_name)
    result_folder = dirs["results"]
    llm_path = result_folder / "llm_res.json"
    modified_path = result_folder / "llm_res_modified.json"
    payload = json.dumps(extractions, indent=4, ensure_ascii=False)
    llm_path.write_text(payload, encoding="utf-8")
    modified_path.write_text(payload, encoding="utf-8")

    manifest_path = extractions_manifest_path(session_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: list = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = []
    if not isinstance(manifest, list):
        manifest = []
    entry = {
        "material_name": material_name,
        "workspace": str(dirs["workspace"]),
        "llm_res": str(llm_path),
        "paper_count": len(extractions),
    }
    manifest = [e for e in manifest if e.get("material_name") != material_name]
    manifest.append(entry)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return llm_path, used_connector


def session_has_extractions(session_dir: Path, material_name: str) -> bool:
    dirs = ensure_workspace(session_dir, material_name)
    llm_path = dirs["results"] / "llm_res.json"
    if not llm_path.is_file():
        return False
    try:
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and len(data) > 0
    except json.JSONDecodeError:
        return False
