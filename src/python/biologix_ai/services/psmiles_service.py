"""PSMILES service facade for HTTP API and shared callers.

Wraps material_mappings and psmiles library helpers used by MCP tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from biologix_ai.material_mappings import (
    annotate_functional_groups,
    check_name_structure_consistency,
    lookup_monomer_pubchem,
    name_to_psmiles,
    validate_psmiles as _validate_psmiles,
)


def validate_psmiles(
    psmiles: Union[str, List[str]],
    material_name: str = "",
    crosscheck_web: bool = False,
) -> Dict[str, Any]:
    """Validate and annotate a PSMILES string (MCP validate_psmiles parity)."""
    if isinstance(psmiles, list):
        psm = str(psmiles[0]).strip() if psmiles else ""
    else:
        psm = str(psmiles).strip()

    out = dict(_validate_psmiles(psm))
    fg = annotate_functional_groups(psm)
    if fg.get("ok"):
        out["functional_groups"] = fg["groups"]
    else:
        out["functional_groups_error"] = fg.get("error", "unknown")

    name = (material_name or "").strip()
    if name:
        out["name_consistency"] = check_name_structure_consistency(name, psm)
        try:
            out["pubchem_lookup"] = lookup_monomer_pubchem(name, psm, timeout=5.0)
        except Exception as exc:
            out["pubchem_lookup"] = {"ok": False, "error": str(exc)}

    if crosscheck_web and name:
        try:
            from biologix_ai.services.literature_service import web_search_results

            q = f"{name} polymer repeat unit SMILES structure"
            raw = web_search_results(q, max_results=5)
            snippets = [
                {
                    "title": (r.get("title") or "")[:120],
                    "snippet": (r.get("body") or r.get("snippet", ""))[:500],
                    "url": r.get("href") or r.get("link", ""),
                }
                for r in raw
            ]
            out["name_crosscheck"] = {
                "material_name": name,
                "query": q,
                "snippets": snippets,
                "disclaimer": (
                    "Web snippets are for human/agent review only. They do not prove the PSMILES "
                    "matches the material name; compare chemistry carefully."
                ),
                "psmiles_submitted": psm.strip()[:200],
            }
        except Exception as exc:
            out["name_crosscheck"] = {"error": str(exc)}
    elif crosscheck_web and not name:
        out["name_crosscheck"] = {"error": "crosscheck_web requires a non-empty material_name"}

    return out


def generate_psmiles_from_name(material_name: str) -> Dict[str, Any]:
    """Convert polymer/monomer name to PSMILES."""
    return name_to_psmiles(material_name)


def mutate_psmiles(
    library_size: int = 10,
    feedback: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Generate mutated PSMILES candidates."""
    from biologix_ai.mutation import MaterialMutator, feedback_guided_mutation

    feedback = feedback or {}
    if feedback.get("high_performer_psmiles"):
        cands = feedback_guided_mutation(feedback, library_size=library_size, random_seed=42)
    else:
        mutator = MaterialMutator(random_seed=42)
        cands = mutator.generate_library(library_size=library_size)
    return [
        {"material_name": c["material_name"], "chemical_structure": c["chemical_structure"]}
        for c in cands
    ]


def _psmiles_check() -> Optional[str]:
    try:
        from psmiles import PolymerSmiles  # noqa: F401

        return None
    except ImportError:
        return "psmiles not installed. Use biologix-ai-sim env or: pip install git+https://github.com/FermiQ/psmiles.git"


def canonicalize_psmiles(psmiles: str) -> str:
    """Canonicalize PSMILES via Ramprasad psmiles library."""
    err = _psmiles_check()
    if err:
        raise RuntimeError(err)
    from psmiles import PolymerSmiles

    ps = PolymerSmiles(psmiles)
    c = ps.canonicalize
    if callable(c):
        c = c()
    return str(c)


def dimerize_psmiles(psmiles: str, star_index: int = 0) -> str:
    """Dimerize PSMILES at connection point."""
    err = _psmiles_check()
    if err:
        raise RuntimeError(err)
    from psmiles import PolymerSmiles

    ps = PolymerSmiles(psmiles)
    if hasattr(ps, "dimer"):
        return str(ps.dimer(star_index))
    return str(ps.dimerize(star_index=star_index))


def fingerprint_psmiles(psmiles: str, fingerprint_type: str = "rdkit") -> Any:
    """Compute PSMILES fingerprint."""
    err = _psmiles_check()
    if err:
        raise RuntimeError(err)
    from psmiles import PolymerSmiles

    fp = PolymerSmiles(psmiles).descriptor(fingerprint_type)
    if hasattr(fp, "tolist"):
        return fp.tolist()
    return str(fp)


def similarity_psmiles(psmiles1: str, psmiles2: str) -> float:
    """Compute similarity between two PSMILES."""
    err = _psmiles_check()
    if err:
        raise RuntimeError(err)
    from psmiles import PolymerSmiles

    return float(PolymerSmiles(psmiles1).similarity(PolymerSmiles(psmiles2)))


def render_psmiles_png(
    psmiles: str,
    session_dir: Path,
    output_basename: str = "",
) -> Dict[str, Any]:
    """Render 2D PNG of polymer repeat unit under session structures/."""
    err = _psmiles_check()
    if err:
        raise RuntimeError(err)
    from biologix_ai.psmiles_drawing import safe_filename_basename, save_psmiles_png

    session_dir.mkdir(parents=True, exist_ok=True)
    struct = session_dir / "structures"
    struct.mkdir(parents=True, exist_ok=True)
    base = (output_basename or "").strip() or safe_filename_basename(psmiles[:80])
    out = struct / f"{base}.png"
    result = save_psmiles_png(psmiles.strip(), out, overwrite=True)
    return {**result, "session_dir": str(session_dir), "relative": f"structures/{out.name}"}
