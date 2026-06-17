#!/usr/bin/env python3
"""Build precursor databases for the RetroSynAgent KG leaf-coverage expansion.

Tiers:
  Tier 1 (manual, ~244 entries): already in precursors.json — always kept.
  Tier 2 (SMiPoly, ~1,083 polymer monomers): fetched from GitHub, merged into
          precursors.json as names + SMILES.
  Tier 3 (Molport, 1M+ building blocks): downloaded from HuggingFace (resumable
          snapshot), filtered to MW ≤ 500 Da; InChIKeys computed via RDKit and
          saved to data/retrosynthesis/molport_inchikeys.pkl (gitignored).
  Tier 4 (ZINC bridge verification): verifies h5py + zinc_stock.hdf5 are
          present so the runtime ZINC InChIKey lookup is operational.

Usage:
    python scripts/build_precursor_db.py [--tiers 1,2,3,4] [--max-molport 0]

    --max-molport 0  (default) = no limit; use all Molport entries passing MW filter.

Requires for Tier 2:  pip install requests
Requires for Tier 3:  pip install huggingface-hub rdkit (rdkit is included in conda env)
Requires for Tier 4:  pip install h5py
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRECURSORS_PATH = REPO_ROOT / "data" / "retrosynthesis" / "precursors.json"
MOLPORT_INCHIKEYS_PATH = REPO_ROOT / "data" / "retrosynthesis" / "molport_inchikeys.pkl"
ZINC_STOCK_PATH = REPO_ROOT / "data" / "aizynthfinder" / "zinc_stock.hdf5"

SMIPOLY_CSV_URL = (
    "https://raw.githubusercontent.com/PEJpOhno/SMiPoly/main/"
    "sample_data/202207_smip_monset.csv"
)
MOLPORT_DATASET_ID = "molport/In-stock-Building-Block-Database"
MOLPORT_SNAPSHOT_MAX_ATTEMPTS = 5

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _canonical_smiles(smiles: str) -> Optional[str]:
    """Return RDKit canonical SMILES or None if parsing fails."""
    if not smiles or smiles.strip() in ("", "null", "None"):
        return None
    # Strip whitespace — spaces make SMILES invalid
    smiles = re.sub(r"\s+", "", smiles)
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return Chem.MolToSmiles(mol)
    except ImportError:
        return smiles
    except Exception:
        return None


def _load_current() -> dict:
    if PRECURSORS_PATH.is_file():
        try:
            data = json.loads(PRECURSORS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"version": "1.0", "source_info": {}, "entries": []}


def _dedup(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by lowercase name; secondary key = canonical SMILES."""
    seen_names: Set[str] = set()
    seen_smiles: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for e in entries:
        name_key = e.get("name", "").strip().lower()
        smiles = e.get("smiles") or ""
        csmi = _canonical_smiles(smiles) if smiles else None

        if name_key and name_key in seen_names:
            continue
        if csmi and csmi in seen_smiles:
            continue

        if name_key:
            seen_names.add(name_key)
        if csmi:
            seen_smiles.add(csmi)
        out.append(e)
    return out


# ─────────────────────────────────────────────
# Tier 2: SMiPoly
# ─────────────────────────────────────────────

def fetch_smipoly_tier() -> List[Dict[str, Any]]:
    """Download SMiPoly CSV and return entries list."""
    try:
        import requests
    except ImportError:
        logger.error("pip install requests  to fetch SMiPoly tier")
        return []

    logger.info("Fetching SMiPoly monomer set from GitHub …")
    resp = requests.get(SMIPOLY_CSV_URL, timeout=30)
    if resp.status_code != 200:
        logger.error("SMiPoly fetch failed: HTTP %s", resp.status_code)
        return []

    entries: List[Dict[str, Any]] = []
    lines = resp.text.splitlines()
    if not lines:
        return entries

    header = [h.strip().lower() for h in lines[0].split(",")]
    smiles_col = next((i for i, h in enumerate(header) if "smiles" in h), None)
    name_col = next((i for i, h in enumerate(header) if "name" in h or "compound" in h), None)
    class_col = next((i for i, h in enumerate(header) if "class" in h or "type" in h), None)

    for row in lines[1:]:
        parts = row.split(",")
        if len(parts) <= max(filter(lambda x: x is not None, [smiles_col, name_col or 0, class_col or 0])):
            continue
        smiles = parts[smiles_col].strip() if smiles_col is not None else ""
        name = parts[name_col].strip() if name_col is not None else smiles
        category = parts[class_col].strip().lower() if class_col is not None else "smipoly_monomer"
        if not name and not smiles:
            continue
        if not name:
            name = smiles
        entries.append(
            {
                "name": name.lower(),
                "aliases": [name.lower()],
                "smiles": smiles or None,
                "source": "smipoly",
                "category": category or "smipoly_monomer",
            }
        )
    logger.info("SMiPoly: loaded %d entries", len(entries))
    return entries


# ─────────────────────────────────────────────
# Tier 3: Molport building blocks → InChIKey pkl
# ─────────────────────────────────────────────

def _inchikey_from_smiles(smiles: str) -> Optional[str]:
    """Compute InChIKey from SMILES using RDKit."""
    try:
        from rdkit import Chem
        from rdkit.Chem.inchi import MolToInchiKey

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        return MolToInchiKey(mol)
    except Exception:
        return None


def molport_smiles_from_tsv_line(text: str) -> Optional[str]:
    """Parse one Molport TSV row and return canonical SMILES column, or None."""
    text = (text or "").strip()
    if not text or "\t" not in text:
        return None
    parts = text.split("\t")
    if len(parts) < 2 or parts[0].upper().startswith("SMILES"):
        return None
    smiles = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    return smiles or None


def download_molport_snapshot(
    *,
    max_attempts: int = MOLPORT_SNAPSHOT_MAX_ATTEMPTS,
) -> Path:
    """Download Molport dataset shards with resume/retry; return local ``data/`` dir."""
    from huggingface_hub import snapshot_download

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            cache_path = snapshot_download(
                repo_id=MOLPORT_DATASET_ID,
                repo_type="dataset",
                allow_patterns=["data/*"],
                max_workers=4,
            )
            root = Path(cache_path)
            data_dir = root / "data"
            if not data_dir.is_dir():
                raise FileNotFoundError(f"Molport data/ missing under {root}")
            txt_files = sorted(data_dir.glob("*.txt"))
            if not txt_files:
                raise FileNotFoundError(f"No Molport .txt shards under {data_dir}")
            logger.info(
                "Molport snapshot ready: %d shard(s) in %s",
                len(txt_files),
                data_dir,
            )
            return data_dir
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            wait_s = 60 * attempt
            logger.warning(
                "Molport snapshot attempt %d/%d failed: %s — retrying in %ds …",
                attempt,
                max_attempts,
                exc,
                wait_s,
            )
            time.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


def _collect_molport_inchikeys_from_shards(
    data_dir: Path,
    *,
    max_entries: int,
) -> tuple[Set[str], int, int, int]:
    """Process local Molport ``*.txt`` shards; return (inchikeys, processed, skipped_mw, skipped_invalid)."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    inchikeys: Set[str] = set()
    processed = 0
    skipped_mw = 0
    skipped_invalid = 0

    for shard in sorted(data_dir.glob("*.txt")):
        logger.info("Processing Molport shard %s …", shard.name)
        with shard.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if max_entries > 0 and processed >= max_entries:
                    return inchikeys, processed, skipped_mw, skipped_invalid

                smiles = molport_smiles_from_tsv_line(line)
                if not smiles:
                    continue

                mol = Chem.MolFromSmiles(smiles)
                if mol is None:
                    skipped_invalid += 1
                    continue

                if Descriptors.MolWt(mol) > 500:
                    skipped_mw += 1
                    continue

                ik = _inchikey_from_smiles(smiles)
                if ik:
                    inchikeys.add(ik)

                processed += 1
                if processed % 100_000 == 0:
                    logger.info(
                        "  … %d processed, %d InChIKeys collected (skipped: %d high-MW, %d invalid)",
                        processed,
                        len(inchikeys),
                        skipped_mw,
                        skipped_invalid,
                    )

    return inchikeys, processed, skipped_mw, skipped_invalid


def build_molport_inchikey_set(max_entries: int = 0) -> int:
    """Download Molport from HuggingFace, compute InChIKeys, save to pkl.

    Uses ``huggingface_hub.snapshot_download`` (resumable file download) instead
    of ``datasets`` streaming, which is prone to CDN 408 timeouts on ranged reads
    during Docker/CI builds.

    Args:
        max_entries: 0 = no limit (process all rows passing MW filter).

    Returns:
        Number of InChIKeys saved.
    """
    try:
        from rdkit import Chem  # noqa: F401
    except ImportError:
        logger.error("rdkit required for Tier 3 — install via conda: conda install -c conda-forge rdkit")
        return 0

    logger.info("Downloading Molport building blocks from HuggingFace (resumable snapshot) …")
    try:
        data_dir = download_molport_snapshot()
    except Exception as exc:
        logger.error("Molport snapshot download failed: %s", exc)
        return 0

    inchikeys, processed, skipped_mw, skipped_invalid = _collect_molport_inchikeys_from_shards(
        data_dir,
        max_entries=max_entries,
    )
    logger.info(
        "Tier 3: processed %d rows (%d high-MW skipped, %d invalid skipped)",
        processed,
        skipped_mw,
        skipped_invalid,
    )

    if not inchikeys:
        logger.warning("Tier 3: no InChIKeys collected — skipping pkl write")
        return 0

    MOLPORT_INCHIKEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MOLPORT_INCHIKEYS_PATH, "wb") as fh:
        pickle.dump(frozenset(inchikeys), fh, protocol=pickle.HIGHEST_PROTOCOL)

    logger.info(
        "Tier 3: saved %d Molport InChIKeys to %s (%.1f MB)",
        len(inchikeys),
        MOLPORT_INCHIKEYS_PATH.relative_to(REPO_ROOT),
        MOLPORT_INCHIKEYS_PATH.stat().st_size / 1024 / 1024,
    )
    return len(inchikeys)


# ─────────────────────────────────────────────
# Tier 4: ZINC bridge verification
# ─────────────────────────────────────────────

def verify_zinc_bridge() -> bool:
    """Verify h5py + pandas are installed and zinc_stock.hdf5 is readable (Tier 4).

    The ZINC stock HDF5 was written by Pandas HDFStore and contains a single
    'table' key with an 'inchi_key' column holding ~17M InChIKey strings.
    """
    try:
        import h5py  # noqa: F401  — just verify importable
        import pandas as pd
    except ImportError as exc:
        logger.error("Install h5py and pandas for Tier 4: %s", exc)
        return False

    if not ZINC_STOCK_PATH.is_file():
        logger.error(
            "ZINC stock not found at %s — run: bash scripts/setup_aizynthfinder.sh",
            ZINC_STOCK_PATH,
        )
        return False

    try:
        import h5py

        # axis1 holds the pandas row index; its length equals the row count
        with h5py.File(str(ZINC_STOCK_PATH), "r") as hf:
            n_keys: int = int(hf["table"]["axis1"].shape[0])
        logger.info(
            "Tier 4 ZINC bridge: OK — %s InChIKeys in zinc_stock.hdf5 (%.0f MB)",
            f"{n_keys:,}",
            ZINC_STOCK_PATH.stat().st_size / 1024 / 1024,
        )
        return True
    except Exception as exc:
        logger.error("Tier 4 ZINC bridge check failed: %s", exc)
        return False


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build precursor databases (all tiers)")
    parser.add_argument(
        "--tiers",
        default="1,2,3,4",
        help=(
            "Comma-separated tier numbers (1=manual, 2=smipoly, 3=molport InChIKeys, "
            "4=zinc bridge verify). Default: 1,2,3,4"
        ),
    )
    parser.add_argument(
        "--max-molport",
        type=int,
        default=0,
        help="Max Molport entries to process (0 = no limit, process all). Default: 0",
    )
    args = parser.parse_args()

    tiers = {int(t.strip()) for t in args.tiers.split(",") if t.strip().isdigit()}
    logger.info("Building precursor databases with tiers: %s", sorted(tiers))

    # ── Tiers 1 + 2: update precursors.json ──────────────────────────────────
    if 1 in tiers or 2 in tiers:
        current = _load_current()
        entries: List[Dict[str, Any]] = []

        if 1 in tiers:
            tier1_manual = [e for e in current.get("entries", []) if e.get("source") == "manual"]
            logger.info("Tier 1 (manual): %d entries", len(tier1_manual))
            entries.extend(tier1_manual)

        if 2 in tiers:
            entries.extend(fetch_smipoly_tier())

        entries = _dedup(entries)
        sources_used = sorted({e.get("source", "unknown") for e in entries})
        output = {
            "version": "1.0",
            "source_info": {
                "built": str(date.today()),
                "sources": sources_used,
                "entry_count": len(entries),
                "notes": (
                    "Tier 1: manual curation (~244 polymer-chemistry essentials). "
                    "Tier 2: SMiPoly polymer monomers (open, from GitHub). "
                    "Tier 3: Molport InChIKeys saved separately to molport_inchikeys.pkl. "
                    "Tier 4: ZINC bridge via zinc_stock.hdf5 + h5py (runtime)."
                ),
            },
            "entries": entries,
        }
        PRECURSORS_PATH.write_text(
            json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            "Tiers 1+2: wrote %d entries to %s",
            len(entries),
            PRECURSORS_PATH.relative_to(REPO_ROOT),
        )

    # ── Tier 3: Molport InChIKey set ─────────────────────────────────────────
    if 3 in tiers:
        n = build_molport_inchikey_set(max_entries=args.max_molport)
        if n == 0:
            logger.error("Tier 3 failed: no Molport InChIKeys saved")
            sys.exit(1)

    # ── Tier 4: ZINC bridge verification ─────────────────────────────────────
    if 4 in tiers:
        ok = verify_zinc_bridge()
        if not ok:
            logger.warning(
                "Tier 4: ZINC bridge not operational — "
                "install h5py and/or run scripts/setup_aizynthfinder.sh"
            )

    logger.info("Done.")


if __name__ == "__main__":
    main()
