"""Resolve any biologic to a prepared, force-field-checked structure for OpenMM screening.

Accepted inputs (``name_or_pdb``):

* ``PDB`` or ``PDB:chains``, e.g. ``4ZGM:B`` or ``1N8Z:A,B`` (RCSB mmCIF)
* ``uniprot:ACC`` or ``uniprot:ACC:start-end``, or a bare UniProt accession
  (AlphaFold DB model)
* ``sequence:<one-letter>``, a single chain of at most 400 residues (ESMFold API)
* any other text is a name: the curated table first, then RCSB full-text search

Every structure except the bundled insulin file goes through PDBFixer (chain
selection, terminal gaps dropped, internal gaps rebuilt, non-standard residues
substituted, heterogens removed, missing atoms added), and every target is then
built with AMBER14 exactly as the OpenMM matrix run builds it. Each substitution
and removal is recorded so the report can disclose it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

RCSB_CIF_DOWNLOAD = "https://files.rcsb.org/download/{pdb_id}.cif"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
RCSB_ENTITY = "https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{entity_id}"
RCSB_ASSEMBLY = "https://data.rcsb.org/rest/v1/core/assembly/{pdb_id}/1"
ALPHAFOLD_PREDICTION = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"
ESMFOLD_API = "https://api.esmatlas.com/foldSequence/v1/pdb/"

ESMFOLD_MAX_RESIDUES = 400
SEARCH_ROWS = 10
SEARCH_ENTRIES_CHECKED = 5
LARGE_TARGET_ENV = "BIOLOGIX_LARGE_TARGET_ATOMS"
STRUCTURE_CACHE_ENV = "BIOLOGIX_AI_STRUCTURE_CACHE"
TARGET_BASENAME = "biologic_target"
_HTTP_TIMEOUT_S = 60
_HTTP_ATTEMPTS = 3
_HTTP_BACKOFF_S = 1.5
# Rate limiting and server-side faults are worth another try; 404 is an answer.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_FOLD_TIMEOUT_S = 180
_INTERNAL_GAP_WARN = 10

# Chains verified against the RCSB polymer-entity descriptions. Each entry keeps
# one copy of the biologic (a Fab is its heavy and light chain) and leaves out
# antigens, receptors, and nucleic acids that share the crystal.
_CURATED_TARGETS: Dict[str, str] = {
    "insulin": "4F1C:A,B",
    "human insulin": "4F1C:A,B",
    "insulin lispro": "4F1C:A,B",
    "semaglutide": "4ZGM:B",
    "ozempic": "4ZGM:B",
    "wegovy": "4ZGM:B",
    "liraglutide": "4APD:A",
    "adalimumab": "3WD5:H,L",
    "humira": "3WD5:H,L",
    "trastuzumab": "1N8Z:A,B",
    "herceptin": "1N8Z:A,B",
    "bevacizumab": "7V5N:A,B",
    "avastin": "7V5N:A,B",
    "rituximab": "2OSL:L,H",
    "infliximab": "5VH3:H,L",
    "pembrolizumab": "5JXE:C,D",
    "keytruda": "5JXE:C,D",
    "nivolumab": "5WT9:H,L",
    "opdivo": "5WT9:H,L",
    "ustekinumab": "3HMW:H,L",
    "omalizumab": "2XA8:H,L",
    "dupilumab": "6WGB:A,B",
    "erythropoietin": "1BUY:A",
    "epoetin alfa": "1BUY:A",
    "human growth hormone": "1HUW:A",
    "somatropin": "1HUW:A",
}

# The bundled insulin file is simulated as it always has been: chains A+B of
# 4F1C, prepared by openmm_protein rather than PDBFixer.
_BUNDLED_INSULIN_ID = "4F1C"
_BUNDLED_INSULIN_CHAINS: Tuple[str, ...] = ("A", "B")

_PDB_RE = re.compile(r"^([0-9][A-Za-z0-9]{3})(?::([A-Za-z0-9]{1,4}(?:,[A-Za-z0-9]{1,4})*))?$")
_UNIPROT_ACC = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
_UNIPROT_RE = re.compile(rf"^(?:uniprot:)?({_UNIPROT_ACC})(?::(\d+)-(\d+))?$", re.IGNORECASE)
_SEQUENCE_RE = re.compile(r"^sequence:\s*([A-Za-z\s]+)$", re.IGNORECASE)
_AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWY")
_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


class BiologicTarget(BaseModel):
    """Resolved biologic target for session + OpenMM."""

    query: str = Field(description="Original user input")
    canonical_name: str = Field(default="", description="Normalized display name when known")
    pdb_id: str = Field(default="", description="4-character PDB ID when the source is RCSB or bundled")
    pdb_path: str = Field(default="", description="Structure OpenMM simulates (the prepared file)")
    organism: str = Field(default="", description="Hint only; not always populated")
    description: str = Field(default="", description="Short note")
    from_cache: bool = Field(default=False, description="True if the prepared target was reused")
    fetch_ok: bool = Field(default=False, description="True when the target is prepared and passes the force-field check")
    errors: list[str] = Field(default_factory=list)
    source: str = Field(default="", description="bundled, rcsb, alphafold, or esmfold")
    source_id: str = Field(default="", description="PDB ID, UniProt accession, or sequence hash")
    chains: list[str] = Field(default_factory=list, description="Chains kept for simulation")
    resolved_target: str = Field(default="", description="Canonical input to reuse, e.g. 4ZGM:B")
    prepared_pdb_path: str = Field(default="", description="Prepared PDB (same as pdb_path)")
    sequence: str = Field(default="", description="One-letter sequence per chain, joined by '/'")
    modifications: list[dict] = Field(default_factory=list, description="Non-standard residues substituted")
    removed_heterogens: list[dict] = Field(default_factory=list, description="Ligands, glycans, ions, waters removed")
    rebuilt_gaps: list[dict] = Field(default_factory=list, description="Internal missing residues rebuilt")
    n_residues: int = 0
    n_atoms: int = Field(default=0, description="Atoms after hydrogens, as OpenMM builds the protein")
    protonation_ph: float = Field(
        default=7.0,
        description="pH passed to OpenMM addHydrogens. Histidines follow this pH.",
    )
    max_extent_nm: float = 0.0
    large_target: bool = False
    suggested_compute: str = Field(default="", description="gpu when the target is large")
    ff_check: str = Field(default="", description="passed, failed, or skipped")
    search_candidates: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def model_dump_public(self) -> Dict[str, Any]:
        return self.model_dump()


@dataclass
class TargetQuery:
    """Parsed ``name_or_pdb`` input."""

    kind: str  # pdb, uniprot, sequence, name
    value: str
    chains: List[str] = field(default_factory=list)
    residue_range: Optional[Tuple[int, int]] = None


class ResolutionError(RuntimeError):
    """A resolution step failed; the message is shown to the model verbatim."""


# -- input grammar ------------------------------------------------------------


def _normalize_name_key(name: str) -> str:
    return " ".join(name.strip().lower().split())


def parse_target_query(text: str) -> TargetQuery:
    """Parse one ``name_or_pdb`` string into a :class:`TargetQuery`."""
    raw = (text or "").strip()
    seq = _SEQUENCE_RE.match(raw)
    if seq:
        return TargetQuery("sequence", "".join(seq.group(1).split()).upper())
    pdb = _PDB_RE.match(raw)
    if pdb:
        chains = [c for c in (pdb.group(2) or "").split(",") if c]
        return TargetQuery("pdb", pdb.group(1).upper(), chains)
    uni = _UNIPROT_RE.match(raw)
    if uni and (raw.lower().startswith("uniprot:") or len(uni.group(1)) in (6, 10)):
        rng = (int(uni.group(2)), int(uni.group(3))) if uni.group(2) else None
        return TargetQuery("uniprot", uni.group(1).upper(), residue_range=rng)
    return TargetQuery("name", raw)


def lookup_pdb_id(name_or_pdb: str) -> str:
    """Map a curated name to its PDB ID, or pass through a PDB code (``PDB[:chains]``)."""
    raw = (name_or_pdb or "").strip()
    if not raw:
        return ""
    parsed = parse_target_query(raw)
    if parsed.kind == "pdb":
        return parsed.value
    curated = _CURATED_TARGETS.get(_normalize_name_key(raw), "")
    return curated.split(":")[0] if curated else ""


# -- network ------------------------------------------------------------------


def _http(
    method: str,
    url: str,
    *,
    json_body: Optional[dict] = None,
    data: Optional[str] = None,
    expect: str = "json",
    timeout: float = _HTTP_TIMEOUT_S,
) -> Any:
    """Single network entry point (tests monkeypatch this).

    Returns parsed JSON, bytes, or text according to *expect*; ``None`` for an
    empty 204 search result. Raises :class:`ResolutionError` on any failure.

    A dropped connection or a 429/5xx from RCSB, AlphaFold or ESMFold is
    transient: it is retried here with backoff rather than surfaced as a
    resolution failure, because the model cannot fix someone else's outage by
    rewriting its query.
    """
    last = ""
    for attempt in range(_HTTP_ATTEMPTS):
        if attempt:
            time.sleep(_HTTP_BACKOFF_S * (2 ** (attempt - 1)))
        try:
            response = requests.request(method, url, json=json_body, data=data, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{method} {url} failed: {exc}"
            continue
        if response.status_code in _RETRY_STATUS:
            last = f"{method} {url} returned HTTP {response.status_code}"
            continue
        break
    else:
        raise ResolutionError(f"{last} (after {_HTTP_ATTEMPTS} attempts)")
    if response.status_code == 204:
        return None
    if response.status_code >= 400:
        raise ResolutionError(f"{method} {url} returned HTTP {response.status_code}")
    if expect == "bytes":
        return response.content
    if expect == "text":
        return response.text
    try:
        return response.json()
    except ValueError as exc:
        raise ResolutionError(f"{url} did not return JSON") from exc


def _rcsb_search(query: str) -> List[str]:
    body = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": query}},
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": SEARCH_ROWS},
            "results_content_type": ["experimental"],
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }
    result = _http("POST", RCSB_SEARCH, json_body=body)
    if not result:
        return []
    return [str(hit["identifier"]).upper() for hit in result.get("result_set", [])]


def _rcsb_entity_search(query: str) -> List[Tuple[str, str]]:
    """Polymer entities whose ``pdbx_description`` contains *query* as a phrase."""
    body = {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": "rcsb_polymer_entity.pdbx_description",
                "operator": "contains_phrase",
                "value": query,
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "paginate": {"start": 0, "rows": SEARCH_ROWS},
            "results_content_type": ["experimental"],
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }
    result = _http("POST", RCSB_SEARCH, json_body=body)
    if not result:
        return []
    hits = []
    for hit in result.get("result_set", []):
        pdb_id, _, entity_id = str(hit["identifier"]).partition("_")
        if pdb_id and entity_id:
            hits.append((pdb_id.upper(), entity_id))
    return hits


def _rcsb_protein_entities(pdb_id: str) -> List[Dict[str, Any]]:
    """Protein entities of *pdb_id*: description and author chain IDs (first is kept)."""
    entry = _http("GET", RCSB_ENTRY.format(pdb_id=pdb_id)) or {}
    ids = entry.get("rcsb_entry_container_identifiers", {}).get("polymer_entity_ids", [])
    entities: List[Dict[str, Any]] = []
    for entity_id in ids:
        data = _http("GET", RCSB_ENTITY.format(pdb_id=pdb_id, entity_id=entity_id)) or {}
        poly = data.get("entity_poly", {})
        if "polypeptide" not in str(poly.get("type", "")):
            continue
        strands = [s.strip() for s in str(poly.get("pdbx_strand_id", "")).split(",") if s.strip()]
        if not strands:
            continue
        entities.append(
            {
                "entity_id": str(entity_id),
                "description": str(data.get("rcsb_polymer_entity", {}).get("pdbx_description", "")),
                "chains": strands,
                "length": poly.get("rcsb_sample_sequence_length"),
            }
        )
    return entities


# Words that mark a partner of the biologic (its receptor, an antibody against
# it) rather than the biologic itself, unless the query names them too.
_PARTNER_WORDS = ("receptor", "antibody", "fab", "nanobody", "binding", "inhibitor",
                  "kinase", "protease", "domain", "complex")


def _entity_score(query: str, description: str) -> int:
    """0 when *description* does not name *query*; higher is a closer match."""
    q = _normalize_name_key(query)
    d = _normalize_name_key(description)
    if not q or not d:
        return 0
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) > 2]
    if q not in d and not (tokens and all(t in d for t in tokens)):
        return 0
    score = 3 if d == q else 2 if d.startswith(q) else 1
    if any(word in d and word not in q for word in _PARTNER_WORDS):
        score -= 2
    return score


def _entity_matches(query: str, description: str) -> bool:
    return _entity_score(query, description) > 0


def select_entities_by_name(
    query: str,
) -> Tuple[str, List[str], List[str], List[Dict[str, Any]], List[str]]:
    """RCSB full-text search, then the entities whose description names *query*.

    Returns ``(pdb_id, first_chains, all_chains, search_candidates, warnings)``:
    the first author chain of each selected entity, every chain of those
    entities (for assembly selection), the entries inspected, and warnings.
    Descriptions that name a partner (``"glucagon receptor"`` for ``glucagon``)
    rank below descriptions that name the biologic itself.
    """
    candidates: List[Dict[str, Any]] = []
    best_entry: Optional[Tuple[int, str]] = None
    for pdb_id, _entity_id in _rcsb_entity_search(query):
        if best_entry is not None and pdb_id == best_entry[1]:
            continue
        entities = _rcsb_protein_entities(pdb_id)
        top = max((_entity_score(query, e["description"]) for e in entities), default=0)
        candidates.append({"pdb_id": pdb_id, "protein_entities": [e["description"] for e in entities]})
        if top > 0 and (best_entry is None or top > best_entry[0]):
            best_entry = (top, pdb_id)
        if best_entry is not None and best_entry[0] >= 3:
            break
        if len(candidates) >= SEARCH_ENTRIES_CHECKED:
            break
    if best_entry is not None:
        pdb_id = best_entry[1]
        chosen = [e for e in _rcsb_protein_entities(pdb_id) if _entity_score(query, e["description"]) > 0]
        return (
            pdb_id,
            [e["chains"][0] for e in chosen],
            [c for e in chosen for c in e["chains"]],
            candidates,
            [],
        )

    hits = _rcsb_search(query)
    if not hits:
        raise ResolutionError(f"RCSB search found no entity or entry for {query!r}")
    best: Optional[Tuple[int, str, List[Dict[str, Any]]]] = None
    for pdb_id in hits[:SEARCH_ENTRIES_CHECKED]:
        entities = _rcsb_protein_entities(pdb_id)
        scored = [(e, _entity_score(query, e["description"])) for e in entities]
        matched = [e for e, score in scored if score > 0]
        top = max((score for _, score in scored), default=0)
        candidates.append(
            {
                "pdb_id": pdb_id,
                "matched_entities": [e["description"] for e in matched],
                "protein_entities": [e["description"] for e in entities],
            }
        )
        if matched and (best is None or top > best[0]):
            best = (top, pdb_id, [e for e, score in scored if score == top])
        if best is not None and best[0] >= 2:
            break
    if best is not None and best[0] > 0:
        _, pdb_id, chosen = best
        return (
            pdb_id,
            [e["chains"][0] for e in chosen],
            [c for e in chosen for c in e["chains"]],
            candidates,
            [],
        )
    if best is not None:
        _, pdb_id, chosen = best
        warning = (
            f"The closest RCSB entity for {query!r} is {', '.join(e['description'] for e in chosen)} "
            f"in {pdb_id}, which may be a partner of the biologic rather than the biologic. "
            "Check the chains, or pass PDB:chains explicitly."
        )
        return pdb_id, [e["chains"][0] for e in chosen], [c for e in chosen for c in e["chains"]], candidates, [warning]
    first = hits[0]
    entities = _rcsb_protein_entities(first)
    if not entities:
        raise ResolutionError(f"RCSB entry {first} has no protein entity")
    warning = (
        f"No protein entity in the top {SEARCH_ENTRIES_CHECKED} RCSB hits names {query!r}; "
        f"using every protein entity of {first} ({', '.join(e['description'] for e in entities)}). "
        "Check that these chains are the biologic, or pass PDB:chains explicitly."
    )
    return (
        first,
        [e["chains"][0] for e in entities],
        [c for e in entities for c in e["chains"]],
        candidates,
        [warning],
    )


def _assembly_chains(pdb_id: str, label_to_auth: Dict[str, str], allowed: List[str]) -> Tuple[List[str], str]:
    """Author chains of biological assembly 1 that are in *allowed*.

    Returns ``([], reason)`` when assembly 1 needs symmetry operators (its copies
    are not in the deposited coordinates) or cannot be read.
    """
    try:
        assembly = _http("GET", RCSB_ASSEMBLY.format(pdb_id=pdb_id)) or {}
    except ResolutionError as exc:
        return [], f"assembly 1 unavailable ({exc})"
    gens = assembly.get("pdbx_struct_assembly_gen") or []
    if not gens or any(str(g.get("oper_expression", "")).strip() != "1" for g in gens):
        return [], "assembly 1 is generated by crystal symmetry"
    chains: List[str] = []
    for gen in gens:
        for label in gen.get("asym_id_list") or []:
            auth = label_to_auth.get(label, label)
            if auth in allowed and auth not in chains:
                chains.append(auth)
    return chains, "" if chains else "assembly 1 has none of the selected entities"


# -- structure sources ---------------------------------------------------------


def _bundled_structure(repo_root: Path, pdb_id: str) -> Optional[Path]:
    pid = pdb_id.upper()
    for candidate in (
        repo_root / "src" / "python" / "biologix_ai" / "simulation" / "data" / f"{pid}.pdb",
        repo_root / "data" / f"{pid}.pdb",
        repo_root / "data" / "biologics" / f"biologic_{pid}.pdb",
    ):
        if candidate.is_file() and _has_atoms(candidate):
            return candidate.resolve()
    return None


def _has_atoms(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:200000]
    except OSError:
        return False
    return "ATOM" in head or "HETATM" in head


def _download_rcsb(pdb_id: str, dest_dir: Path) -> Path:
    dest = dest_dir / f"{pdb_id.upper()}.cif"
    if not dest.is_file():
        content = _http("GET", RCSB_CIF_DOWNLOAD.format(pdb_id=pdb_id.upper()), expect="bytes")
        dest.write_bytes(content)
    return dest


def _download_alphafold(accession: str, dest_dir: Path) -> Tuple[Path, str]:
    predictions = _http("GET", ALPHAFOLD_PREDICTION.format(accession=accession))
    if not predictions:
        raise ResolutionError(f"AlphaFold DB has no model for UniProt {accession}")
    entry = predictions[0]
    url = entry.get("pdbUrl") or ""
    if not url:
        raise ResolutionError(f"AlphaFold DB entry for {accession} has no pdbUrl")
    dest = dest_dir / f"AF-{accession}.pdb"
    if not dest.is_file():
        dest.write_bytes(_http("GET", url, expect="bytes"))
    return dest, str(entry.get("entryId") or f"AF-{accession}")


def _fold_esmfold(sequence: str, dest_dir: Path) -> Path:
    if len(sequence) > ESMFOLD_MAX_RESIDUES:
        raise ResolutionError(
            f"sequence has {len(sequence)} residues; the ESMFold API accepts at most "
            f"{ESMFOLD_MAX_RESIDUES}. Pass a PDB ID with chains or a UniProt accession instead."
        )
    bad = sorted(set(sequence) - _AMINO_ACIDS)
    if bad:
        raise ResolutionError(f"sequence contains non-amino-acid letters: {''.join(bad)}")
    dest = dest_dir / f"esmfold_{_sequence_hash(sequence)}.pdb"
    if not dest.is_file():
        text = _http("POST", ESMFOLD_API, data=sequence, expect="text", timeout=_FOLD_TIMEOUT_S)
        if "ATOM" not in str(text):
            raise ResolutionError("ESMFold returned no atoms")
        dest.write_text(str(text), encoding="utf-8")
    return dest


def _sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()[:12]


def _trim_residue_range(pdb_path: Path, residue_range: Tuple[int, int], dest: Path) -> Path:
    """Keep ATOM records whose residue number lies in *residue_range* (inclusive)."""
    start, end = residue_range
    lines = []
    for line in pdb_path.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith(("ATOM", "HETATM")):
            try:
                resseq = int(line[22:26])
            except ValueError:
                continue
            if start <= resseq <= end:
                lines.append(line)
        elif line.startswith(("TER", "END")):
            continue
    if not lines:
        raise ResolutionError(f"no residues in range {start}-{end}")
    dest.write_text("".join(lines) + "END\n", encoding="utf-8")
    return dest


def _mean_plddt(pdb_path: Path) -> Optional[float]:
    values = []
    for line in pdb_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("ATOM") and line[12:16].strip() == "CA":
            try:
                values.append(float(line[60:66]))
            except ValueError:
                continue
    return sum(values) / len(values) if values else None


# -- preparation ----------------------------------------------------------------


@dataclass
class PreparedStructure:
    path: Path
    chains: List[str]
    sequences: Dict[str, str]
    modifications: List[dict]
    removed_heterogens: List[dict]
    rebuilt_gaps: List[dict]
    n_residues: int
    max_extent_nm: float
    warnings: List[str]


def prepare_structure(source: Path, keep_chains: List[str], dest: Path) -> PreparedStructure:
    """PDBFixer preparation of *source* keeping *keep_chains* (all chains when empty)."""
    try:
        from openmm.app import PDBFile
        from pdbfixer import PDBFixer
    except ImportError as exc:
        raise ResolutionError(f"pdbfixer/openmm not importable: {exc}") from exc

    fixer = PDBFixer(filename=str(source))
    # OpenMM reads mmCIF chains by label_asym_id when that separates ligands and
    # waters into their own chains; users and RCSB pages name author chains.
    label_to_auth = _mmcif_label_to_auth(source) if source.suffix.lower() in (".cif", ".mmcif") else {}

    def author(chain_id: str) -> str:
        return label_to_auth.get(chain_id, chain_id)

    present = [chain.id for chain in fixer.topology.chains()]
    warnings: List[str] = []
    if keep_chains:
        available = set(present) | {author(c) for c in present}
        missing = [c for c in keep_chains if c not in available]
        if missing:
            raise ResolutionError(
                f"chains {', '.join(missing)} are not in {source.name} (chains present: "
                f"{', '.join(dict.fromkeys(author(c) for c in present))})"
            )
        drop = [c for c in dict.fromkeys(present) if c not in keep_chains and author(c) not in keep_chains]
        if drop:
            fixer.removeChains(chainIds=drop)

    fixer.findMissingResidues()
    chains = list(fixer.topology.chains())
    rebuilt: List[dict] = []
    for key in list(fixer.missingResidues):
        chain = chains[key[0]]
        n_res = len(list(chain.residues()))
        if key[1] == 0 or key[1] == n_res:
            names = list(fixer.missingResidues[key])
            end = "N" if key[1] == 0 else "C"
            # Report the deposited residue names (e.g. AIB), not PDBFixer's substitutes.
            deposited = next((q.residues for q in fixer.sequences if q.chainId == chain.id), None)
            if deposited and len(deposited) >= len(names):
                names = list(deposited[: len(names)] if end == "N" else deposited[-len(names):])
            warnings.append(
                f"chain {author(chain.id)}: {len(names)} {end}-terminal residue(s) are not observed "
                f"in the structure and are not modelled ({'-'.join(names)})"
            )
            del fixer.missingResidues[key]
            continue
        names = fixer.missingResidues[key]
        rebuilt.append({"chain": author(chain.id), "after_residue_index": key[1], "n_residues": len(names)})
        if len(names) > _INTERNAL_GAP_WARN:
            warnings.append(
                f"chain {author(chain.id)}: rebuilt an internal gap of {len(names)} missing residues; "
                "its geometry is modelled, not observed"
            )

    fixer.findNonstandardResidues()
    modifications = [
        {"chain": author(residue.chain.id), "residue": residue.name, "residue_id": residue.id,
         "replaced_by": replacement}
        for residue, replacement in fixer.nonstandardResidues
    ]
    fixer.replaceNonstandardResidues()

    standard = set(PDBFile._standardResidues) - {"HOH"}
    removed: Dict[Tuple[str, str], int] = {}
    in_chain: List[dict] = []
    for chain in fixer.topology.chains():
        residues = list(chain.residues())
        protein_idx = [i for i, r in enumerate(residues) if r.name in standard]
        for i, residue in enumerate(residues):
            if residue.name in standard:
                continue
            key = (author(chain.id), residue.name)
            removed[key] = removed.get(key, 0) + 1
            if protein_idx and protein_idx[0] < i < protein_idx[-1]:
                in_chain.append({"chain": author(chain.id), "residue": residue.name, "residue_id": residue.id})
    fixer.removeHeterogens(keepWater=False)
    removed_list = [
        {"chain": chain, "residue": name, "count": count} for (chain, name), count in removed.items()
    ]
    for item in in_chain:
        warnings.append(
            f"chain {item['chain']}: non-standard residue {item['residue']} {item['residue_id']} "
            "has no standard substitute and was removed, leaving a gap in the chain"
        )

    fixer.findMissingAtoms()
    fixer.addMissingAtoms()

    kept = [chain for chain in fixer.topology.chains() if len(list(chain.residues())) > 0]
    if not kept:
        raise ResolutionError("no protein residues remain after preparation")
    if len({author(chain.id) for chain in kept}) == len(kept):
        for chain in kept:
            chain.id = author(chain.id)
    empty = [chain for chain in fixer.topology.chains() if len(list(chain.residues())) == 0]
    if empty:
        from openmm.app import Modeller  # noqa: PLC0415

        modeller = Modeller(fixer.topology, fixer.positions)
        modeller.delete(empty)
        fixer.topology, fixer.positions = modeller.topology, modeller.positions
    used = {chain.id for chain in fixer.topology.chains() if len(chain.id) == 1}
    spare = iter(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" if c not in used)
    sequences: Dict[str, str] = {}
    for chain in fixer.topology.chains():
        if len(chain.id) != 1:
            original = chain.id
            chain.id = next(spare)
            warnings.append(f"chain {original} renamed {chain.id} (PDB format allows one character)")
        for index, residue in enumerate(chain.residues(), start=1):
            residue.id = str(index)
            residue.insertionCode = ""
        sequences[chain.id] = "".join(_THREE_TO_ONE.get(r.name, "X") for r in chain.residues())

    ssbond_lines = []
    for bond in fixer.topology.bonds():
        a, b = bond.atom1, bond.atom2
        if a.name == "SG" and b.name == "SG" and a.residue is not b.residue:
            ssbond_lines.append((a.residue.chain.id, int(a.residue.id), b.residue.chain.id, int(b.residue.id)))

    import numpy as np  # noqa: PLC0415
    from openmm import unit  # noqa: PLC0415

    coords = np.array(fixer.positions.value_in_unit(unit.nanometer))
    extent = float((coords.max(axis=0) - coords.min(axis=0)).max()) if len(coords) else 0.0

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        for i, (c1, r1, c2, r2) in enumerate(ssbond_lines, 1):
            handle.write(f"SSBOND {i:3d} CYS {c1} {r1:4d}    CYS {c2} {r2:4d}\n")
        PDBFile.writeFile(fixer.topology, fixer.positions, handle, keepIds=True)

    return PreparedStructure(
        path=dest,
        chains=[chain.id for chain in fixer.topology.chains()],
        sequences=sequences,
        modifications=modifications,
        removed_heterogens=removed_list,
        rebuilt_gaps=rebuilt,
        n_residues=sum(len(s) for s in sequences.values()),
        max_extent_nm=round(extent, 3),
        warnings=warnings,
    )


def _mmcif_label_to_auth(path: Path) -> Dict[str, str]:
    """``label_asym_id -> auth_asym_id`` from the mmCIF ``atom_site`` table."""
    try:
        from openmm.app.internal.pdbx.reader.PdbxReader import PdbxReader  # noqa: PLC0415
    except ImportError:
        return {}
    with open(path, encoding="utf-8") as handle:
        data: List[Any] = []
        PdbxReader(handle).read(data)
    if not data:
        return {}
    atoms = data[0].getObj("atom_site")
    if atoms is None:
        return {}
    label_col = atoms.getAttributeIndex("label_asym_id")
    auth_col = atoms.getAttributeIndex("auth_asym_id")
    if -1 in (label_col, auth_col):
        return {}
    mapping: Dict[str, str] = {}
    for row in atoms.getRowList():
        mapping.setdefault(row[label_col], row[auth_col])
    return mapping


def force_field_check(pdb_path: Path) -> Tuple[int, str]:
    """Build the protein exactly as the OpenMM matrix run does and create an AMBER14 system.

    Returns ``(n_atoms, "")`` on success or ``(0, error)`` with the unmatched residue.
    Raises :class:`ImportError` when OpenMM is not installed.
    """
    import openmm.app as app  # noqa: PLC0415

    from biologix_ai.simulation.openmm_complex import (  # noqa: PLC0415
        PROTONATION_PH,
        target_protein_chains,
    )
    from biologix_ai.simulation.openmm_protein import (  # noqa: PLC0415
        load_protein_modeller,
        prepare_protein_pdb,
    )

    with tempfile.TemporaryDirectory(prefix="biologix_ffcheck_") as work:
        work_pdb = Path(work) / "target.pdb"
        prepare_protein_pdb(str(pdb_path), str(work_pdb), chains=target_protein_chains(str(pdb_path)))
        try:
            modeller = load_protein_modeller(str(work_pdb), add_ssbond=True)
            forcefield = app.ForceField("amber14-all.xml")
            modeller.addHydrogens(forcefield, pH=PROTONATION_PH)
            forcefield.createSystem(modeller.topology, nonbondedMethod=app.NoCutoff)
        except Exception as exc:  # ValueError "No template found for residue ..."
            return 0, f"AMBER14 cannot parameterize the target: {exc}"
        return modeller.topology.getNumAtoms(), ""


def _large_target_threshold() -> int:
    try:
        return int(os.environ.get(LARGE_TARGET_ENV, "15000"))
    except ValueError:
        return 15000


# -- cache ---------------------------------------------------------------------


def _cache_root(repo_root: Path, cache_dir: Optional[Path]) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    env = os.environ.get(STRUCTURE_CACHE_ENV, "").strip()
    if env:
        return Path(env)
    return Path(repo_root) / "runs" / ".structures"


def _cache_key(resolved_target: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved_target)
    if len(safe) > 60:
        safe = safe[:40] + "_" + hashlib.sha256(resolved_target.encode()).hexdigest()[:12]
    return safe


def _load_cached(cache: Path) -> Optional[BiologicTarget]:
    meta = cache / f"{TARGET_BASENAME}.json"
    pdb = cache / f"{TARGET_BASENAME}.pdb"
    if not (meta.is_file() and pdb.is_file()):
        return None
    try:
        target = BiologicTarget(**json.loads(meta.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    if not target.fetch_ok:
        return None
    target.pdb_path = target.prepared_pdb_path = str(pdb.resolve())
    target.from_cache = True
    return target


def _write_target(directory: Path, target: BiologicTarget, pdb_source: Path) -> BiologicTarget:
    """Copy the prepared PDB and metadata into *directory*; return the target pointing there."""
    directory.mkdir(parents=True, exist_ok=True)
    copy = target.model_copy(deep=True)
    if target.resolved_target != f"{_BUNDLED_INSULIN_ID}:{','.join(_BUNDLED_INSULIN_CHAINS)}":
        # The bundled insulin stays at its packaged path: OpenMM recognises that
        # file and keeps chains A+B, exactly as before this resolver existed.
        pdb = directory / f"{TARGET_BASENAME}.pdb"
        if pdb_source.resolve() != pdb.resolve():
            shutil.copyfile(pdb_source, pdb)
        copy.pdb_path = copy.prepared_pdb_path = str(pdb.resolve())
    (directory / f"{TARGET_BASENAME}.json").write_text(
        json.dumps(copy.model_dump(), indent=2, default=str), encoding="utf-8"
    )
    return copy


# -- entry point -----------------------------------------------------------------


def resolve_biologic_target(
    name_or_pdb: str,
    repo_root: Path,
    session_dir: Optional[Path] = None,
    fetch_pdb: bool = True,
    cache_dir: Optional[Path] = None,
) -> BiologicTarget:
    """Resolve, prepare, and force-field-check a biologic target.

    Network access is used only when ``fetch_pdb`` is true. Results are cached
    under ``BIOLOGIX_AI_STRUCTURE_CACHE`` (default ``<repo>/runs/.structures``) or
    *cache_dir*, and copied to ``<session>/structures/biologic_target.{pdb,json}``.
    """
    raw = (name_or_pdb or "").strip()
    if not raw:
        return BiologicTarget(query=raw, errors=["empty name_or_pdb"])
    repo_root = Path(repo_root)
    parsed = parse_target_query(raw)
    canonical = raw
    if parsed.kind == "name":
        curated = _CURATED_TARGETS.get(_normalize_name_key(raw))
        if curated:
            canonical = raw.strip().title() if raw.islower() else raw.strip()
            parsed = parse_target_query(curated)
    base = BiologicTarget(query=raw, canonical_name=canonical)
    try:
        target = _resolve(parsed, base, repo_root, fetch_pdb, cache_dir)
    except ResolutionError as exc:
        base.errors.append(str(exc))
        base.errors.append(_retry_hint(parsed))
        return base
    if target.fetch_ok and session_dir is not None:
        target = _write_target(Path(session_dir) / "structures", target, Path(target.pdb_path))
    return target


def _retry_hint(parsed: TargetQuery) -> str:
    return (
        "Retry resolve_biologic_target with a more specific form found in the literature: "
        "PDB:chains (e.g. 4ZGM:B), uniprot:ACCESSION[:start-end], or sequence:ONE_LETTER "
        f"(at most {ESMFOLD_MAX_RESIDUES} residues). Do not ask the user."
        if parsed.kind in ("name", "pdb", "uniprot", "sequence")
        else ""
    )


def _resolve(
    parsed: TargetQuery,
    base: BiologicTarget,
    repo_root: Path,
    fetch_pdb: bool,
    cache_dir: Optional[Path],
) -> BiologicTarget:
    target = base.model_copy(deep=True)
    work_root = _cache_root(repo_root, cache_dir)

    if parsed.kind == "pdb":
        target.pdb_id = parsed.value
        target.source_id = parsed.value
        bundled_insulin = (
            parsed.value == _BUNDLED_INSULIN_ID
            and (not parsed.chains or tuple(parsed.chains) == _BUNDLED_INSULIN_CHAINS)
        )
        if bundled_insulin:
            bundled = _bundled_structure(repo_root, parsed.value)
            if bundled is not None:
                return _bundled_insulin_target(target, bundled)

    name_query = parsed.value if parsed.kind == "name" else ""
    if parsed.kind == "name":
        if not fetch_pdb:
            raise ResolutionError(
                f"unknown biologic name: {parsed.value!r} is not in the curated table and "
                "fetch_pdb=False, so RCSB search was not attempted"
            )
        pdb_id, first_chains, all_chains, candidates, warnings = select_entities_by_name(parsed.value)
        target.search_candidates = candidates
        target.warnings.extend(warnings)
        parsed = TargetQuery("pdb", pdb_id, [])
        entity_chains = (first_chains, all_chains)
        target.pdb_id = target.source_id = pdb_id
    else:
        entity_chains = None

    resolved = _canonical_target(parsed)
    target.resolved_target = resolved
    # A searched name selects entities by description, so it is cached apart
    # from the bare PDB ID of the same entry.
    cache = work_root / _cache_key(f"name_{_normalize_name_key(name_query)}" if name_query else resolved)
    cached = _load_cached(cache)
    if cached is not None:
        cached.query = target.query
        cached.canonical_name = target.canonical_name
        cached.search_candidates = target.search_candidates or cached.search_candidates
        return cached

    staging = cache / "source"
    staging.mkdir(parents=True, exist_ok=True)
    if parsed.kind == "pdb":
        target.source = "rcsb"
        local = _bundled_structure(repo_root, parsed.value)
        if local is not None:
            source_path = local
            target.source = "bundled"
        elif not fetch_pdb:
            raise ResolutionError(
                f"fetch_pdb=False and no local structure for {parsed.value} in {work_root}"
            )
        else:
            source_path = _download_rcsb(parsed.value, staging)
        keep = list(parsed.chains)
        if not keep:
            keep = _default_chains(parsed.value, source_path, entity_chains, fetch_pdb, target)
            target.resolved_target = f"{parsed.value}:{','.join(keep)}" if keep else parsed.value
        target.description = f"{target.source} {parsed.value}"
    elif parsed.kind == "uniprot":
        if not fetch_pdb:
            raise ResolutionError("fetch_pdb=False; AlphaFold DB was not queried")
        source_path, entry_id = _download_alphafold(parsed.value, staging)
        if parsed.residue_range:
            start, end = parsed.residue_range
            source_path = _trim_residue_range(source_path, parsed.residue_range, staging / f"AF-{parsed.value}_{start}-{end}.pdb")
        target.source = "alphafold"
        target.source_id = entry_id
        target.description = f"AlphaFold DB model {entry_id}"
        keep = []
        plddt = _mean_plddt(source_path)
        if plddt is not None and plddt < 70:
            target.warnings.append(
                f"AlphaFold mean pLDDT is {plddt:.1f} (<70): low-confidence regions are "
                "simulated as modelled; consider a residue range or an experimental PDB entry"
            )
    elif parsed.kind == "sequence":
        if not fetch_pdb:
            raise ResolutionError("fetch_pdb=False; ESMFold was not called")
        source_path = _fold_esmfold(parsed.value, staging)
        target.source = "esmfold"
        target.source_id = f"sha256:{_sequence_hash(parsed.value)}"
        target.description = "ESMFold prediction (api.esmatlas.com)"
        keep = []
    else:
        raise ResolutionError(f"unsupported target form: {parsed.kind}")

    prepared = prepare_structure(source_path, keep, staging / "prepared.pdb")
    target.chains = prepared.chains
    target.sequence = "/".join(prepared.sequences[c] for c in prepared.chains if c in prepared.sequences)
    target.modifications = prepared.modifications
    target.removed_heterogens = prepared.removed_heterogens
    target.rebuilt_gaps = prepared.rebuilt_gaps
    target.n_residues = prepared.n_residues
    target.max_extent_nm = prepared.max_extent_nm
    target.warnings.extend(prepared.warnings)
    target.pdb_path = target.prepared_pdb_path = str(prepared.path)
    _finish(target)
    if target.fetch_ok:
        return _write_target(cache, target, prepared.path)
    return target


def _default_chains(
    pdb_id: str,
    source_path: Path,
    entity_chains: Optional[Tuple[List[str], List[str]]],
    fetch_pdb: bool,
    target: BiologicTarget,
) -> List[str]:
    """Chains for a PDB entry given without chains.

    A searched name keeps the first chain of each matching entity. A bare PDB ID
    keeps biological assembly 1's protein chains, so a full IgG keeps all four,
    or one chain per entity when assembly 1 needs symmetry operators. Offline,
    every chain in the file is kept.
    """
    if not fetch_pdb:
        target.warnings.append(f"fetch_pdb=False: keeping every chain of {pdb_id}")
        return []
    if entity_chains is not None:
        # A searched name keeps one copy of each matching entity.
        return list(entity_chains[0])
    entities = _rcsb_protein_entities(pdb_id)
    first = [e["chains"][0] for e in entities]
    allowed = [c for e in entities for c in e["chains"]]
    label_to_auth = _mmcif_label_to_auth(source_path) if source_path.suffix.lower() == ".cif" else {}
    if label_to_auth:
        chains, reason = _assembly_chains(pdb_id, label_to_auth, allowed)
        if chains:
            return chains
        target.warnings.append(f"{pdb_id}: {reason}; keeping one chain per entity ({', '.join(first)})")
    return first


def _canonical_target(parsed: TargetQuery) -> str:
    if parsed.kind == "pdb":
        return parsed.value + (":" + ",".join(parsed.chains) if parsed.chains else "")
    if parsed.kind == "uniprot":
        rng = f":{parsed.residue_range[0]}-{parsed.residue_range[1]}" if parsed.residue_range else ""
        return f"uniprot:{parsed.value}{rng}"
    if parsed.kind == "sequence":
        return f"sequence:{parsed.value}"
    return parsed.value


def _bundled_insulin_target(target: BiologicTarget, bundled: Path) -> BiologicTarget:
    """The bundled 4F1C insulin keeps its original, un-PDBFixed simulation path."""
    target.source = "bundled"
    target.chains = list(_BUNDLED_INSULIN_CHAINS)
    target.resolved_target = f"{_BUNDLED_INSULIN_ID}:{','.join(_BUNDLED_INSULIN_CHAINS)}"
    target.pdb_path = target.prepared_pdb_path = str(bundled)
    target.description = "bundled package data (insulin chains A+B)"
    target.from_cache = True
    target.max_extent_nm = _extent_nm(bundled, set(_BUNDLED_INSULIN_CHAINS))
    _finish(target)
    return target


def _extent_nm(pdb_path: Path, chains: set) -> float:
    xs, ys, zs = [], [], []
    for line in pdb_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("ATOM") and line[21:22] in chains:
            try:
                xs.append(float(line[30:38]))
                ys.append(float(line[38:46]))
                zs.append(float(line[46:54]))
            except ValueError:
                continue
    if not xs:
        return 0.0
    return round(max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 10.0, 3)


def _finish(target: BiologicTarget) -> None:
    """Run the AMBER14 check and set ``fetch_ok``, size, and compute advice."""
    try:
        n_atoms, error = force_field_check(Path(target.pdb_path))
    except ImportError as exc:
        target.ff_check = "skipped"
        target.warnings.append(f"force-field check skipped: OpenMM not importable ({exc})")
        target.fetch_ok = Path(target.pdb_path).is_file()
        return
    if error:
        target.ff_check = "failed"
        target.fetch_ok = False
        target.errors.append(error)
        return
    target.ff_check = "passed"
    target.n_atoms = n_atoms
    target.fetch_ok = True
    if n_atoms > _large_target_threshold():
        target.large_target = True
        target.suggested_compute = "gpu"
        target.warnings.append(
            f"target has {n_atoms} atoms with hydrogens (>{_large_target_threshold()}); run OpenMM "
            "with compute=\"gpu\". For a full antibody, a Fab (PDB:H,L) or one domain is "
            "usually enough for excipient screening."
        )


def target_metadata_path(session_dir: Path) -> Path:
    """``<session>/structures/biologic_target.json`` written by :func:`resolve_biologic_target`."""
    return Path(session_dir) / "structures" / f"{TARGET_BASENAME}.json"


def load_session_target(session_dir: Path) -> Optional[BiologicTarget]:
    """The target resolved into *session_dir*, if any."""
    meta = target_metadata_path(session_dir)
    if not meta.is_file():
        return None
    try:
        return BiologicTarget(**json.loads(meta.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
