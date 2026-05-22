"""Adapt agent-produced extractions to RetroSynthesisAgent llm_res file layout."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from biologix_ai.retrosynthesis.retro_workspace import ensure_workspace, extractions_manifest_path

_REACTION_HEADER = re.compile(r"^Reaction\s+\d+:\s*$", re.IGNORECASE)
_PSMILES_SUFFIX = re.compile(r"\s+\[\*\]\S*")


def resolve_material_name(target_or_name: str, agent_provided_name: str = "") -> str:
    """Resolve polymer identity for workspace paths and RetroSyn tree root.

    Priority: agent_provided_name > material_mappings lookup > target as-is.
    Never returns raw PSMILES when a human name is available.
    """
    if agent_provided_name and "[*]" not in agent_provided_name:
        return agent_provided_name.strip()

    from biologix_ai.retrosynthesis.psmiles_bridge import resolve_retro_target

    resolved = resolve_retro_target((target_or_name or "").strip())
    name = resolved["material_name"] or (target_or_name or "").strip()

    if "[*]" in name and "[*]" not in (target_or_name or ""):
        return (target_or_name or "").strip()

    return name


def _looks_like_smiles(s: str) -> bool:
    """Heuristic: SMILES has no spaces and contains chemistry characters."""
    if " " in s:
        return False
    if not re.search(r"[CNOcno=\[\]#]", s):
        return False
    return True


def _strip_trailing_smiles_parens(tok: str) -> str:
    """Remove trailing parenthetical SMILES annotation from a token."""
    tok = tok.strip()
    while tok.endswith(")"):
        idx = tok.rfind(" (")
        if idx < 0:
            break
        inner = tok[idx + 2 : -1]
        if _looks_like_smiles(inner):
            tok = tok[:idx].strip()
        else:
            break
    return tok


def _strip_smiles_annotations(val: str) -> str:
    """Strip parenthetical SMILES from comma-separated tokens."""
    tokens = val.split(", ")
    clean = [_strip_trailing_smiles_parens(tok) for tok in tokens]
    return " " + ", ".join(clean)


def normalize_for_tree_root(text: str, material_name: str) -> str:
    """Normalize extraction text so product/reactant tokens are clean names.

    - Strips PSMILES suffixes (e.g. ' [*]CC([*])...' after a name)
    - Strips inline SMILES annotations (e.g. ' (C=CC(=O)Cl)' after a name)
    - Ensures the final-product reaction uses material_name exactly
    """
    root = material_name.strip().lower()
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("products:"):
            val = stripped.split(":", 1)[1]
            val = _PSMILES_SUFFIX.sub("", val)
            val = _strip_smiles_annotations(val)
            if root in val.lower():
                tokens = [t.strip() for t in val.split(",")]
                tokens = [material_name if root in t.lower() else t for t in tokens]
                line = "Products: " + ", ".join(tokens)
            else:
                line = "Products:" + val
        elif stripped.lower().startswith("reactants:"):
            val = stripped.split(":", 1)[1]
            val = _PSMILES_SUFFIX.sub("", val)
            val = _strip_smiles_annotations(val)
            line = "Reactants:" + val
        lines.append(line)
    return "\n".join(lines)


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


def require_root_product_in_extractions(
    extractions: Dict[str, str],
    material_name: str,
) -> None:
    """Raise ValueError when no reaction lists the target polymer in Products:."""
    if any(_mentions_product(text, material_name) for text in extractions.values()):
        return
    root = tree_root_name(material_name)
    raise ValueError(
        f"No extraction lists Products containing {root!r}. "
        "Each submit must include at least one reaction whose Products line "
        "contains the target polymer name (case-insensitive)."
    )


def validate_extractions_for_tree(
    extractions: Dict[str, str],
    material_name: str,
) -> Dict[str, Any]:
    root = tree_root_name(material_name)
    root_found = any(_mentions_product(text, material_name) for text in extractions.values())
    warnings: list[str] = []
    if not root_found:
        warnings.append(
            f"No reaction lists Products containing {root!r}; submission will be rejected."
        )

    # Leaf reachability pre-flight (non-blocking)
    leaf_reachability: Dict[str, Any] = {}
    blocking_reactants: list[str] = []
    suggested_reaction_count = 0
    try:
        from biologix_ai.retrosynthesis.precursor_registry import (
            collect_reactants_from_extractions,
            diagnose_leaf_reachability,
        )

        reactants = collect_reactants_from_extractions(extractions)
        leaf_reachability = diagnose_leaf_reachability(reactants)
        blocking_reactants = [n for n, s in leaf_reachability.items() if s["blocking"]]
        if blocking_reactants:
            suggested_reaction_count = len(blocking_reactants)
            warnings.append(
                f"Leaf coverage warning: {len(blocking_reactants)} reactant(s) not yet in "
                f"purchasable database: {blocking_reactants}. Consider adding upstream "
                "reaction steps or calling register_retro_precursors before plan_retrosynthesis."
            )
    except Exception:
        pass

    return {
        "root_product_found": root_found,
        "tree_root": root,
        "paper_count": len(extractions),
        "warnings": warnings,
        "leaf_reachability": leaf_reachability,
        "blocking_reactants": blocking_reactants,
        "suggested_reaction_count": suggested_reaction_count,
    }


def _normalize_field_line(line: str, field: str) -> Tuple[str, bool]:
    """Rewrite reactants/products/conditions line to RetroSyn canonical capitalization."""
    stripped = line.strip()
    prefix = f"{field}:"
    if stripped.lower().startswith(prefix.lower()):
        value = line.split(":", 1)[-1] if ":" in line else ""
        return f"{field}:{value}", True
    return line, False


def normalize_reaction_text(text: str) -> Tuple[str, dict]:
    """Per-paper normalization: fix field labels, ensure Conditions:, count reactions."""
    stats: dict = {
        "reactions_in": 0,
        "reactions_out": 0,
        "blocks_missing_conditions": 0,
    }
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if _REACTION_HEADER.match(line.strip()):
            if current:
                blocks.append(current)
            current = [line.strip()]
            stats["reactions_in"] += 1
        elif current:
            current.append(line)
        elif line.strip():
            current = [line]

    if current:
        blocks.append(current)

    if stats["reactions_in"] == 0 and blocks:
        stats["reactions_in"] = len(blocks)

    out_blocks: list[str] = []
    for block in blocks:
        normalized_lines: list[str] = []
        has_reactants = has_products = has_conditions = False
        for line in block:
            new_line, matched = _normalize_field_line(line, "Reactants")
            if matched:
                has_reactants = True
                normalized_lines.append(new_line)
                continue
            new_line, matched = _normalize_field_line(line, "Products")
            if matched:
                has_products = True
                normalized_lines.append(new_line)
                continue
            new_line, matched = _normalize_field_line(line, "Conditions")
            if matched:
                has_conditions = True
                normalized_lines.append(new_line)
                continue
            normalized_lines.append(line)

        if has_reactants and has_products and not has_conditions:
            normalized_lines.append("Conditions: not specified")
            stats["blocks_missing_conditions"] += 1
        if has_reactants and has_products:
            stats["reactions_out"] += 1
        out_blocks.append("\n".join(normalized_lines))

    return "\n\n".join(out_blocks), stats


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
        normalized, _stats = normalize_reaction_text(text)
        out[key.strip()] = normalized
    if not out:
        raise ValueError("extractions must contain at least one paper entry")
    return out


def write_llm_res(
    session_dir: Path,
    material_name: str,
    extractions: Dict[str, str],
    *,
    ensure_tree_root: bool = True,
    target_psmiles: str = "",
) -> Tuple[Path, dict]:
    """Write llm_res.json and llm_res_modified.json for EntityAlignment fast-path.

    Returns (path, parse_stats aggregated across papers).
    """
    aggregate_stats = {
        "reactions_in": 0,
        "reactions_out": 0,
        "blocks_missing_conditions": 0,
    }
    normalized: Dict[str, str] = {}
    for key, text in extractions.items():
        norm_text, stats = normalize_reaction_text(text)
        normalized[key] = normalize_for_tree_root(norm_text, material_name)
        for k in aggregate_stats:
            aggregate_stats[k] += stats.get(k, 0)

    if ensure_tree_root:
        require_root_product_in_extractions(normalized, material_name)

    dirs = ensure_workspace(session_dir, material_name)
    result_folder = dirs["results"]
    llm_path = result_folder / "llm_res.json"
    modified_path = result_folder / "llm_res_modified.json"
    payload = json.dumps(normalized, indent=4, ensure_ascii=False)
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
        "target_psmiles": target_psmiles or None,
        "workspace": str(dirs["workspace"]),
        "llm_res": str(llm_path),
        "paper_count": len(normalized),
        "parse_stats": aggregate_stats,
    }
    manifest = [e for e in manifest if e.get("material_name") != material_name]
    manifest.append(entry)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return llm_path, aggregate_stats


def infer_polymer_name_from_extractions(
    extractions: Dict[str, str],
    target_psmiles: str = "",
) -> Optional[str]:
    """Infer human polymer name from Products lines when target is unmapped PSMILES."""
    psmiles_lower = (target_psmiles or "").strip().lower()
    for text in extractions.values():
        for line in text.splitlines():
            if not line.strip().lower().startswith("products:"):
                continue
            val = line.split(":", 1)[1]
            val_lower = val.lower()
            if psmiles_lower and psmiles_lower not in val_lower and "[*]" not in val_lower:
                continue
            val_clean = _PSMILES_SUFFIX.sub("", val).strip()
            for tok in val_clean.split(","):
                tok = tok.strip()
                if tok.lower().startswith("poly(") and "[*]" not in tok:
                    return tok
    return None


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
