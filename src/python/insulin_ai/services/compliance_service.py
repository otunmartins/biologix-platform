"""Regulatory excipient compliance service.

Checks a polymer PSMILES against:
- EMA / FDA Inactive Ingredient / GRAS approved-excipient lookup tables
- Immunogenicity structural alerts (anti-PEG antibody motifs, aggregation inducers)
- Optional jurisdiction filtering
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approved excipient lookup (PSMILES → names; subset of EMA/FDA IIG/GRAS)
# Keyed by canonical PSMILES repeat unit.
# ---------------------------------------------------------------------------
_APPROVED_EXCIPIENTS: Dict[str, Dict[str, Any]] = {
    "[*]OCC[*]": {
        "name": "Polyethylene glycol (PEG)",
        "gras": True,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 95,
        "notes": "FDA IIG listed. EMA excipient monograph. Widely used for protein PEGylation.",
    },
    "[*]OC(C)C[*]": {
        "name": "Polypropylene glycol (PPG)",
        "gras": True,
        "jurisdictions": ["FDA"],
        "precedent_count": 12,
        "notes": "FDA IIG listed for oral and parenteral use.",
    },
    "[*]OC(=O)C(C)OC(=O)C[*]": {
        "name": "PLGA (poly(lactic-co-glycolic acid))",
        "gras": False,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 40,
        "notes": "Approved in multiple parenteral formulations. Biodegradable.",
    },
    "[*]OC(=O)CC[*]": {
        "name": "Poly(glycolic acid) / PGA",
        "gras": False,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 8,
        "notes": "Approved as biodegradable matrix. ICH S10 safety reference.",
    },
    "[*]OC(=O)C(C)[*]": {
        "name": "Polylactic acid (PLA)",
        "gras": False,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 18,
        "notes": "Approved as biodegradable controlled-release matrix.",
    },
    "[*]N1CCOCC1[*]": {
        "name": "Polyvinylpyrrolidone / PVP",
        "gras": True,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 55,
        "notes": "FDA IIG listed. Widely used binder and stabiliser.",
    },
    "[*]CC(O)[*]": {
        "name": "Polyvinyl alcohol (PVA)",
        "gras": False,
        "jurisdictions": ["FDA", "EMA"],
        "precedent_count": 20,
        "notes": "EMA excipient monograph. Ophthalmic and parenteral use.",
    },
    "[*]OC(=O)CCCCC(=O)O[*]": {
        "name": "Polycaprolactone (PCL)",
        "gras": False,
        "jurisdictions": ["FDA"],
        "precedent_count": 6,
        "notes": "FDA approved in biodegradable implants.",
    },
}

# Name-based lookup (lower-case fragment match)
_NAME_FRAGMENT_MAP: Dict[str, str] = {
    "peg": "Polyethylene glycol (PEG)",
    "polyethylene glycol": "Polyethylene glycol (PEG)",
    "pvp": "Polyvinylpyrrolidone / PVP",
    "polyvinylpyrrolidone": "Polyvinylpyrrolidone / PVP",
    "plga": "PLGA (poly(lactic-co-glycolic acid))",
    "pla": "Polylactic acid (PLA)",
    "pva": "Polyvinyl alcohol (PVA)",
    "polyvinyl alcohol": "Polyvinyl alcohol (PVA)",
    "pcl": "Polycaprolactone (PCL)",
    "polycaprolactone": "Polycaprolactone (PCL)",
}

# ---------------------------------------------------------------------------
# Immunogenicity SMARTS (RDKit, optional)
# ---------------------------------------------------------------------------
_IMMUNOGENICITY_SMARTS: List[Dict[str, str]] = [
    {
        "name": "anti_PEG_ether_repeat",
        "smarts": "[OX2;H0][CH2][CH2]",
        "severity": "warning",
        "note": "PEG-like ether repeat; anti-PEG antibodies documented in patients with prior PEG exposure.",
    },
    {
        "name": "polysorbate_ester_linkage",
        "smarts": "[OX2][C](=[O])[CX4]",
        "severity": "info",
        "note": "Ester linkage common in polysorbates; hypersensitivity reactions reported.",
    },
    {
        "name": "quaternary_ammonium",
        "smarts": "[N+;!H0]",
        "severity": "warning",
        "note": "Quaternary ammonium motif; documented adjuvant/immune stimulation activity.",
    },
    {
        "name": "carrageenan_sulfonate",
        "smarts": "[S](=O)(=O)[OH]",
        "severity": "warning",
        "note": "Sulfonate group; polysaccharide sulfates can activate complement.",
    },
]

# ---------------------------------------------------------------------------
# Aggregation-induction alerts (structural features that destabilise proteins)
# ---------------------------------------------------------------------------
_AGGREGATION_SMARTS: List[Dict[str, str]] = [
    {
        "name": "hydrophobic_aromatic_rich",
        "smarts": "c1ccccc1",
        "severity": "warning",
        "note": "Aromatic ring: high aromatic density in repeat unit can hydrophobically coat protein surface and induce aggregation.",
    },
    {
        "name": "charged_amine_density",
        "smarts": "[NX3;H2,H1;!$([NX3][CX3](=[OX1]))]",
        "severity": "info",
        "note": "Primary amine density: electrostatic interaction may disrupt native charge patterns.",
    },
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class ComplianceResult:
    psmiles: str
    approved_match: Optional[str] = None
    approved_name: Optional[str] = None
    gras: Optional[bool] = None
    jurisdictions_matched: List[str] = field(default_factory=list)
    precedent_count: int = 0
    immunogenicity_flags: List[Dict[str, str]] = field(default_factory=list)
    aggregation_flags: List[Dict[str, str]] = field(default_factory=list)
    jurisdiction_clear: bool = True
    overall_status: str = "unknown"   # "approved", "no_match", "flagged"
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "psmiles": self.psmiles,
            "approved_match": self.approved_match,
            "approved_name": self.approved_name,
            "gras": self.gras,
            "jurisdictions_matched": self.jurisdictions_matched,
            "precedent_count": self.precedent_count,
            "immunogenicity_flags": self.immunogenicity_flags,
            "aggregation_flags": self.aggregation_flags,
            "jurisdiction_clear": self.jurisdiction_clear,
            "overall_status": self.overall_status,
            "notes": self.notes,
            "errors": self.errors,
        }


def _run_smarts(smiles: str, smarts_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Run a list of SMARTS checks against a SMILES string; return matches."""
    hits: List[Dict[str, str]] = []
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return hits
        for entry in smarts_list:
            pat = Chem.MolFromSmarts(entry["smarts"])
            if pat and mol.HasSubstructMatch(pat):
                hits.append({k: v for k, v in entry.items() if k != "smarts"})
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("SMARTS check error: %s", exc)
    return hits


def _psmiles_to_smiles(psmiles: str) -> str:
    """Cap polymer star atoms with carbon to produce a valid SMILES fragment for RDKit."""
    return psmiles.replace("[*]", "C").strip()


def check_excipient_compliance(
    psmiles: str,
    jurisdiction: str = "FDA,EMA",
    check_gras: bool = True,
    check_immunogenicity: bool = True,
    check_aggregation: bool = True,
) -> ComplianceResult:
    """
    Check a PSMILES repeat unit for regulatory excipient compliance.

    Parameters
    ----------
    psmiles:
        Polymer SMILES (repeat unit, with or without [*] connection points).
    jurisdiction:
        Comma-separated list of jurisdictions to check (FDA, EMA).
    check_gras:
        Whether to check the FDA GRAS status of approved matches.
    check_immunogenicity:
        Whether to run immunogenicity SMARTS alerts.
    check_aggregation:
        Whether to run aggregation-induction SMARTS alerts.
    """
    jurisdictions = [j.strip().upper() for j in jurisdiction.split(",") if j.strip()]
    result = ComplianceResult(psmiles=psmiles)

    # 1. Direct PSMILES lookup
    canonical = psmiles.strip()
    match_entry = _APPROVED_EXCIPIENTS.get(canonical)

    # 2. Fragment SMILES match (strip stars)
    if match_entry is None:
        frag = _psmiles_to_smiles(canonical)
        for key, entry in _APPROVED_EXCIPIENTS.items():
            if _psmiles_to_smiles(key) == frag:
                match_entry = entry
                break

    if match_entry:
        matched_jurisdictions = [j for j in match_entry.get("jurisdictions", []) if j in jurisdictions]
        result.approved_match = canonical
        result.approved_name = match_entry.get("name", "")
        result.gras = match_entry.get("gras") if check_gras else None
        result.jurisdictions_matched = matched_jurisdictions
        result.precedent_count = match_entry.get("precedent_count", 0)
        result.jurisdiction_clear = bool(matched_jurisdictions)
        result.notes.append(match_entry.get("notes", ""))
        result.overall_status = "approved" if matched_jurisdictions else "no_match"
    else:
        result.jurisdiction_clear = False
        result.overall_status = "no_match"
        result.notes.append(
            f"No direct match in approved excipient database for jurisdictions: {jurisdictions}. "
            "Novel excipient; full regulatory package required."
        )

    # 3. Immunogenicity SMARTS
    if check_immunogenicity:
        smiles_frag = _psmiles_to_smiles(canonical)
        imm_hits = _run_smarts(smiles_frag, _IMMUNOGENICITY_SMARTS)
        result.immunogenicity_flags = imm_hits
        if any(h["severity"] == "warning" for h in imm_hits):
            result.overall_status = "flagged"
            result.notes.append("Immunogenicity structural alert(s) detected.")

    # 4. Aggregation alerts
    if check_aggregation:
        smiles_frag = _psmiles_to_smiles(canonical)
        agg_hits = _run_smarts(smiles_frag, _AGGREGATION_SMARTS)
        result.aggregation_flags = agg_hits
        if agg_hits and result.overall_status == "approved":
            result.notes.append("Aggregation-induction alert(s) detected despite approved status — verify with MD.")

    if not result.immunogenicity_flags and not result.aggregation_flags and result.overall_status == "no_match":
        result.overall_status = "no_match"  # no safety flags at least

    return result
