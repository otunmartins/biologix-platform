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
}

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


def lookup_pdb_id(name_or_pdb: str) -> str:
    """Map a common name to a PDB ID, or pass through a valid PDB code."""
    raw = name_or_pdb.strip()
    if not raw:
        return ""
    if _PDB_CODE_RE.match(raw):
        return raw.upper()
    key = _normalize_name_key(raw)
    return _NAME_TO_PDB.get(key, "")


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
