"""ToxicityService: SMARTS screening + ADMET-AI for residual monomer risk.

ADMET predictions are on small-molecule SMILES (monomers or realistic
residual fragments), never on full polymer graphs. All ADMET models are
trained on drug-like small molecules — predictions for monomers are
informative but not a substitute for regulatory studies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TOXIC_SMARTS = {
    "acrylamide": "[CH2]=[CH][C](=O)[NH2]",
    "epoxide": "[OX2r3]1[CX4r3][CX4r3]1",
    "aldehyde": "[CX3H1](=O)[#6]",
    "michael_acceptor": "[CX3]=[CX3][CX3](=O)",
    "aziridine": "[NX3r3]1[CX4r3][CX4r3]1",
    "isocyanate": "[NX2]=[CX2]=[OX1]",
    "acid_halide": "[CX3](=O)[F,Cl,Br,I]",
    "nitro_aromatic": "[$(c1ccccc1[N+](=O)[O-])]",
    "sulfonyl_halide": "[SX4](=O)(=O)[F,Cl,Br,I]",
    "peroxide": "[OX2][OX2]",
    "hydrazine": "[NX3][NX3]",
    "vinyl_halide": "[CX3]=[CX3][F,Cl,Br,I]",
}

ADMET_THRESHOLDS = {
    "hERG": {"key": "hERG", "threshold": 0.5, "direction": "above_is_bad"},
    "hepatotoxicity": {"key": "HepTox", "threshold": 0.5, "direction": "above_is_bad"},
    "AMES": {"key": "AMES", "threshold": 0.5, "direction": "above_is_bad"},
    "LD50_Zhu": {"key": "LD50_Zhu", "threshold": 500, "direction": "lower_is_bad"},
}


class SMARTSHit(BaseModel):
    pattern_name: str
    smarts: str
    smiles: str


class ADMETProfile(BaseModel):
    smiles: str
    predictions: Dict[str, float] = Field(default_factory=dict)
    flags: List[str] = Field(default_factory=list)
    available: bool = True


class ToxicityResult(BaseModel):
    smiles: str
    smarts_hits: List[SMARTSHit] = Field(default_factory=list)
    admet: Optional[ADMETProfile] = None
    safe: bool = True
    warnings: List[str] = Field(default_factory=list)


def _is_admet_available() -> bool:
    try:
        from admet_ai import ADMETModel  # noqa: F401
        return True
    except ImportError:
        return False


def _run_smarts_screen(smiles: str) -> List[SMARTSHit]:
    """Screen a SMILES against the curated SMARTS toxicity library."""
    hits: List[SMARTSHit] = []
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return hits
        for name, smarts in TOXIC_SMARTS.items():
            pattern = Chem.MolFromSmarts(smarts)
            if pattern and mol.HasSubstructMatch(pattern):
                hits.append(SMARTSHit(pattern_name=name, smarts=smarts, smiles=smiles))
    except ImportError:
        logger.debug("RDKit not available; skipping SMARTS screening")
    return hits


def _run_admet(smiles: str) -> Optional[ADMETProfile]:
    """Run ADMET-AI predictions on a single SMILES."""
    if not _is_admet_available():
        return ADMETProfile(smiles=smiles, available=False)

    try:
        from admet_ai import ADMETModel

        model = ADMETModel()
        preds = model.predict(smiles=smiles)

        flags: List[str] = []
        pred_dict: Dict[str, float] = {}

        if isinstance(preds, dict):
            pred_dict = {k: float(v) for k, v in preds.items() if isinstance(v, (int, float))}
        else:
            pred_dict = {}

        for label, cfg in ADMET_THRESHOLDS.items():
            val = pred_dict.get(cfg["key"])
            if val is not None and cfg["threshold"] is not None:
                if cfg["direction"] == "above_is_bad" and val > cfg["threshold"]:
                    flags.append(f"{label}={val:.3f} (threshold {cfg['threshold']})")
                elif cfg["direction"] == "lower_is_bad" and val < cfg["threshold"]:
                    flags.append(f"{label}={val:.3f} (threshold {cfg['threshold']})")

        return ADMETProfile(smiles=smiles, predictions=pred_dict, flags=flags)

    except Exception as exc:
        logger.error("ADMET-AI failed for %s: %s", smiles, exc)
        return ADMETProfile(smiles=smiles, available=False)


def screen_monomer(smiles: str) -> ToxicityResult:
    """Full toxicity screen on a single monomer SMILES: SMARTS + ADMET."""
    smarts_hits = _run_smarts_screen(smiles)
    admet = _run_admet(smiles)

    warnings: List[str] = []
    safe = True

    if smarts_hits:
        safe = False
        for hit in smarts_hits:
            warnings.append(f"SMARTS alert: {hit.pattern_name}")

    if admet and admet.flags:
        safe = False
        warnings.extend(admet.flags)

    return ToxicityResult(
        smiles=smiles,
        smarts_hits=smarts_hits,
        admet=admet,
        safe=safe,
        warnings=warnings,
    )


def screen_monomers_batch(smiles_list: List[str]) -> List[ToxicityResult]:
    """Screen multiple monomers."""
    return [screen_monomer(s) for s in smiles_list]
