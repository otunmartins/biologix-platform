"""Resolve a biologic name or PDB code to a local PDB path for OpenMM matrix screening."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

RSCB_DOWNLOAD = "https://files.rcsb.org/download/{pdb_id}.pdb"

# Representative structures (Fab fragments or single-chain where noted).
# Users can override by passing an explicit PDB ID (e.g. 6WC1).
_NAME_TO_PDB: Dict[str, str] = {
    "insulin": "4F1C",
    "insulin lispro": "4F1C",
    "human insulin": "4F1C",
    "adalimumab": "3WD5",
    "humira": "3WD5",
    "trastuzumab": "1N8Z",
    "herceptin": "1N8Z",
    "bevacizumab": "1BJ1",
    "rituximab": "2OSL",
    "infliximab": "4G5P",
    "etanercept": "2AZP",
    "pembrolizumab": "5JXE",
    "nivolumab": "5WT9",
    "ustekinumab": "3HMW",
    "omalizumab": "4HKI",
    "cas9": "4CMP",
    "crispr cas9": "4CMP",
    "crispr-cas9": "4CMP",
    "streptococcus pyogenes cas9": "4CMP",
    # Common non-antibody biologics. "Lysozyme" ships as an example in the
    # experiment form, so it must resolve without a network round trip.
    "lysozyme": "1LYZ",
    "hen egg white lysozyme": "1LYZ",
    "human lysozyme": "1REX",
    "albumin": "1AO6",
    "human serum albumin": "1AO6",
    "hsa": "1AO6",
    "somatropin": "1HGU",
    "human growth hormone": "1HGU",
    "hgh": "1HGU",
    "erythropoietin": "1BUY",
    "epo": "1BUY",
    "interferon alpha": "1ITF",
    "interferon beta": "1AU1",
    "filgrastim": "1RHG",
    "g-csf": "1RHG",
    "glucagon": "1GCN",
    "factor viii": "2R7E",
    "asparaginase": "3ECA",
    "urate oxidase": "1R4U",
    "rasburicase": "1R4U",
}

_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
_search_cache: Dict[str, str] = {}

_PDB_CODE_RE = re.compile(r"^([0-9]{1}[A-Za-z0-9]{3})$", re.IGNORECASE)


class BiologicTarget(BaseModel):
    """Resolved biologic target for session + OpenMM."""

    query: str = Field(description="Original user input (name or PDB ID)")
    canonical_name: str = Field(default="", description="Normalized display name when known")
    pdb_id: str = Field(description="4-character PDB ID (lowercase)")
    pdb_path: str = Field(default="", description="Absolute path to local PDB file if fetched or bundled")
    organism: str = Field(default="", description="Hint only; not always populated")
    description: str = Field(default="", description="Short note")
    from_cache: bool = Field(default=False, description="True if file already existed locally")
    fetch_ok: bool = Field(default=False, description="True if PDB file is present and readable")
    errors: list[str] = Field(default_factory=list)

    def model_dump_public(self) -> Dict[str, Any]:
        return self.model_dump()


def _normalize_name_key(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _search_rcsb(name: str) -> str:
    """Best-match PDB entry from the RCSB full-text index.

    Without this the resolver only knew a fixed handful of names and returned an
    empty id for everything else, leaving the physics stage with no structure.
    """
    if name in _search_cache:
        return _search_cache[name]
    payload = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": name},
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": 1},
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }
    found = ""
    try:
        response = requests.post(_SEARCH_URL, json=payload, timeout=10)
        if response.ok:
            results = response.json().get("result_set") or []
            if results:
                found = str(results[0].get("identifier", "")).upper()
    except Exception as exc:
        logger.warning("RCSB search failed for %r: %s", name, exc)
    _search_cache[name] = found
    return found


def resolve_pdb_entry(name_or_pdb: str, allow_search: bool = True) -> Dict[str, Any]:
    """Resolve a biologic to a PDB entry and report how the answer was reached.

    Claude picks the structure the field uses and the entry is read back from RCSB to
    confirm it exists and holds a protein. The name table and the full-text search are
    fallbacks for when the model cannot be reached, and each is labelled.
    """
    raw = (name_or_pdb or "").strip()
    if not raw:
        return {"pdb_id": "", "provenance": "none", "canonical_name": ""}
    if _PDB_CODE_RE.match(raw):
        return {"pdb_id": raw.upper(), "provenance": "user_supplied", "canonical_name": raw.upper()}

    try:
        from biologix_ai.llm.client import LLMUnavailable
        from biologix_ai.llm.structures import resolve_biologic

        resolved = resolve_biologic(raw)
        return {
            "pdb_id": resolved["pdb_id"],
            "canonical_name": resolved["canonical_name"],
            "provenance": "llm_verified",
            "confidence": resolved["confidence"],
            "rationale": resolved["rationale"],
            "entry": resolved["entry"],
            "model": resolved["model"],
        }
    except LLMUnavailable as exc:
        fallback_reason = str(exc)
    except ValueError as exc:
        fallback_reason = str(exc)
    except ImportError as exc:
        fallback_reason = f"structure resolution unavailable: {exc}"
    logger.warning("Falling back from Claude structure resolution for %r: %s", raw, fallback_reason)

    key = _normalize_name_key(raw)
    curated = _NAME_TO_PDB.get(key, "")
    if curated:
        return {
            "pdb_id": curated.upper(),
            "canonical_name": raw,
            "provenance": "offline_cache",
            "fallback_reason": fallback_reason,
        }
    if not allow_search:
        return {"pdb_id": "", "provenance": "none", "canonical_name": raw, "fallback_reason": fallback_reason}
    found = _search_rcsb(raw)
    return {
        "pdb_id": found.upper() if found else "",
        "canonical_name": raw,
        "provenance": "rcsb_text_search" if found else "none",
        "fallback_reason": fallback_reason,
    }


def lookup_pdb_id(name_or_pdb: str, allow_search: bool = True) -> str:
    """Map a common name to a PDB ID, or pass through a valid PDB code."""
    return resolve_pdb_entry(name_or_pdb, allow_search=allow_search)["pdb_id"]


def _default_bundled_pdb(repo_root: Path, pdb_id: str) -> Optional[Path]:
    """Prefer packaged simulation data when present (e.g. insulin 4F1C)."""
    pid = pdb_id.upper()
    for sub in (
        repo_root / "src" / "python" / "biologix_ai" / "simulation" / "data" / f"{pid}.pdb",
        repo_root / "data" / f"{pid}.pdb",
    ):
        if sub.is_file():
            return sub.resolve()
    return None


def _validate_pdb_file(path: Path) -> tuple[bool, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "ATOM" in text[:20000] or "HETATM" in text[:20000]:
            return True, ""
        return False, "no ATOM/HETATM records found"
    except OSError as e:
        return False, str(e)


def resolve_biologic_target(
    name_or_pdb: str,
    repo_root: Path,
    session_dir: Optional[Path] = None,
    fetch_pdb: bool = True,
    cache_dir: Optional[Path] = None,
) -> BiologicTarget:
    """
    Resolve biologic to a PDB file path.

    - Looks up common names in ``_NAME_TO_PDB``.
    - Accepts explicit 4-character PDB IDs.
    - Checks bundled ``simulation/data`` and ``<repo>/data``.
    - Optionally downloads from RCSB into ``session_dir/structures`` or ``cache_dir``.
    """
    err: list[str] = []
    raw = (name_or_pdb or "").strip()
    if not raw:
        return BiologicTarget(
            query=raw,
            pdb_id="",
            errors=["empty name_or_pdb"],
        )

    pdb_id = lookup_pdb_id(raw)
    if not pdb_id:
        pdb_id = raw.upper() if _PDB_CODE_RE.match(raw) else ""
    if not pdb_id:
        return BiologicTarget(
            query=raw,
            pdb_id="",
            errors=[f"unknown biologic name: {raw!r}; pass a 4-letter PDB ID"],
        )

    canonical = raw if _PDB_CODE_RE.match(raw) else next(
        (k.title() for k, v in _NAME_TO_PDB.items() if v.upper() == pdb_id.upper()),
        raw,
    )

    bundled = _default_bundled_pdb(Path(repo_root), pdb_id)
    if bundled is not None:
        ok, msg = _validate_pdb_file(bundled)
        if ok:
            return BiologicTarget(
                query=raw,
                canonical_name=canonical,
                pdb_id=pdb_id.upper(),
                pdb_path=str(bundled),
                description="bundled package data",
                from_cache=True,
                fetch_ok=True,
            )
        err.append(f"bundled PDB invalid: {msg}")

    dest_dir: Path
    if session_dir is not None:
        dest_dir = Path(session_dir) / "structures"
    elif cache_dir is not None:
        dest_dir = Path(cache_dir)
    else:
        dest_dir = Path(repo_root) / "data" / "biologics"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"biologic_{pdb_id.upper()}.pdb"

    if dest.is_file():
        ok, msg = _validate_pdb_file(dest)
        if ok:
            return BiologicTarget(
                query=raw,
                canonical_name=canonical,
                pdb_id=pdb_id.upper(),
                pdb_path=str(dest.resolve()),
                description="cached download",
                from_cache=True,
                fetch_ok=True,
            )
        err.append(f"cached file invalid: {msg}")

    if not fetch_pdb:
        return BiologicTarget(
            query=raw,
            canonical_name=canonical,
            pdb_id=pdb_id.upper(),
            errors=err + ["fetch_pdb=False and no local PDB found"],
        )

    url = RSCB_DOWNLOAD.format(pdb_id=pdb_id.upper())
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        dest.write_bytes(r.content)
    except Exception as exc:
        logger.warning("RCSB download failed for %s: %s", pdb_id, exc)
        return BiologicTarget(
            query=raw,
            canonical_name=canonical,
            pdb_id=pdb_id.upper(),
            errors=err + [f"download failed: {exc}"],
        )

    ok, msg = _validate_pdb_file(dest)
    if not ok:
        return BiologicTarget(
            query=raw,
            canonical_name=canonical,
            pdb_id=pdb_id.upper(),
            errors=err + [msg or "downloaded file invalid"],
        )

    return BiologicTarget(
        query=raw,
        canonical_name=canonical,
        pdb_id=pdb_id.upper(),
        pdb_path=str(dest.resolve()),
        description=f"downloaded from RCSB ({url})",
        from_cache=False,
        fetch_ok=True,
    )
