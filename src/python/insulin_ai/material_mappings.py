#!/usr/bin/env python3
"""
PSMILES validation, functional-group annotation, and name-structure consistency.

Validation uses RDKit or the Ramprasad ``psmiles`` package.  Functional-group
annotation (``annotate_functional_groups``) and name-consistency checking
(``check_name_structure_consistency``) use RDKit SMARTS.  PubChem monomer lookup
(``lookup_monomer_pubchem``) uses PUG REST + RDKit Tanimoto similarity.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

# In-process PubChem response cache (key = normalized monomer name). Avoids repeated
# HTTPS round-trips when the agent validates many candidates; keeps MCP calls under
# client timeouts.
_PUBCHEM_CACHE_MAX = 256
_pubchem_monomer_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def clear_pubchem_lookup_cache() -> None:
    """Clear the PubChem monomer cache (e.g. for tests)."""
    _pubchem_monomer_cache.clear()


def morgan_fingerprint_bit_vect(mol: Any, radius: int = 2, n_bits: int = 2048) -> Any:
    """Morgan fingerprint as an RDKit ``ExplicitBitVect``.

    Uses :func:`rdkit.Chem.rdFingerprintGenerator.GetMorganGenerator` when
    available so RDKit does not emit deprecation warnings for the legacy
    ``GetMorganFingerprintAsBitVect`` API.
    """
    try:
        from rdkit.Chem.rdFingerprintGenerator import GetMorganGenerator

        gen = GetMorganGenerator(radius=radius, fpSize=n_bits)
        return gen.GetFingerprint(mol)
    except (ImportError, AttributeError):
        from rdkit.Chem import AllChem

        return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)


def _pubchem_cache_get(key: str) -> Optional[Dict[str, Any]]:
    if key not in _pubchem_monomer_cache:
        return None
    _pubchem_monomer_cache.move_to_end(key)
    return _pubchem_monomer_cache[key]


def _pubchem_cache_set(key: str, value: Dict[str, Any]) -> None:
    _pubchem_monomer_cache[key] = value
    _pubchem_monomer_cache.move_to_end(key)
    while len(_pubchem_monomer_cache) > _PUBCHEM_CACHE_MAX:
        _pubchem_monomer_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# SMARTS patterns for polymer-relevant functional groups
# ---------------------------------------------------------------------------
# Sources: RDKit Functional_Group_Hierarchy.txt, custom polymer patterns.
# Each entry: (group_name, SMARTS_string).
_FG_SMARTS: List[Tuple[str, str]] = [
    ("carboxylic_acid", "C(=O)[O;H,-]"),
    ("ester", "[#6][CX3](=O)[OX2H0][#6]"),
    ("amide", "[NX3][CX3](=[OX1])[#6]"),
    ("amine", "[NX3;H2,H1,H0;!$(NC=O);!$(NS=O);!$(N=*)]"),
    ("hydroxyl", "[OX2H;!$([OX2H]C=O)]"),
    ("aldehyde", "[CX3H1](=O)"),
    ("ketone", "[#6][CX3](=O)[#6]"),
    ("ether", "[OD2;!$(OC=O)]([#6])[#6]"),
    ("thioether", "[#16X2]([#6])[#6]"),
    ("aromatic", "a"),
    ("fluorinated", "[CX4][F]"),
    ("sulfonate", "[SX4](=O)(=O)[O;H,-]"),
    ("carbonate", "[OX2][CX3](=O)[OX2]"),
    ("urea", "[NX3][CX3](=O)[NX3]"),
    ("imide", "[CX3](=O)[NX3][CX3](=O)"),
]


def _cap_psmiles(psmiles: str, cap: str = "[CH3]") -> str:
    """Replace ``[*]`` connection points for RDKit parsing.

    Default cap is ``[CH3]`` (methyl), which preserves in-chain bonding
    context better than ``[H]``: e.g. ``[*]OCC[*]`` (PEG) capped with methyl
    gives dimethyl ether (ether detected), while ``[H]`` gives ethylene glycol
    (only hydroxyl detected, no ether).
    """
    return psmiles.strip().replace("[*]", cap)


def _psmiles_to_mol(psmiles: str):
    """Return an RDKit ``Mol`` from a capped PSMILES, or ``None``."""
    from rdkit import Chem

    return Chem.MolFromSmiles(_cap_psmiles(psmiles))


def annotate_functional_groups(psmiles: str) -> Dict[str, Any]:
    """
    Identify functional groups in a PSMILES repeat unit via SMARTS matching.

    Caps ``[*]`` with ``[CH3]`` (see ``_cap_psmiles``), then counts matches for each group in
    ``_FG_SMARTS``.  Returns ``{"ok": True, "groups": {"carboxylic_acid": 2, ...}}``
    or ``{"ok": False, "error": "..."}`` if RDKit is unavailable or the SMILES
    is invalid.
    """
    try:
        from rdkit import Chem
    except ImportError:
        return {"ok": False, "error": "rdkit required for functional-group annotation"}

    mol = _psmiles_to_mol(psmiles)
    if mol is None:
        return {"ok": False, "error": f"Invalid SMILES after capping: {_cap_psmiles(psmiles)}"}

    groups: Dict[str, int] = {}
    for name, smarts in _FG_SMARTS:
        pat = Chem.MolFromSmarts(smarts)
        if pat is None:
            continue
        count = len(mol.GetSubstructMatches(pat))
        groups[name] = count
    return {"ok": True, "groups": groups}


def validate_psmiles(psmiles: str) -> dict:
    """
    Validate PSMILES. Returns {valid: bool, canonical?: str, error?: str}.
    Uses psmiles.canonicalize when available, else RDKit.
    """
    if not psmiles or not isinstance(psmiles, str):
        return {"valid": False, "error": "Empty or invalid input"}

    psm = psmiles.strip()
    if "[*]" not in psm:
        return {"valid": False, "error": "PSMILES must contain [*] connection points"}

    # Try Ramprasad psmiles package (canonicalize)
    try:
        from psmiles import PolymerSmiles

        ps = PolymerSmiles(psm)
        # psmiles stable API: canonicalize is a property, not a method.
        c = ps.canonicalize
        if callable(c):
            c = c()
        canonical = str(c)
        return {"valid": True, "canonical": canonical}
    except ImportError:
        pass
    except Exception as e:
        return {"valid": False, "error": str(e)}

    # Fallback: RDKit validation (cap [*] to [H])
    try:
        from rdkit import Chem
        capped = psm.replace("[*]", "[H]")
        mol = Chem.MolFromSmiles(capped)
        if mol is None:
            return {"valid": False, "error": "Invalid SMILES structure"}
        return {"valid": True, "canonical": psm}
    except ImportError:
        return {"valid": False, "error": "rdkit or psmiles required for validation"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Layer 2: Name-structure consistency
# ---------------------------------------------------------------------------
# Maps keyword patterns in the material name to expected functional groups.
# Each rule: (compiled regex, list of acceptable groups (OR), min count for at
# least one of them).  "acid" accepts carboxylic_acid OR ester because a
# poly(diacid) is typically a polyester.
_NameFGRule = Tuple[re.Pattern, List[str], int]
_NAME_FG_RULES: List[_NameFGRule] = [
    (re.compile(r"(?:carboxyl|dicarboxyl|\bacid\b)", re.I), ["carboxylic_acid", "ester"], 1),
    (re.compile(r"(?:\bester\b|lactone|lactide|\bPLA\b|\bPLGA\b|\bPCL\b)", re.I), ["ester"], 1),
    (re.compile(r"(?:\bamine\b|\bamino\b|chitosan|glucosamine)", re.I), ["amine"], 1),
    (re.compile(r"(?:\bamide\b|nylon|peptide|polyamide)", re.I), ["amide"], 1),
    (re.compile(r"(?:\bether\b|glycol|\bPEG\b|\bPEO\b|\bPPO\b)", re.I), ["ether"], 1),
    (re.compile(r"(?:\balcohol\b|hydroxyl|\bPVA\b|vinyl\s*alcohol)", re.I), ["hydroxyl"], 1),
    (re.compile(r"(?:aromatic|styrene|phenyl|polystyrene)", re.I), ["aromatic"], 1),
    (re.compile(r"(?:fluorin|PTFE|PVDF|\bPVF\b)", re.I), ["fluorinated"], 1),
    (re.compile(r"(?:carbonate|\bPC\b)", re.I), ["carbonate"], 1),
    (re.compile(r"(?:sulfon)", re.I), ["sulfonate"], 1),
    (re.compile(r"(?:\burea\b|polyurea)", re.I), ["urea"], 1),
    (re.compile(r"(?:\bimide\b|polyimide)", re.I), ["imide"], 1),
    (re.compile(r"(?:thioether|polysulfide)", re.I), ["thioether"], 1),
]


def check_name_structure_consistency(
    material_name: str,
    psmiles: str,
) -> Dict[str, Any]:
    """
    Check whether a material name's implied chemistry matches the PSMILES structure.

    Extracts keywords from the name (e.g. "acid", "ester", "amine"), determines
    which functional groups should be present, then compares against the actual
    SMARTS-detected groups from ``annotate_functional_groups``.

    Returns::

        {
            "consistent": bool,
            "expected": {"carboxylic_acid": ">=1", ...},
            "found": {"carboxylic_acid": 0, "aldehyde": 2, ...},
            "missing": ["carboxylic_acid"],
            "warnings": ["Name implies 'acid' but ..."],
        }

    If no keyword rules fire (e.g. generic trade names), ``consistent`` is True
    with an empty ``expected`` dict and a note that no rules applied.
    """
    name = (material_name or "").strip()
    if not name:
        return {
            "consistent": True,
            "expected": {},
            "found": {},
            "missing": [],
            "warnings": ["No material_name provided; skipping consistency check."],
        }

    fg = annotate_functional_groups(psmiles)
    if not fg.get("ok"):
        return {
            "consistent": False,
            "expected": {},
            "found": {},
            "missing": [],
            "warnings": [f"Functional-group annotation failed: {fg.get('error')}"],
        }

    groups = fg["groups"]
    expected: Dict[str, str] = {}
    missing: List[str] = []
    warnings: List[str] = []

    for pattern, fg_names, min_count in _NAME_FG_RULES:
        if not pattern.search(name):
            continue
        label = " or ".join(fg_names)
        expected[label] = f">={min_count}"
        satisfied = any(groups.get(fg, 0) >= min_count for fg in fg_names)
        if not satisfied:
            missing.append(label)
            present = [
                f"{k}={v}" for k, v in groups.items() if v > 0
            ]
            present_str = ", ".join(present) if present else "none detected"
            warnings.append(
                f"Name implies '{label}' (>={min_count}) but none found; "
                f"structure has: {present_str}"
            )

    if not expected:
        return {
            "consistent": True,
            "expected": {},
            "found": {k: v for k, v in groups.items() if v > 0},
            "missing": [],
            "warnings": [
                "No keyword rules matched the material name; "
                "consistency not checked (consider PubChem lookup)."
            ],
        }

    return {
        "consistent": len(missing) == 0,
        "expected": expected,
        "found": {k: v for k, v in groups.items() if v > 0},
        "missing": missing,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Layer 3: PubChem monomer lookup + Tanimoto similarity
# ---------------------------------------------------------------------------
_POLY_PREFIX_RE = re.compile(
    r"^poly\s*\(\s*", re.I
)
_POLY_SUFFIX_RE = re.compile(r"\)\s*$")
_POLY_BARE_RE = re.compile(r"^poly(?=[a-z])", re.I)


def _strip_poly_prefix(name: str) -> str:
    """Extract probable monomer name from a polymer name.

    ``"poly(glutaric acid)"`` -> ``"glutaric acid"``
    ``"polyethylene glycol"`` -> ``"ethylene glycol"``
    ``"polylactic acid"`` -> ``"lactic acid"``
    """
    s = name.strip()
    s = _POLY_PREFIX_RE.sub("", s)
    s = _POLY_SUFFIX_RE.sub("", s)
    s = _POLY_BARE_RE.sub("", s)
    return s.strip()


def _apply_pubchem_similarity(out: Dict[str, Any], psmiles: Optional[str]) -> None:
    """Add Tanimoto similarity and optional warning to a PubChem result dict."""
    if not psmiles or not out.get("ok"):
        return
    ref_smiles = out.get("pubchem_smiles") or ""
    if not ref_smiles:
        return
    sim = _tanimoto_similarity(ref_smiles, _cap_psmiles(psmiles, cap="[H]"))
    out["similarity"] = sim
    if sim is not None and sim < 0.4:
        out["warning"] = (
            f"Low Tanimoto similarity ({sim:.2f}) between PubChem reference "
            f"and capped PSMILES; the PSMILES may not represent this material."
        )


def lookup_monomer_pubchem(
    material_name: str,
    psmiles: Optional[str] = None,
    *,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """
    Look up the monomer of a polymer on PubChem and optionally compare to a PSMILES.

    Strips common "poly" prefixes to derive the monomer name, queries PubChem
    PUG REST for canonical SMILES, and (when ``psmiles`` is given) computes
    Tanimoto similarity between the PubChem reference and the capped repeat unit
    using RDKit Morgan fingerprints.

    Responses are **cached in-process** by monomer name (LRU, max 256) so repeated
    validations in one MCP session do not block on the network.

    Returns ``{"ok": True, "monomer_name": ..., "pubchem_smiles": ..., ...}``
    or ``{"ok": False, "error": "..."}``.  No new pip dependency (uses
    ``requests`` + ``rdkit``).
    """
    import requests as _req

    name = (material_name or "").strip()
    if not name:
        return {"ok": False, "error": "Empty material_name"}

    monomer = _strip_poly_prefix(name)
    if not monomer:
        monomer = name

    cache_key = monomer.lower()
    cached = _pubchem_cache_get(cache_key)
    if cached is not None:
        out = dict(cached)
        _apply_pubchem_similarity(out, psmiles)
        return out

    url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{_req.utils.quote(monomer, safe='')}"
        f"/property/CanonicalSMILES,IUPACName,MolecularFormula/JSON"
    )
    read_t = float(timeout)
    connect_t = min(3.0, read_t * 0.6)
    try:
        resp = _req.get(url, timeout=(connect_t, read_t))
    except Exception as e:
        return {"ok": False, "error": f"PubChem request failed: {e}", "monomer_name": monomer}

    if resp.status_code == 404:
        err: Dict[str, Any] = {
            "ok": False,
            "error": f"PubChem has no compound named '{monomer}'",
            "monomer_name": monomer,
        }
        _pubchem_cache_set(cache_key, dict(err))
        return err
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"PubChem returned HTTP {resp.status_code}",
            "monomer_name": monomer,
        }

    try:
        props = resp.json()["PropertyTable"]["Properties"][0]
    except (KeyError, IndexError, ValueError) as e:
        return {"ok": False, "error": f"Unexpected PubChem response: {e}", "monomer_name": monomer}

    ref_smiles = props.get("CanonicalSMILES") or props.get("ConnectivitySMILES") or ""
    result: Dict[str, Any] = {
        "ok": True,
        "monomer_name": monomer,
        "pubchem_smiles": ref_smiles,
        "pubchem_iupac": props.get("IUPACName", ""),
        "pubchem_cid": props.get("CID"),
    }
    to_cache = {k: v for k, v in result.items() if k != "similarity" and k != "warning"}
    _pubchem_cache_set(cache_key, to_cache)

    _apply_pubchem_similarity(result, psmiles)
    return result


def _tanimoto_similarity(smiles_a: str, smiles_b: str) -> Optional[float]:
    """Morgan-fingerprint Tanimoto between two SMILES. Returns None on failure."""
    try:
        from rdkit import Chem, DataStructs

        ma = Chem.MolFromSmiles(smiles_a)
        mb = Chem.MolFromSmiles(smiles_b)
        if ma is None or mb is None:
            return None
        fpa = morgan_fingerprint_bit_vect(ma, radius=2, n_bits=2048)
        fpb = morgan_fingerprint_bit_vect(mb, radius=2, n_bits=2048)
        return round(float(DataStructs.TanimotoSimilarity(fpa, fpb)), 4)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Layer 4: Name → PSMILES generation (PubChem SMILES → polymer repeat unit)
# ---------------------------------------------------------------------------

# Curated lookup: normalised polymer name → known-good PSMILES.
# Highest confidence; bypasses automated conversion entirely.
_KNOWN_POLYMER_PSMILES: Dict[str, str] = {
    # --- polyethers ---
    "polyethylene glycol": "[*]OCC[*]",
    "poly(ethylene glycol)": "[*]OCC[*]",
    "poly(ethylene oxide)": "[*]OCC[*]",
    "peg": "[*]OCC[*]",
    "peo": "[*]OCC[*]",
    "poly(propylene oxide)": "[*]OCC(C)[*]",
    "ppo": "[*]OCC(C)[*]",
    "polyoxymethylene": "[*]CO[*]",
    "pom": "[*]CO[*]",
    # --- polyesters ---
    "polylactic acid": "[*]OC(=O)C(C)[*]",
    "poly(lactic acid)": "[*]OC(=O)C(C)[*]",
    "pla": "[*]OC(=O)C(C)[*]",
    "polyglycolic acid": "[*]OC(=O)C[*]",
    "poly(glycolic acid)": "[*]OC(=O)C[*]",
    "pga": "[*]OC(=O)C[*]",
    "poly(lactic-co-glycolic acid)": "[*]OC(=O)COC(=O)C(C)[*]",
    "plga": "[*]OC(=O)COC(=O)C(C)[*]",
    "polycaprolactone": "[*]OC(=O)CCCCC[*]",
    "poly(caprolactone)": "[*]OC(=O)CCCCC[*]",
    "pcl": "[*]OC(=O)CCCCC[*]",
    "poly(3-hydroxybutyrate)": "[*]OC(=O)CC(C)[*]",
    "phb": "[*]OC(=O)CC(C)[*]",
    "poly(butylene succinate)": "[*]OC(=O)CCC(=O)OCCCC[*]",
    "pbs": "[*]OC(=O)CCC(=O)OCCCC[*]",
    "poly(ethylene terephthalate)": "[*]OC(=O)c1ccc(cc1)C(=O)OCC[*]",
    "pet": "[*]OC(=O)c1ccc(cc1)C(=O)OCC[*]",
    # --- vinyl / addition ---
    "polyethylene": "[*]CC[*]",
    "pe": "[*]CC[*]",
    "polypropylene": "[*]CC(C)[*]",
    "pp": "[*]CC(C)[*]",
    "polystyrene": "[*]CC([*])c1ccccc1",
    "ps": "[*]CC([*])c1ccccc1",
    "poly(vinyl alcohol)": "[*]CC([*])O",
    "pva": "[*]CC([*])O",
    "poly(vinyl chloride)": "[*]CC([*])Cl",
    "pvc": "[*]CC([*])Cl",
    "poly(vinyl acetate)": "[*]CC([*])OC(=O)C",
    "pvac": "[*]CC([*])OC(=O)C",
    "poly(methyl methacrylate)": "[*]CC([*])(C)C(=O)OC",
    "pmma": "[*]CC([*])(C)C(=O)OC",
    "poly(acrylic acid)": "[*]CC([*])C(=O)O",
    "paa": "[*]CC([*])C(=O)O",
    "poly(methacrylic acid)": "[*]CC([*])(C)C(=O)O",
    "polyacrylonitrile": "[*]CC([*])C#N",
    "pan": "[*]CC([*])C#N",
    "poly(n-isopropylacrylamide)": "[*]CC([*])C(=O)NC(C)C",
    "pnipam": "[*]CC([*])C(=O)NC(C)C",
    "poly(2-hydroxyethyl methacrylate)": "[*]CC([*])(C)C(=O)OCCO",
    "phema": "[*]CC([*])(C)C(=O)OCCO",
    "polytetrafluoroethylene": "[*]C(F)(F)C(F)(F)[*]",
    "ptfe": "[*]C(F)(F)C(F)(F)[*]",
    "poly(vinylidene fluoride)": "[*]CC([*])(F)F",
    "pvdf": "[*]CC([*])(F)F",
    # --- polyamides / polypeptides ---
    "nylon 6": "[*]NC(=O)CCCCC[*]",
    "polycaprolactam": "[*]NC(=O)CCCCC[*]",
    "nylon 6,6": "[*]NC(=O)CCCCC(=O)NCCCCCC[*]",
    # --- polycarbonates ---
    "polycarbonate": "[*]OC(=O)Oc1ccc(cc1)C(C)(C)c1ccc(cc1)[*]",
    "pc": "[*]OC(=O)Oc1ccc(cc1)C(C)(C)c1ccc(cc1)[*]",
    "poly(trimethylene carbonate)": "[*]OC(=O)OCCC[*]",
    "ptmc": "[*]OC(=O)OCCC[*]",
    # --- polyurethane building blocks ---
    "poly(ethylene carbonate)": "[*]OC(=O)OCC[*]",
    # --- silicones ---
    "polydimethylsiloxane": "[*]O[Si](C)(C)[*]",
    "pdms": "[*]O[Si](C)(C)[*]",
    # --- other ---
    "polyvinylpyrrolidone": "[*]CC([*])N1CCCC1=O",
    "pvp": "[*]CC([*])N1CCCC1=O",
    "chitosan": "[*]OC1C(N)C(O)C(CO)OC1[*]",
}


def _try_known_polymer_lookup(name: str) -> Optional[str]:
    """Check curated table.  Returns PSMILES or None."""
    key = name.strip().lower()
    return _KNOWN_POLYMER_PSMILES.get(key)


def _vinyl_smiles_to_psmiles(smiles: str) -> Optional[str]:
    """
    Convert a vinyl monomer SMILES (contains non-ring C=C) to a PSMILES repeat unit.

    Opens the first exocyclic C=C double bond and places ``[*]`` at each end.
    E.g. styrene ``C=Cc1ccccc1`` → ``[*]CC([*])c1ccccc1``.
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    pattern = Chem.MolFromSmarts("[CX3:1]=[CX3:2]")
    matches = mol.GetSubstructMatches(pattern)
    if not matches:
        return None

    target = None
    for m in matches:
        bond = mol.GetBondBetweenAtoms(m[0], m[1])
        if bond and not bond.IsInRing():
            target = m
            break
    if target is None:
        return None

    idx1, idx2 = target
    rwmol = Chem.RWMol(mol)
    bond = rwmol.GetBondBetweenAtoms(idx1, idx2)
    bond.SetBondType(Chem.BondType.SINGLE)
    d1 = rwmol.AddAtom(Chem.Atom(0))
    d2 = rwmol.AddAtom(Chem.Atom(0))
    rwmol.AddBond(idx1, d1, Chem.BondType.SINGLE)
    rwmol.AddBond(idx2, d2, Chem.BondType.SINGLE)
    try:
        Chem.SanitizeMol(rwmol)
        smi = Chem.MolToSmiles(rwmol)
        return smi.replace("*", "[*]") if "[*]" not in smi else smi
    except Exception:
        return None


def _hydroxy_acid_smiles_to_psmiles(smiles: str) -> Optional[str]:
    """
    Convert a hydroxy-acid monomer to a polyester PSMILES repeat unit.

    Detects molecules with **both** a non-acid hydroxyl (-OH) **and** a
    carboxylic acid (-COOH).  The repeat unit is the ester linkage
    ``[*]-O-<backbone>-C(=O)-[*]``.
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    acid_pat = Chem.MolFromSmarts("[CX3:1](=[OX1])[OX2H1:2]")
    alc_pat = Chem.MolFromSmarts("[CX4,c:1][OX2H1:2]")

    acid_matches = mol.GetSubstructMatches(acid_pat)
    alc_matches = mol.GetSubstructMatches(alc_pat)
    if not acid_matches or not alc_matches:
        return None

    acid_oh_indices = {m[2] for m in acid_matches}
    alc_matches = [m for m in alc_matches if m[1] not in acid_oh_indices]
    if not alc_matches:
        return None

    acid_c_idx = acid_matches[0][0]
    acid_oh_idx = acid_matches[0][2]
    alc_o_idx = alc_matches[0][1]

    mol_h = Chem.AddHs(mol)
    rwmol = Chem.RWMol(mol_h)

    alc_h_idx = None
    for nb in rwmol.GetAtomWithIdx(alc_o_idx).GetNeighbors():
        if nb.GetAtomicNum() == 1:
            alc_h_idx = nb.GetIdx()
            break
    acid_h_idx = None
    for nb in rwmol.GetAtomWithIdx(acid_oh_idx).GetNeighbors():
        if nb.GetAtomicNum() == 1:
            acid_h_idx = nb.GetIdx()
            break

    if alc_h_idx is None or acid_h_idx is None:
        return None

    d_alc = rwmol.AddAtom(Chem.Atom(0))
    rwmol.AddBond(alc_o_idx, d_alc, Chem.BondType.SINGLE)
    rwmol.RemoveAtom(alc_h_idx)

    if acid_h_idx > alc_h_idx:
        acid_h_idx -= 1
    if acid_oh_idx > alc_h_idx:
        acid_oh_idx -= 1
    if acid_c_idx > alc_h_idx:
        acid_c_idx -= 1

    ah_idx = None
    for nb in rwmol.GetAtomWithIdx(acid_oh_idx).GetNeighbors():
        if nb.GetAtomicNum() == 1:
            ah_idx = nb.GetIdx()
            break
    if ah_idx is not None:
        rwmol.RemoveAtom(ah_idx)
        if acid_oh_idx > ah_idx:
            acid_oh_idx -= 1
        if acid_c_idx > ah_idx:
            acid_c_idx -= 1

    rwmol.RemoveAtom(acid_oh_idx)
    if acid_c_idx > acid_oh_idx:
        acid_c_idx -= 1

    d_acid = rwmol.AddAtom(Chem.Atom(0))
    rwmol.AddBond(acid_c_idx, d_acid, Chem.BondType.SINGLE)

    try:
        Chem.SanitizeMol(rwmol)
        smi = Chem.MolToSmiles(Chem.RemoveHs(rwmol))
        return smi.replace("*", "[*]") if "[*]" not in smi else smi
    except Exception:
        return None


def _amino_acid_smiles_to_psmiles(smiles: str) -> Optional[str]:
    """
    Convert an amino-acid monomer to a polyamide PSMILES repeat unit.

    ``H2N-R-COOH`` → ``[*]N-R-C(=O)[*]``  (water eliminated).
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    acid_pat = Chem.MolFromSmarts("[CX3:1](=[OX1])[OX2H1:2]")
    amine_pat = Chem.MolFromSmarts("[NX3H2:1]")

    if not mol.HasSubstructMatch(acid_pat) or not mol.HasSubstructMatch(amine_pat):
        return None

    acid_match = mol.GetSubstructMatch(acid_pat)
    amine_match = mol.GetSubstructMatch(amine_pat)

    acid_c_idx = acid_match[0]
    acid_oh_idx = acid_match[2]
    amine_n_idx = amine_match[0]

    mol_h = Chem.AddHs(mol)
    rwmol = Chem.RWMol(mol_h)

    amine_h_idx = None
    for nb in rwmol.GetAtomWithIdx(amine_n_idx).GetNeighbors():
        if nb.GetAtomicNum() == 1:
            amine_h_idx = nb.GetIdx()
            break

    acid_h_idx = None
    for nb in rwmol.GetAtomWithIdx(acid_oh_idx).GetNeighbors():
        if nb.GetAtomicNum() == 1:
            acid_h_idx = nb.GetIdx()
            break

    if amine_h_idx is None or acid_h_idx is None:
        return None

    d_amine = rwmol.AddAtom(Chem.Atom(0))
    rwmol.AddBond(amine_n_idx, d_amine, Chem.BondType.SINGLE)
    to_remove = sorted([amine_h_idx, acid_h_idx, acid_oh_idx], reverse=True)
    for idx in to_remove:
        rwmol.RemoveAtom(idx)

    new_acid_c = acid_c_idx
    for idx in to_remove:
        if acid_c_idx > idx:
            new_acid_c -= 1

    d_acid = rwmol.AddAtom(Chem.Atom(0))
    rwmol.AddBond(new_acid_c, d_acid, Chem.BondType.SINGLE)

    try:
        Chem.SanitizeMol(rwmol)
        smi = Chem.MolToSmiles(Chem.RemoveHs(rwmol))
        return smi.replace("*", "[*]") if "[*]" not in smi else smi
    except Exception:
        return None


def monomer_smiles_to_psmiles(
    smiles: str,
    mechanism: str = "auto",
) -> Dict[str, Any]:
    """
    Convert a monomer SMILES to a PSMILES polymer repeat unit.

    Args:
        smiles: Canonical SMILES of the monomer (e.g. from PubChem).
        mechanism: ``"auto"`` (try all heuristics), ``"vinyl"``, ``"condensation"``,
            or ``"amide"``.

    Returns dict with ``ok``, ``psmiles``, ``mechanism``, ``confidence``.
    """
    if not smiles:
        return {"ok": False, "error": "empty SMILES"}

    converters = []
    if mechanism in ("auto", "vinyl"):
        converters.append(("vinyl", _vinyl_smiles_to_psmiles))
    if mechanism in ("auto", "condensation"):
        converters.append(("condensation_ester", _hydroxy_acid_smiles_to_psmiles))
    if mechanism in ("auto", "amide"):
        converters.append(("condensation_amide", _amino_acid_smiles_to_psmiles))

    for mech_name, fn in converters:
        result = fn(smiles)
        if result and "[*]" in result and result.count("[*]") == 2:
            pre = prescreen_psmiles_for_md(result)
            return {
                "ok": True,
                "psmiles": result,
                "mechanism": mech_name,
                "confidence": "medium",
                "monomer_smiles": smiles,
                "md_compatible": pre.get("ok", False),
            }

    return {
        "ok": False,
        "error": (
            f"Could not determine polymerization sites for SMILES: {smiles}. "
            "No vinyl (C=C), hydroxy-acid, or amino-acid pattern detected."
        ),
        "monomer_smiles": smiles,
    }


def name_to_psmiles(material_name: str) -> Dict[str, Any]:
    """
    Full pipeline: polymer name → PSMILES repeat unit.

    Resolution order:

    1. **Known polymer lookup** (curated table, high confidence).
    2. **PubChem** monomer SMILES → automated conversion (vinyl / condensation /
       amide detection).

    Returns ``{"ok": True, "psmiles": ..., "source": ..., ...}`` or
    ``{"ok": False, "error": ...}``.
    """
    name = (material_name or "").strip()
    if not name:
        return {"ok": False, "error": "Empty material_name"}

    known = _try_known_polymer_lookup(name)
    if known:
        pre = prescreen_psmiles_for_md(known)
        return {
            "ok": True,
            "psmiles": known,
            "source": "known_polymer_table",
            "confidence": "high",
            "material_name": name,
            "md_compatible": pre.get("ok", False),
        }

    pub = lookup_monomer_pubchem(name)
    if not pub.get("ok"):
        return {
            "ok": False,
            "error": f"PubChem lookup failed: {pub.get('error')}",
            "material_name": name,
        }

    monomer_smiles = pub.get("pubchem_smiles", "")
    if not monomer_smiles:
        return {
            "ok": False,
            "error": "PubChem returned empty SMILES",
            "material_name": name,
            "pubchem": pub,
        }

    conv = monomer_smiles_to_psmiles(monomer_smiles)
    if conv.get("ok"):
        conv["source"] = "pubchem_auto"
        conv["material_name"] = name
        conv["monomer_name"] = pub.get("monomer_name")
        conv["pubchem_smiles"] = monomer_smiles
        conv["pubchem_cid"] = pub.get("pubchem_cid")
        return conv

    return {
        "ok": False,
        "error": conv.get("error", "Conversion failed"),
        "material_name": name,
        "monomer_name": pub.get("monomer_name"),
        "pubchem_smiles": monomer_smiles,
        "hint": (
            "PubChem SMILES retrieved but automatic polymerization-site detection failed. "
            "Manually identify the repeat unit and add [*] at the two backbone connection points."
        ),
    }


def clean_psmiles(psmiles: str) -> str | None:
    """
    Clean/repair PSMILES if possible. Returns canonical form or None.
    """
    r = validate_psmiles(psmiles)
    if r.get("valid"):
        return r.get("canonical", psmiles)
    return None


def prescreen_psmiles_for_md(psmiles: str) -> Dict[str, Any]:
    """
    Lightweight check that a PSMILES is safe for the OpenMM/GAFF evaluation pipeline.

    Catches problems that would otherwise crash deep inside RDKit embed, OpenFF
    ``Molecule.from_rdkit``, or GAFF template generation.  Runs **before**
    ``MDSimulator.evaluate_candidates`` so bad candidates are rejected early with
    a structured error instead of aborting the whole batch.

    Returns ``{"ok": True}`` or ``{"ok": False, "error": "...", "stage": "prescreen"}``.
    """
    try:
        from rdkit import Chem
    except ImportError:
        return {"ok": True}

    psm = (psmiles or "").strip()
    if not psm or "[*]" not in psm:
        return {"ok": False, "error": "Missing [*] connection points", "stage": "prescreen"}

    star_count = psm.count("[*]")
    if star_count != 2:
        return {
            "ok": False,
            "error": f"Expected exactly 2 [*] connection points, found {star_count}",
            "stage": "prescreen",
        }

    capped = psm.replace("[*]", "[H]")
    mol = Chem.MolFromSmiles(capped, sanitize=False)
    if mol is None:
        return {"ok": False, "error": f"RDKit cannot parse H-capped SMILES: {capped}", "stage": "prescreen"}

    try:
        Chem.SanitizeMol(mol)
    except Exception as e:
        return {"ok": False, "error": f"RDKit sanitize failed: {e}", "stage": "prescreen"}

    n_radicals = sum(1 for a in mol.GetAtoms() if a.GetNumRadicalElectrons() > 0)
    if n_radicals > 0:
        return {"ok": False, "error": f"H-capped form has {n_radicals} radical electron(s); OpenFF will reject", "stage": "prescreen"}

    charged = [(a.GetSymbol(), a.GetFormalCharge()) for a in mol.GetAtoms() if a.GetFormalCharge() != 0]
    if charged:
        symbols = ", ".join(f"{s}({c:+d})" for s, c in charged[:5])
        return {"ok": False, "error": f"Formally charged atoms ({symbols}); GAFF/Gasteiger may fail", "stage": "prescreen"}

    n_heavy = mol.GetNumHeavyAtoms()
    if n_heavy > 200:
        return {"ok": False, "error": f"Repeat unit has {n_heavy} heavy atoms (>200); likely invalid", "stage": "prescreen"}
    if n_heavy < 1:
        return {"ok": False, "error": "Repeat unit has no heavy atoms", "stage": "prescreen"}

    return {"ok": True}
