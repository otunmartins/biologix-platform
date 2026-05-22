"""Session-aware precursor registry for RetroSynAgent leaf coverage.

Three responsibilities:
1. Load bundled ``data/retrosynthesis/precursors.json`` and expose names/SMILES
   to the bootstrap-patched ``CommonSubstanceDB.get_added_database``.
2. Collect reactant names from agent extractions and seed workspace-level
   purchasable-leaf caches before Tree.construct_tree() is called.
3. Optionally bridge the AiZynthFinder ``zinc_stock.hdf5`` (InChIKey set) for
   massive background coverage when h5py + rdkit are available.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

logger = logging.getLogger(__name__)

# Module-level sets updated by seed_workspace_precursors; read by the bootstrap patch.
_bundled_names: Optional[Set[str]] = None
_workspace_precursors: Set[str] = set()

# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _precursors_json_path() -> Path:
    return _repo_root() / "data" / "retrosynthesis" / "precursors.json"


def _zinc_stock_path() -> Path:
    return _repo_root() / "data" / "aizynthfinder" / "zinc_stock.hdf5"


def _molport_inchikeys_path() -> Path:
    return _repo_root() / "data" / "retrosynthesis" / "molport_inchikeys.pkl"


# ─────────────────────────────────────────────────────────────
# Bundled database loading
# ─────────────────────────────────────────────────────────────

def _load_precursors_json() -> list:
    path = _precursors_json_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read precursors.json at %s", path)
        return []


def get_bundled_precursors() -> Set[str]:
    """Return the full set of lowercase names + SMILES from the bundled database.

    Cached after first call; invalidated by ``reload_bundled_precursors()``.
    """
    global _bundled_names
    if _bundled_names is not None:
        return _bundled_names

    names: Set[str] = set()
    for entry in _load_precursors_json():
        name = (entry.get("name") or "").strip().lower()
        if name:
            names.add(name)
        for alias in entry.get("aliases") or []:
            alias_lower = alias.strip().lower()
            if alias_lower:
                names.add(alias_lower)
        smiles = entry.get("smiles")
        if smiles:
            # Strip whitespace from SMILES before adding
            clean = re.sub(r"\s+", "", smiles)
            if clean:
                names.add(clean.lower())

    _bundled_names = names
    logger.debug("Loaded %d bundled precursor tokens", len(names))
    return names


def reload_bundled_precursors() -> None:
    """Force reload of bundled database (call after updating precursors.json)."""
    global _bundled_names
    _bundled_names = None


# ─────────────────────────────────────────────────────────────
# Workspace precursors (session-scoped)
# ─────────────────────────────────────────────────────────────

def get_workspace_precursors() -> Set[str]:
    """Return names seeded for the current session via seed_workspace_precursors."""
    return _workspace_precursors


def clear_workspace_precursors() -> None:
    """Reset session-scoped precursors (call at the start of each retrosyn run)."""
    _workspace_precursors.clear()


# ─────────────────────────────────────────────────────────────
# Reactant collection from extractions
# ─────────────────────────────────────────────────────────────

_REACTANTS_LINE = re.compile(r"^Reactants\s*:\s*(.+)$", re.IGNORECASE)


def collect_reactants_from_extractions(results_dict: Dict[str, str]) -> Set[str]:
    """Parse all Reactants: lines from a results_dict and return a flat name set.

    Args:
        results_dict: mapping of paper_name -> normalized reaction text
            (same format as the llm_res.json output).

    Returns:
        Set of lowercase reactant name strings, stripped of SMILES annotations.
    """
    reactants: Set[str] = set()
    for text in results_dict.values():
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            m = _REACTANTS_LINE.match(line.strip())
            if not m:
                continue
            raw = m.group(1)
            # Strip PSMILES suffixes: ' [*]CC([*])...'
            raw = re.sub(r"\s+\[\*\]\S*", "", raw)
            # Strip parenthetical SMILES annotations: ' (C=CC(=O)Cl)'
            raw = _strip_smiles_parens_from_csv(raw)
            for tok in raw.split(","):
                tok = tok.strip().lower()
                if tok:
                    reactants.add(tok)
    return reactants


def _strip_smiles_parens_from_csv(val: str) -> str:
    """Remove trailing ` (SMILES)` from each comma-separated token."""
    tokens = val.split(",")
    result = []
    for tok in tokens:
        tok = tok.strip()
        while tok.endswith(")"):
            idx = tok.rfind(" (")
            if idx < 0:
                break
            inner = tok[idx + 2 : -1]
            # Check looks like SMILES: no spaces, has chemistry chars
            if " " not in inner and re.search(r"[CNOcno=\[\]#]", inner):
                tok = tok[:idx].strip()
            else:
                break
        result.append(tok)
    return ", ".join(result)


# ─────────────────────────────────────────────────────────────
# PubChem batch resolution
# ─────────────────────────────────────────────────────────────

def _pubchem_smiles(name: str) -> Optional[str]:
    """Look up SMILES for a compound name via PubChem REST API (single call)."""
    try:
        import urllib.request
        import urllib.parse

        safe = urllib.parse.quote(name)
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{safe}/property/IsomericSMILES/JSON"
        req = urllib.request.Request(url, headers={"User-Agent": "biologix-ai/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        props = body.get("PropertyTable", {}).get("Properties", [])
        if props:
            return props[0].get("IsomericSMILES")
    except Exception:
        pass
    return None


def _pubchem_inchikey(name: str) -> Optional[str]:
    """Look up InChIKey for a compound name via PubChem REST API."""
    try:
        import urllib.request
        import urllib.parse

        safe = urllib.parse.quote(name)
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{safe}/property/InChIKey/JSON"
        req = urllib.request.Request(url, headers={"User-Agent": "biologix-ai/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        props = body.get("PropertyTable", {}).get("Properties", [])
        if props:
            return props[0].get("InChIKey")
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────
# ZINC stock bridge (optional, requires h5py)
# ─────────────────────────────────────────────────────────────

def _load_zinc_inchikeys() -> Optional[Set[str]]:
    """Load InChIKey set from the AiZynthFinder zinc_stock.hdf5 (lazy, cached).

    The file is a Pandas HDFStore (key='table', column='inchi_key') containing
    ~17M InChIKeys.  Both h5py and pandas are required at runtime; both are
    installed by install_submodules.sh.  Memory cost ~1–1.5 GB once loaded.
    """
    path = _zinc_stock_path()
    if not path.is_file():
        logger.debug("zinc_stock.hdf5 not found; ZINC bridge disabled")
        return None
    try:
        import h5py  # noqa: F401  — pandas HDF backend requires h5py
        import pandas as pd

        df = pd.read_hdf(str(path), key="table", columns=["inchi_key"])
        keys: Set[str] = set(df["inchi_key"].dropna())
        if keys:
            logger.info(
                "ZINC bridge: loaded %d InChIKeys from zinc_stock.hdf5 (%.0f MB file)",
                len(keys),
                path.stat().st_size / 1024 / 1024,
            )
            return keys
        logger.warning("ZINC bridge: no valid InChIKeys found in zinc_stock.hdf5")
        return None
    except ImportError as exc:
        logger.debug("h5py or pandas not installed; ZINC bridge disabled: %s", exc)
        return None
    except Exception as exc:
        logger.warning("ZINC bridge load failed: %s", exc)
        return None


_zinc_inchikeys: Optional[Set[str]] = None
_zinc_attempted = False

_molport_inchikeys: Optional[Set[str]] = None
_molport_attempted = False


def _load_molport_inchikeys() -> Optional[Set[str]]:
    """Load Molport InChIKey set from molport_inchikeys.pkl (built by build_precursor_db.py)."""
    import pickle

    path = _molport_inchikeys_path()
    if not path.is_file():
        logger.debug("Molport InChIKey pkl not found; Tier 3 bridge disabled")
        return None
    try:
        with open(path, "rb") as fh:
            keys = pickle.load(fh)
        if isinstance(keys, (set, frozenset)):
            logger.info("Molport bridge: loaded %d InChIKeys from %s", len(keys), path.name)
            return set(keys)
        logger.warning("Molport pkl had unexpected type %s", type(keys))
        return None
    except Exception as exc:
        logger.warning("Molport InChIKey pkl load failed: %s", exc)
        return None


def _is_purchasable_inchikey(inchikey: str) -> bool:
    """Return True if inchikey is in the ZINC stock OR the Molport building-block set."""
    global _zinc_inchikeys, _zinc_attempted, _molport_inchikeys, _molport_attempted

    if not _zinc_attempted:
        _zinc_attempted = True
        _zinc_inchikeys = _load_zinc_inchikeys()
    if not _molport_attempted:
        _molport_attempted = True
        _molport_inchikeys = _load_molport_inchikeys()

    if _zinc_inchikeys is not None and inchikey in _zinc_inchikeys:
        return True
    if _molport_inchikeys is not None and inchikey in _molport_inchikeys:
        return True
    return False


# ─────────────────────────────────────────────────────────────
# Main seeding function
# ─────────────────────────────────────────────────────────────

def seed_workspace_precursors(
    ws: Path,
    reactants: Iterable[str],
    extra: Iterable[str] = (),
) -> Dict[str, str]:
    """Resolve reactant names and seed workspace caches for leaf-check.

    For each name in ``reactants`` + ``extra``:
    1. Check bundled precursors.json (instant).
    2. If not found, query PubChem for SMILES + InChIKey (cached).
    3. If InChIKey found in ZINC stock, mark purchasable.
    4. Write workspace ``smiles_cache.json``, ``substance_query_result.json``,
       and ``precursor_registry.json`` (audit trail).

    Adds resolved names to the module-level ``_workspace_precursors`` set so
    the bootstrap-patched ``get_added_database`` picks them up immediately.

    Returns:
        Dict mapping each input name → resolution source
        ("bundled" | "pubchem" | "zinc" | "unresolved")
    """
    global _workspace_precursors

    all_names = list(reactants) + list(extra)
    bundled = get_bundled_precursors()

    smiles_cache_path = ws / "smiles_cache.json"
    substance_result_path = ws / "substance_query_result.json"
    registry_path = ws / "precursor_registry.json"

    # Load existing caches
    smiles_cache: Dict[str, str] = {}
    substance_result: Dict[str, bool] = {}
    registry: list = []

    for path, obj_ref in (
        (smiles_cache_path, smiles_cache),
        (substance_result_path, substance_result),
    ):
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    obj_ref.update(loaded)  # type: ignore[arg-type]
            except (json.JSONDecodeError, OSError):
                pass
    if registry_path.is_file():
        try:
            loaded_reg = json.loads(registry_path.read_text(encoding="utf-8"))
            if isinstance(loaded_reg, list):
                registry = loaded_reg
        except (json.JSONDecodeError, OSError):
            pass

    resolution_map: Dict[str, str] = {}

    for name in all_names:
        name_lower = name.strip().lower()
        if not name_lower:
            continue
        if name_lower in resolution_map:
            continue

        # 1. Already in bundled DB
        if name_lower in bundled:
            resolution_map[name_lower] = "bundled"
            _workspace_precursors.add(name_lower)
            substance_result[name_lower] = True
            continue

        # 2. Already cached as purchasable
        if substance_result.get(name_lower):
            resolution_map[name_lower] = "cached"
            _workspace_precursors.add(name_lower)
            continue

        # 3. PubChem lookup
        smiles = smiles_cache.get(name_lower) or _pubchem_smiles(name_lower)
        source = "unresolved"

        if smiles:
            smiles_cache[name_lower] = smiles
            # 4. ZINC + Molport bridge (Tiers 4 + 3)
            ik = _pubchem_inchikey(name_lower)
            if ik and _is_purchasable_inchikey(ik):
                substance_result[name_lower] = True
                _workspace_precursors.add(name_lower)
                source = "zinc_or_molport"
            else:
                # PubChem resolved it — treat as known chemical (listed in PubChem = real compound)
                substance_result[name_lower] = True
                _workspace_precursors.add(name_lower)
                source = "pubchem"
        else:
            resolution_map[name_lower] = "unresolved"
            continue

        resolution_map[name_lower] = source
        registry.append(
            {
                "name": name_lower,
                "smiles": smiles,
                "source": source,
            }
        )

    # Persist
    try:
        ws.mkdir(parents=True, exist_ok=True)
        smiles_cache_path.write_text(
            json.dumps(smiles_cache, indent=2), encoding="utf-8"
        )
        substance_result_path.write_text(
            json.dumps(substance_result, indent=2), encoding="utf-8"
        )
        registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not write workspace precursor caches: %s", exc)

    if resolution_map:
        counts: Dict[str, int] = {}
        for src in resolution_map.values():
            counts[src] = counts.get(src, 0) + 1
        logger.info(
            "seed_workspace_precursors: resolved %d names %s",
            len(resolution_map),
            counts,
        )

    return resolution_map


# ─────────────────────────────────────────────────────────────
# Leaf reachability diagnostic
# ─────────────────────────────────────────────────────────────

def diagnose_leaf_reachability(
    reactants: Set[str],
    bundled: Optional[Set[str]] = None,
) -> Dict[str, Dict]:
    """Return per-reactant purchasability status without running the full tree.

    Args:
        reactants: lowercase reactant names to check.
        bundled: override bundled set (default: get_bundled_precursors()).

    Returns:
        Dict mapping name -> {purchasable: bool, resolution_source: str, blocking: bool}
    """
    if bundled is None:
        bundled = get_bundled_precursors()
    ws_extra = get_workspace_precursors()

    result: Dict[str, Dict] = {}
    for name in reactants:
        name_lower = name.strip().lower()
        in_bundled = name_lower in bundled
        in_ws = name_lower in ws_extra
        purchasable = in_bundled or in_ws
        source = "bundled" if in_bundled else ("session" if in_ws else "unresolved")
        result[name_lower] = {
            "purchasable": purchasable,
            "resolution_source": source,
            "blocking": not purchasable,
        }
    return result
